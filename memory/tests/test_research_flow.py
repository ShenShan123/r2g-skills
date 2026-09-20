from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

import tehm.evaluation.research_flow as research_flow
from tehm.evaluation.research_flow import (
    ResearchFlowError,
    audit_flow_run,
    stage_flow_project,
    verify_flow_audit,
    verify_staged_flow_project,
)
from tehm.evaluation.research_inventory import (
    bind_research_inventory_adapters,
    build_research_inventory,
)


def _adapted_inventory(tmp_path: Path) -> Path:
    authority = tmp_path / "orfs"
    project = authority / "flow/designs/src/gcd"
    project.mkdir(parents=True)
    (project / "gcd.v").write_text(
        "module gcd(input wire clk, output wire done); assign done = clk; endmodule\n",
        encoding="utf-8",
    )
    (project / "src_manifest.txt").write_text("gcd.v\n", encoding="utf-8")
    (project / "design_meta.json").write_text(json.dumps({
        "design": "gcd", "top": "gcd", "notes": "repo=orfs-test",
    }), encoding="utf-8")
    support = authority / "flow/designs/test/gcd"
    support.mkdir(parents=True)
    (support / "config.mk").write_text(
        "export DESIGN_NAME = wrong\n"
        "export PLATFORM = wrong\n"
        "export VERILOG_FILES = /old/source.v\n"
        "export SDC_FILE = /old/constraint.sdc\n"
        "export CORE_UTILIZATION = 50\n",
        encoding="utf-8",
    )
    (support / "constraint.sdc").write_text(
        "create_clock -name clk -period 10 [get_ports clk]\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q", str(authority)], check=True)
    subprocess.run(
        ["git", "-C", str(authority), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(authority), "config", "user.name", "Research Test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(authority), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(authority), "commit", "-q", "-m", "fixture"],
        check=True,
    )
    head = subprocess.run(
        ["git", "-C", str(authority), "rev-parse", "HEAD"], check=True,
        text=True, stdout=subprocess.PIPE,
    ).stdout.strip()
    source_inventory = tmp_path / "source-inventory"
    source_result = build_research_inventory(
        corpus_root=authority / "flow/designs/src", output=source_inventory
    )
    spec = tmp_path / "adapter.json"
    spec.write_text(json.dumps({
        "schema": "tehm-research-inventory-adapter-spec-v1",
        "adapter_id": "test-flow-adapter-v1",
        "source_inventory_digest": source_result["inventory_digest"],
        "authority_checkout": {
            "git_head": head,
            "require_clean": True,
            "source_subtree": "flow/designs/src",
        },
        "designs": [{
            "design_id": "gcd",
            "role": "official_control",
            "top_module": "gcd",
            "ordered_filelist": ["gcd.v"],
            "include_dirs": [],
            "defines": {},
            "top_parameters": {},
            "support_files": [
                "flow/designs/test/gcd/config.mk",
                "flow/designs/test/gcd/constraint.sdc",
            ],
            "flow_binding": {
                "platform": "test",
                "config_template": "flow/designs/test/gcd/config.mk",
                "sdc_template": "flow/designs/test/gcd/constraint.sdc",
                "overrides": {"CORE_UTILIZATION": "45"},
            },
        }],
    }), encoding="utf-8")
    adapted = tmp_path / "adapted-inventory"
    bind_research_inventory_adapters(
        inventory=source_inventory, adapter_spec=spec,
        authority_root=authority, output=adapted,
    )
    return adapted


def test_stage_flow_project_is_source_bound_and_replayable(tmp_path: Path) -> None:
    inventory = _adapted_inventory(tmp_path)
    project = tmp_path / "campaign/project-gcd"
    result = stage_flow_project(inventory=inventory, design_id="gcd", output=project)

    assert result["valid"] is True
    assert result["source_unchanged"] is True
    assert result["logic_changes"] == []
    assert result["stub_generated"] is False
    assert (project / "rtl/gcd.v").is_file()
    config = (project / "constraints/config.mk").read_text(encoding="utf-8")
    assert "export DESIGN_NAME = gcd" in config
    assert "export DESIGN_NICKNAME = gcd" in config
    assert "export PLATFORM = test" in config
    assert f"export VERILOG_FILES = {project}/rtl/gcd.v" in config
    assert f"export SDC_FILE = {project}/constraints/constraint.sdc" in config
    assert "export CORE_UTILIZATION = 45" in config
    assert "/old/source.v" not in config
    assert verify_staged_flow_project(project)["valid"] is True

    (project / "backend").mkdir()
    (project / "backend/output.log").write_text("later output\n", encoding="utf-8")
    assert verify_staged_flow_project(project)["valid"] is True


def test_stage_flow_project_detects_input_tamper(tmp_path: Path) -> None:
    inventory = _adapted_inventory(tmp_path)
    project = tmp_path / "campaign/project-gcd"
    stage_flow_project(inventory=inventory, design_id="gcd", output=project)
    (project / "rtl/gcd.v").write_text("module changed; endmodule\n", encoding="utf-8")
    with pytest.raises(ResearchFlowError, match="staged input drifted"):
        verify_staged_flow_project(project)


def test_stage_flow_project_refuses_overwrite(tmp_path: Path) -> None:
    inventory = _adapted_inventory(tmp_path)
    project = tmp_path / "campaign/project-gcd"
    stage_flow_project(inventory=inventory, design_id="gcd", output=project)
    with pytest.raises(ResearchFlowError, match="refusing to overwrite"):
        stage_flow_project(inventory=inventory, design_id="gcd", output=project)


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _terminal_congestion_run(project: Path) -> Path:
    run = project / "backend/RUN_test"
    final = run / "final"
    final.mkdir(parents=True)
    receipt = json.loads(
        (project / "stage-receipt.json").read_text(encoding="utf-8")
    )
    authority_head = receipt["authority_checkout"]["git_head"][:9]
    authority_root = receipt["authority_checkout"]["root"]
    fingerprint = (
        f"orfs={authority_root}@{authority_head} openroad=/tools/openroad "
        "yosys=/tools/yosys frontend=default"
    )
    stages = []
    artifact_rows = []
    for index, (name, artifact) in enumerate(
        (
            ("synth", "1_synth.odb"),
            ("floorplan", "2_floorplan.odb"),
            ("place", "3_place.odb"),
            ("cts", "4_cts.odb"),
        ),
        start=1,
    ):
        target = final / artifact
        target.write_bytes(f"{name}-artifact\n".encode())
        stages.append({
            "stage": name, "status": 0, "elapsed_s": index,
            "artifact": artifact,
        })
        artifact_rows.append({
            "schema_version": 1,
            "stage_contract_version": 2,
            "stage": name,
            "status": 0,
            "run_tag": "RUN_test",
            "platform": "test",
            "design": "gcd",
            "flow_variant": "fixed-v1",
            "toolchain": fingerprint,
            "artifact": artifact,
            "artifact_path": f"/work/{artifact}",
            "size": target.stat().st_size,
            "sha256": _sha256(target).removeprefix("sha256:"),
        })
    stages.append({"stage": "route", "status": 2, "elapsed_s": 5})
    (run / "stage_log.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in stages), encoding="utf-8"
    )
    (run / "stage_artifact_manifest.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in artifact_rows),
        encoding="utf-8",
    )
    (run / "flow.log").write_text(
        "[ERROR GRT-0232] Routing congestion too high.\n", encoding="utf-8"
    )
    (run / "run-meta.json").write_text(json.dumps({
        "config_mk": str(project / "constraints/config.mk"),
        "design_name": "gcd",
        "design_nickname": "gcd",
        "flow_variant": "fixed-v1",
        "make_status": 2,
        "openroad_exe": "/tools/openroad",
        "platform": "test",
        "run_tag": "RUN_test",
        "sdc_file": str(project / "constraints/constraint.sdc"),
        "toolchain_fingerprint": fingerprint,
        "yosys_exe": "/tools/yosys",
    }), encoding="utf-8")
    return run


def test_audit_flow_run_preserves_terminal_failure_and_epoch_roles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = _adapted_inventory(tmp_path)
    project = tmp_path / "campaign/project-gcd"
    stage_flow_project(inventory=inventory, design_id="gcd", output=project)
    run = _terminal_congestion_run(project)
    producer = tmp_path / "producer-epoch"
    auditor = tmp_path / "auditor-epoch"
    (producer / "bindings").mkdir(parents=True)
    (auditor / "bindings/oracles").mkdir(parents=True)
    (producer / "bindings/toolchain-manifest.json").write_text(json.dumps({
        "tools": {
            "openroad": {"path": "/tools/openroad"},
            "yosys": {"path": "/tools/yosys"},
        },
    }), encoding="utf-8")
    auditor_source = Path(research_flow.__file__).resolve()
    frozen_auditor = auditor / "bindings/oracles/00-research_flow.py"
    frozen_auditor.write_bytes(auditor_source.read_bytes())
    (auditor / "bindings/oracle-binding.json").write_text(json.dumps({
        "files": [{
            "source_path": str(auditor_source),
            "frozen_path": "bindings/oracles/00-research_flow.py",
            "sha256": _sha256(auditor_source),
        }],
    }), encoding="utf-8")

    def fake_verify_epoch(path: str | Path) -> dict[str, object]:
        resolved = Path(path).resolve()
        if resolved == producer.resolve():
            return {
                "valid": True,
                "research_evaluation_ready": True,
                "epoch_id": "producer-v1",
                "epoch_digest": "sha256:producer",
                "toolchain_manifest_digest": "sha256:tools",
            }
        if resolved == auditor.resolve():
            return {
                "valid": True,
                "research_evaluation_ready": True,
                "epoch_id": "auditor-v1",
                "epoch_digest": "sha256:auditor",
                "toolchain_manifest_digest": "sha256:tools",
            }
        raise AssertionError(f"unexpected epoch: {resolved}")

    monkeypatch.setattr(research_flow, "verify_research_epoch", fake_verify_epoch)
    output = tmp_path / "audit"
    result = audit_flow_run(
        project=project,
        run_dir=run,
        producer_epoch=producer,
        auditor_epoch=auditor,
        output=output,
    )

    assert result["valid"] is True
    assert result["oracle_verdict"] == "FAIL"
    assert result["oracle_reason"] == "routing_congestion"
    assert result["failure_layer"] == "FLOW_TARGET_FAILURE"
    audit = json.loads((output / "flow-audit.json").read_text(encoding="utf-8"))
    assert audit["producer_epoch_id"] == "producer-v1"
    assert audit["auditor_epoch_id"] == "auditor-v1"
    assert audit["checks"]["route"] == "FAIL"
    assert audit["checks"]["finish"] == "NOT_EXECUTED"
    assert audit["actual_cost"]["eda_stage_calls"] == 5
    assert audit["actual_cost"]["model_calls"] == 0
    assert verify_flow_audit(output)["valid"] is True

    (run / "flow.log").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ResearchFlowError, match="raw flow artifact drifted"):
        verify_flow_audit(output)


