from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

import tehm.evaluation.research_ledger as ledger_module
import tehm.evaluation.research_runtime as runtime_module
from tehm.evaluation.research_ledger import audit_attempt_ledger
from tehm.evaluation.research_runtime import (
    ResearchRuntimeError,
    run_research_campaign,
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, tool_exit: int = 0):
    source_root = tmp_path / "RTL"
    project = source_root / "catalog" / "demo"
    rtl = project / "rtl" / "top.v"
    rtl.parent.mkdir(parents=True)
    rtl.write_text("module top(input a, output y); assign y = a; endmodule\n")
    entries = [{"path": "rtl/top.v", "bytes": rtl.stat().st_size, "sha256": _sha(rtl)}]
    design = {
        "schema": "tehm-research-design-manifest-v1",
        "design_id": "demo",
        "project_path": "catalog/demo",
        "source_root": str(source_root),
        "source_files": entries,
        "source_bundle_digest": _digest(entries),
        "compilation": {
            "top_module": "top", "top_authority": "explicit_config",
            "ordered_filelist": ["rtl/top.v"],
            "filelist_authority": "explicit:filelist", "include_dirs": [],
            "defines": {}, "top_parameters": {},
        },
    }
    unsigned = dict(design)
    design["manifest_digest"] = _digest(unsigned)
    inventory = tmp_path / "inventory"
    _write(inventory / "design-manifests/demo.json", design)
    _write(inventory / "inventory.json", {
        "schema": "tehm-research-design-inventory-v1",
        "inventory_digest": "sha256:inventory",
        "designs": [{"design_id": "demo",
                     "manifest_path": "design-manifests/demo.json"}],
    })

    fake_yosys = tmp_path / "yosys"
    fake_yosys.write_text(
        f"#!{sys.executable}\n"
        "import json, re, sys\n"
        "from pathlib import Path\n"
        "script = Path(sys.argv[sys.argv.index('-s') + 1]).read_text()\n"
        + ("\nfor name in re.findall(r'write_json \\\"([^\\\"]+)\\\"', script):\n"
           "    Path(name).write_text(json.dumps({'modules': {'top': {}}}))\n"
           if tool_exit == 0 else "")
        + f"sys.exit({tool_exit})\n",
        encoding="utf-8",
    )
    fake_yosys.chmod(0o755)
    epoch = tmp_path / "epoch"
    _write(epoch / "bindings/toolchain-manifest.json", {
        "manifest_digest": "1" * 64,
        "tools": {"yosys": {"path": str(fake_yosys), "sha256": _sha(fake_yosys)}},
    })

    prepared = tmp_path / "prepared"
    campaign = {
        "campaign_id": "campaign-1",
        "prepared_campaign_digest": "sha256:prepared",
        "profile": "frontend_preflight",
        "task_ids": ["task-1"],
        "policies": ["no_persistent_memory"],
        "epoch": {"path": str(epoch)},
        "inventory": {"path": str(inventory)},
        "budget": {
            "candidate_limit": 1, "eda_call_limit": 1,
            "model_call_limit": 0, "model_token_limit": 0,
            "wallclock_limit_seconds": 30, "retry_limit": 0,
            "retryable_classes": [],
        },
    }
    context = {
        "task_id": "task-1", "case_id": "task-1", "design_id": "demo",
        "compile_input": {
            "source_root": str(source_root), "project_path": "catalog/demo",
            "top_module": "top", "ordered_filelist": ["rtl/top.v"],
            "include_dirs": [], "defines": {}, "top_parameters": {},
            "source_bundle_digest": design["source_bundle_digest"],
            "design_manifest_digest": design["manifest_digest"],
        },
        "target_scope": {"kind": "frontend_synthesis"},
        "contract": {
            "task_kind": "frontend_synthesis_preflight",
            "required_checks": ["source_integrity", "elaboration", "synthesis"],
            "optional_checks": [], "eligible_for_repair_metric": False,
        },
    }
    _write(prepared / "prepared-campaign.json", campaign)
    _write(prepared / "task-contexts/task-1.json", context)
    verified = {"valid": True, "campaign_id": "campaign-1",
                "prepared_campaign_digest": "sha256:prepared"}
    monkeypatch.setattr(runtime_module, "verify_prepared_campaign", lambda path: verified)
    monkeypatch.setattr(ledger_module, "verify_prepared_campaign", lambda path: verified)
    monkeypatch.setattr(runtime_module, "verify_research_inventory", lambda path: {
        "valid": True, "corpus_unchanged": True, "corpus_root": str(source_root),
    })
    monkeypatch.setattr(runtime_module, "_verify_runtime_source", lambda path: {
        "git_head": "test", "critical_paths": [],
    })
    return prepared, project


def test_runtime_executes_full_s0_and_independent_audit_passes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepared, project = _inputs(tmp_path, monkeypatch)
    source_before = _sha(project / "rtl/top.v")
    output = tmp_path / "run"
    result = run_research_campaign(prepared=prepared, output=output)
    assert result["all_registered_terminal"] is True
    assert result["actual_cost"]["eda_calls"] == 1
    assert result["memory_update"] == "none"
    assert _sha(project / "rtl/top.v") == source_before
    audited = audit_attempt_ledger(
        prepared=prepared, ledger=output / "attempt-ledger.jsonl",
        output=tmp_path / "audit",
    )
    assert audited["oracle_verdict_counts"] == {"PASS": 1}
    payload = json.loads((tmp_path / "audit/audited-attempts.json").read_text())
    assert payload["attempts"][0]["audit_recomputed_checks"] is True
    assert payload["attempts"][0]["check_disagreements"] == []


def test_runtime_preserves_frontend_failure_and_full_denominator(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepared, _ = _inputs(tmp_path, monkeypatch, tool_exit=1)
    output = tmp_path / "run"
    run_research_campaign(prepared=prepared, output=output)
    audited = audit_attempt_ledger(
        prepared=prepared, ledger=output / "attempt-ledger.jsonl",
        output=tmp_path / "audit",
    )
    assert audited["registered_case_count"] == 1
    assert audited["terminal_case_count"] == 1
    assert audited["oracle_verdict_counts"] == {"FAIL": 1}
    attempt = json.loads((tmp_path / "audit/audited-attempts.json").read_text())["attempts"][0]
    assert attempt["required_checks"] == {
        "source_integrity": "PASS", "elaboration": "FAIL", "synthesis": "UNKNOWN",
    }


def test_runtime_rejects_non_s0_profile(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepared, _ = _inputs(tmp_path, monkeypatch)
    campaign = json.loads((prepared / "prepared-campaign.json").read_text())
    campaign["profile"] = "controlled_action_pilot"
    _write(prepared / "prepared-campaign.json", campaign)
    with pytest.raises(ResearchRuntimeError, match="frontend_preflight"):
        run_research_campaign(prepared=prepared, output=tmp_path / "run")
