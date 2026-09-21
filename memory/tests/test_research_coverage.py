"""Read-only route coverage keeps the full fixed-flow denominator honest."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tehm.evaluation import research_coverage as coverage
from tehm.state import ensure_state_schema


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _case(root: Path, design: str, verdict: str) -> tuple[Path, dict]:
    project = root / f"project-{design}"
    receipt = f"sha256:{design}-staged"
    _write(project / "stage-receipt.json", {
        "design_id": design, "platform": "sky130hs",
        "source_bundle_digest": f"sha256:{design}-source",
        "receipt_digest": receipt,
    })
    audit_root = root / f"audit-{design}"
    _write(audit_root / "flow-audit.json", {
        "design_id": design,
        "project_path": str(project),
        "stage_receipt_digest": receipt,
        "source_mutation": "none",
        "memory_update": "none",
        "toolchain_manifest_digest": "toolchain-frozen",
    })
    result = {
        "valid": True,
        "audit_digest": f"sha256:{design}-audit",
        "oracle_verdict": verdict,
        "oracle_reason": "routing_congestion" if verdict == "FAIL" else "flow_completed",
        "failure_layer": "FLOW_TARGET_FAILURE" if verdict == "FAIL" else "NONE",
        "terminal_stage": "route" if verdict == "FAIL" else "finish",
    }
    return audit_root, result


def _setup(tmp_tehm, monkeypatch):
    connection, _, root = tmp_tehm
    ensure_state_schema(connection)
    connection.close()
    database = root / "tehm.sqlite"
    snapshot = root / "m0"
    (snapshot / "closed_loop").mkdir(parents=True)
    database.rename(snapshot / "closed_loop" / "tehm.sqlite")
    database = snapshot / "closed_loop" / "tehm.sqlite"
    epoch = root / "epoch"
    _write(epoch / "research-epoch.json", {
        "epoch_id": "frozen-test-epoch",
        "authority": {
            "research_only": True,
            "online_memory_update": False,
            "production_authority": False,
        },
        "memory_snapshot": {"bundle_path": str(snapshot)},
        "budget": {"frozen_path": "bindings/budget.json"},
    })
    _write(epoch / "bindings/budget.json", {"candidate_limit": 3})
    monkeypatch.setattr(coverage, "verify_research_epoch", lambda _: {
        "valid": True, "research_evaluation_ready": True,
        "epoch_digest": "sha256:frozen-epoch",
        "toolchain_manifest_digest": "toolchain-frozen",
        "memory_bundle_digest": "frozen-m0",
    })
    return root, epoch, database


def test_coverage_replays_real_router_without_mutating_m0(tmp_tehm, monkeypatch):
    root, epoch, database = _setup(tmp_tehm, monkeypatch)
    gcd, gcd_result = _case(root, "gcd", "FAIL")
    uart, uart_result = _case(root, "uart", "PASS")
    results = {gcd: gcd_result, uart: uart_result}
    monkeypatch.setattr(coverage, "verify_flow_audit", lambda path: results[path])
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    output = root / "coverage"
    reported = coverage.audit_memory_route_coverage(
        epoch=epoch, flow_audits=[gcd, uart], output=output)
    assert reported["valid"] is True
    assert reported["denominator"] == {
        "registered_flows": 2,
        "routable_terminal_failures": 1,
        "route_selected": 0,
        "no_match": 1,
        "abstained": 0,
        "no_target_failure": 1,
        "unverified_flow": 0,
    }
    payload = json.loads((output / "coverage.json").read_text())
    assert payload["rows"][0]["routing_decision"]["decision"] == "NO_SKILL"
    assert payload["rows"][0]["routing_decision"]["no_skill_reason"] == "NO_MATCH"
    assert payload["rows"][1]["query"] is None
    assert payload["authority"]["action_selected_or_executed"] is False
    assert coverage.verify_memory_route_coverage(output) == reported
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert not (database.parent / "tehm.sqlite-wal").exists()
    assert not (database.parent / "tehm.sqlite-shm").exists()

    with pytest.raises(coverage.ResearchCoverageError, match="overwrite"):
        coverage.audit_memory_route_coverage(
            epoch=epoch, flow_audits=[gcd, uart], output=output)
    payload["denominator"]["no_match"] = 0
    _write(output / "coverage.json", payload)
    with pytest.raises(coverage.ResearchCoverageError, match="digest mismatch"):
        coverage.verify_memory_route_coverage(output)


def test_coverage_refuses_duplicate_designs_and_bad_authority(tmp_tehm, monkeypatch):
    root, epoch, _ = _setup(tmp_tehm, monkeypatch)
    first, checked = _case(root, "gcd", "FAIL")
    second = root / "audit-gcd-replay"
    _write(second / "flow-audit.json", json.loads((first / "flow-audit.json").read_text()))
    monkeypatch.setattr(coverage, "verify_flow_audit", lambda _: checked)
    with pytest.raises(coverage.ResearchCoverageError, match="duplicate design"):
        coverage.audit_memory_route_coverage(
            epoch=epoch, flow_audits=[first, second], output=root / "coverage")
    assert not (root / "coverage").exists()
    payload = json.loads((first / "flow-audit.json").read_text())
    payload["toolchain_manifest_digest"] = "drifted"
    _write(first / "flow-audit.json", payload)
    with pytest.raises(coverage.ResearchCoverageError, match="authority"):
        coverage.audit_memory_route_coverage(
            epoch=epoch, flow_audits=[first], output=root / "coverage")
