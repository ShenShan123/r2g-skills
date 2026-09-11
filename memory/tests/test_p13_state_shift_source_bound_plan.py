"""Source-bound StateShift plans replay source and provenance boundaries."""
from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from scripts.build_p13_state_shift_source_bound_plan import (
    P13StateShiftSourceBoundPlanError,
    build_p13_state_shift_source_bound_plan,
)
from tehm.ids import stable_dumps
from tehm.knowledge import register_knowledge
from tehm.state import resolve_current_state
from tehm.evolution import state_semantic_digest
from test_state_rebase import _claim, _plan


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _sha(path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _source_fixture(tmp_tehm, tmp_path):
    conn, _, _ = tmp_tehm
    parent = _claim()
    register_knowledge(
        conn, parent, target_scope="flow_feasibility", evidence_refs=[{
            "evidence_type": "manual_review", "evidence_id": "rebase-seed",
            "split": "training", "lineage_id": "lineage-a",
            "evidence_level": parent.evidence_level,
        }])
    conn.execute(
        """INSERT INTO tehm_dataset_membership
           (transition_id, campaign_id, split, learner_eligible,
            frozen_snapshot_digest, assigned_at)
           VALUES (?, ?, 'training', 1, NULL, ?)""",
        ("transition-a", "training-campaign", "2000-01-01T00:00:00+00:00"))
    conn.commit()
    scope = {
        "mechanism_family": "DENSITY_RELIEF",
        "target_scope": "flow_feasibility",
    }
    state = resolve_current_state(conn, scope, mode="shadow", persist=False)
    source_db = tmp_path / "source.sqlite"
    frozen = sqlite3.connect(source_db)
    conn.backup(frozen)
    frozen.close()
    check = sqlite3.connect(source_db)
    logical = _digest("\n".join(check.iterdump()))
    check.close()

    plan = _plan(parent)
    plan_report = {
        "version": "p13-state-shift-plan-report-v1",
        "proposal_transition_ids": ["transition-a"],
        "localized_update_plan": {
            **plan.to_dict(), "plan_digest": plan.plan_digest,
        },
    }
    plan_report["report_digest"] = _digest(plan_report)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan_report))

    source_report = {
        "version": "p13-state-shift-source-snapshot-v1",
        "plan_report": {
            "path": str(plan_path), "sha256": _sha(plan_path),
            "report_digest": plan_report["report_digest"],
            "plan_digest": plan.plan_digest,
        },
        "source_database": {
            "path": str(source_db), "sha256": _sha(source_db),
            "logical_digest": logical, "sidecar_free": True,
        },
        "replay": {
            "transition_ids": ["transition-a"],
            "resolved_source_state": state.to_dict(),
            "resolved_source_semantic_digest": state_semantic_digest(state),
        },
        "campaign_binding": {
            "training_evidence_campaign_id": "training-campaign",
            "training_membership_relabelled": False,
        },
        "state_binding": {
            "source_resolution_id": state.resolution_id,
            "source_semantic_digest": state_semantic_digest(state),
            "plan_resolution_id": plan.state_resolution_id,
            "resolution_rebase_required": True,
            "resolution_rebase_authorized": False,
        },
        "source_database_present": True,
        "source_database_mutated_after_freeze": False,
        "scoped_replay_required_for_shadow": True,
        "anti_forgetting_present": False,
        "shadow_update_attempted": False,
        "shadow_execution_ready": False,
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "production_integration": "not_attempted",
        "memory_docs_submitted": False,
    }
    source_report["report_digest"] = _digest(source_report)
    source_path = tmp_path / "source-report.json"
    source_path.write_text(json.dumps(source_report))
    return source_path


def test_source_bound_plan_rebases_and_keeps_execution_closed(tmp_tehm, tmp_path):
    source = _source_fixture(tmp_tehm, tmp_path)
    report = build_p13_state_shift_source_bound_plan(
        source, output=tmp_path / "source-bound-plan.json")
    assert report["cross_campaign_training_membership_verified"] is True
    assert report["training_membership_relabelled"] is False
    assert report["source_resolution_rebase_authorized_for_shadow"] is True
    assert report["eligible_for_anti_forgetting"] is True
    assert report["scoped_verified_execution_replay_pending"] is True
    assert report["anti_forgetting_present"] is False
    assert report["shadow_update_attempted"] is False
    assert report["shadow_execution_ready"] is False
    assert report["canonical_memory_mutation"] == "none"
    assert report["resolution_rebase"]["receipt_digest"] in report[
        "source_bound_localized_update_plan"]["evidence_refs"]


def test_source_bound_plan_rejects_rehashed_state_binding_drift(
        tmp_tehm, tmp_path):
    source = _source_fixture(tmp_tehm, tmp_path)
    payload = json.loads(source.read_text())
    payload["state_binding"]["source_resolution_id"] = "resolution-tampered"
    payload.pop("report_digest")
    payload["report_digest"] = _digest(payload)
    source.write_text(json.dumps(payload))
    with pytest.raises(P13StateShiftSourceBoundPlanError,
                       match="state binding is not eligible"):
        build_p13_state_shift_source_bound_plan(
            source, output=tmp_path / "source-bound-plan.json")
