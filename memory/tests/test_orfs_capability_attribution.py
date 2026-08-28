"""ORFS capability attribution binds C1-C8 to database authority."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from tehm.canonical.capture import ExecutionRecord

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import build_orfs_capability_attribution as orfs_attribution  # noqa: E402


def _record(lineage: str) -> ExecutionRecord:
    return ExecutionRecord(
        record_id=f"orfs-capability:{lineage}",
        domain="flow.signoff",
        project_id=lineage,
        design_id=lineage,
        lineage_id=lineage,
        repository_ref=f"orfs-source:{lineage}",
        before={
            "config": {"DESIGN_NAME": lineage, "PLATFORM": "sky130hs",
                       "CORE_UTILIZATION": "50"},
            "reports": {"route": {"status": "fail"}},
            "failure_signature": {"check": "route", "class": "route"},
        },
        action={
            "domain": "flow.CONFIG_DELTA",
            "transformation_family": "DENSITY_RELIEF",
            "payload": {"config_edits": {"CORE_UTILIZATION": "40"},
                        "rerun_from": "floorplan", "recheck": "route"},
        },
        after={
            "config": {"DESIGN_NAME": lineage, "PLATFORM": "sky130hs",
                        "CORE_UTILIZATION": "40"},
            "reports": {"route": {"status": "clean"}},
        },
        observation_delta={
            "original_failure": "REMOVED",
            "first_divergence": {"before": 1, "after": 0},
            "failing_tests": {"before": 1, "after": 0},
            "created_regressions": [], "newly_observed_failures": [],
            "experiment_kind": "REPAIR", "utility_verdict": "NEUTRAL",
        },
        verification={
            "verdict": "PASS", "oracle_type": "TARGET_TEST",
            "scope": "signoff:route", "confidence_tier": "T",
            "obligation_coverage": 1.0,
            "evidence_refs": [f"orfs:{lineage}:before",
                              f"orfs:{lineage}:after"],
            "tool_versions": {"orfs": "test"}, "oracle_complete": True,
        },
        episode={"episode_id": f"episode:{lineage}",
                 "mechanism_family": "DENSITY_RELIEF",
                 "lineage_id": lineage, "terminal_status": "VERIFIED_REPAIR"},
    )


def test_orfs_ablation_action_check_fails_closed_on_missing_or_duplicate_receipts():
    runtime = {"decisions": [{"lineage_id": "train:a",
                               "selected_action": "none"}]}
    assert orfs_attribution._runtime_selects_action(
        runtime, {"train:a"}, "DENSITY_RELIEF") is False
    duplicate = {"decisions": [
        {"lineage_id": "train:a", "selected_action": "DENSITY_RELIEF"},
        {"lineage_id": "train:a", "selected_action": "none"},
    ]}
    assert orfs_attribution._runtime_selects_action(
        duplicate, {"train:a"}, "DENSITY_RELIEF") is False


def test_orfs_capability_authority_is_db_bound_and_read_only(
        tmp_tehm, tmp_path, monkeypatch):
    conn, _, _ = tmp_tehm
    source = tmp_path / "source.sqlite"
    destination = sqlite3.connect(source)
    conn.backup(destination)
    destination.close()
    conn.close()
    source_before = hashlib.sha256(source.read_bytes()).hexdigest()

    monkeypatch.setattr(orfs_attribution, "_build_record",
                        lambda spec: _record(str(spec["lineage_id"])))
    causal_report = tmp_path / "causal.json"
    causal_report.write_text(json.dumps({
        "path": {"path_id": "causal_path_test"},
        "replication": {"eligible": True},
    }))
    train = [
        {"lineage_id": "train:a", "config_edits": {"CORE_UTILIZATION": "40"}},
        {"lineage_id": "train:b", "config_edits": {"CORE_UTILIZATION": "40"}},
    ]
    result = orfs_attribution.build_orfs_capability_attribution(
        source, output_dir=tmp_path / "attribution",
        causal_report=causal_report, training_pairs=train,
        heldout_pair={"lineage_id": "heldout:c",
                      "config_edits": {"CORE_UTILIZATION": "40"}},
        non_target_pair={"lineage_id": "non-target:d",
                         "config_edits": {"CORE_UTILIZATION": "40"}},
    )

    assert result["attribution"]["attribution"]["gates"] == {
        "C1": True, "C2": True, "C3": True, "C4": True,
        "C5": True, "C6": True, "C7": True, "C8": True,
    }
    assert result["capability_authority_gates"]["eligible"] is True
    assert result["capability_authority_verified"]["eligible"] is True
    assert result["capability_promotion_eligible"] is True
    assert result["promotion_attempted"] is False
    assert result["production_promotion_eligible"] is False
    assert result["canonical_memory_mutation"] == "none"
    memory_delta = result["attribution"]["attribution"]["detail"]["memory_delta"]
    assert memory_delta["eligible"] is True
    transition_ids = memory_delta["delta"]["added_transition_ids"]
    assert len(transition_ids) == len(set(transition_ids)) == 2
    assert all(item.startswith("transition_") for item in transition_ids)
    assert result["memory_delta"]["candidate_memory_digest"] == (
        memory_delta["candidate_memory_digest"])
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_before
    ablation = result["ablation_runtime"]
    detail_ablation = result["attribution"]["attribution"]["detail"]["ablation"]
    assert ablation["runtime"]["receipt_id"] == detail_ablation["runtime_receipt_id"]
    assert detail_ablation["gain_without_memory"] is False
    assert detail_ablation["gain_with_memory"] is True
    assert ablation["policy_load"]["receipt_id"] == detail_ablation[
        "policy_load_receipt_id"]
    load = result["policy_load"]
    assert load["receipt_id"]
    assert load["execution_receipt_id"] == (
        result["runtime_behavior"]["candidate"]["receipt_id"])
    load_row = sqlite3.connect(result["derived_db"]).execute(
        "SELECT receipt_json FROM tehm_policy_load_receipts "
        "WHERE receipt_id=?", (load["receipt_id"],)).fetchone()
    assert load_row is not None
    load_payload = json.loads(load_row[0])
    assert load_payload["receipt"]["execution_receipt_id"] == (
        result["runtime_behavior"]["candidate"]["receipt_id"])

    derived = result["derived_db"]
    authority_rows = sqlite3.connect(derived).execute(
        "SELECT COUNT(*) FROM tehm_capability_evidence "
        "WHERE evidence_type='capability_authority'"
    ).fetchone()[0]
    assert authority_rows == 1