def test_flow_failure_class_keeps_infrastructure_unknown() -> None:
    assert research_flow._flow_failure_class(124, "timeout") == (
        "UNKNOWN", "flow_timeout", "INFRASTRUCTURE_ERROR"
    )


def test_authority_fingerprint_accepts_short_head_but_rejects_wrong_root() -> None:
    authority = {"root": "/authority/orfs", "git_head": "0123456789abcdef"}
    research_flow._verify_authority_fingerprint(
        "orfs=/authority/orfs@012345678 openroad=/tools/openroad", authority
    )
    with pytest.raises(ResearchFlowError, match="differs from adapter authority"):
        research_flow._verify_authority_fingerprint(
            "orfs=/other/orfs@012345678 openroad=/tools/openroad", authority
        )


def test_single_clock_constraint_retarget_is_explicit_and_fail_closed() -> None:
    template = (
        "current_design gcd\n"
        "set clk_port_name clk\n"
        "set clk_period 1.4\n"
        "create_clock -period $clk_period [get_ports $clk_port_name]\n"
    )
    binding = {
        "mode": "retarget_single_clock",
        "template_design": "gcd",
        "template_clock_port": "clk",
        "target_clock_port": "axis_clk",
        "clock_period_ns": 2.0,
    }
    rewritten, applied = research_flow._retarget_sdc(
        template, top="ll_axis_bridge", binding=binding
    )
    assert "current_design ll_axis_bridge" in rewritten
    assert "set clk_port_name axis_clk" in rewritten
    assert "set clk_period 2.0" in rewritten
    assert applied == {
        **binding,
        "target_design": "ll_axis_bridge",
    }

    with pytest.raises(ResearchFlowError, match="exactly one"):
        research_flow._retarget_sdc(
            template.replace("current_design gcd\n", ""),
            top="ll_axis_bridge",
            binding=binding,
        )
