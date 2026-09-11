"""P13 StateShift isolated source-snapshot evidence boundaries."""
from __future__ import annotations

import hashlib
import json

import pytest

from scripts import build_p13_state_shift_source_snapshot as source_builder
from scripts.build_p13_shadow_trigger_report import build_p13_shadow_trigger_report
from scripts.build_p13_state_shift_admission_report import (
    build_p13_state_shift_admission_report,
)
from scripts.build_p13_state_shift_plan_report import (
    build_p13_state_shift_plan_report,
)
from scripts.build_p13_state_shift_proposal_report import (
    build_p13_state_shift_proposal_report,
)
from scripts.build_p13_state_shift_reason_bundle import (
    build_p13_state_shift_reason_bundle,
)
from tehm.ids import stable_dumps
from test_p13_shadow_trigger_report import (
    _write_state_shift_audit,
    _write_state_shift_inputs,
)


def _digest(payload) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(payload).encode()).hexdigest()


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_bound_plan(tmp_path):
    cohort, partition, routes, _ = _write_state_shift_inputs(tmp_path)
    audit = _write_state_shift_audit(tmp_path, cohort, routes)
    audit_payload = json.loads(audit.read_text())
    for item in audit_payload["cases"]:
        item["registration"] = {
            "current_context_digest": item["state_shift"][
                "current_context_digest"],
        }
    acquisitions_path = tmp_path / "scoped-support-envelope-acquisitions.json"
    acquisitions = {"transition-fixture": {"fixture": True}}
    acquisition_payload = {
        "digest": _digest(acquisitions), "acquisitions": acquisitions,
    }
    acquisitions_path.write_text(json.dumps(acquisition_payload))
    parent = audit_payload["parent_replay"]
    parent.update({
        "campaign_id": "fixture-parent-campaign",
        "acquisition_digest": acquisition_payload["digest"],
        "path_id": "causal-path-fixture",
        "replication": {"eligible": True},
        "support_envelope": {"envelope_digest": "sha256:envelope"},
        "authority": {"eligible": True, "target_scope": "fixture-scope"},
    })
    parent_audit_path = tmp_path / "scoped-support-envelope-audit.json"
    parent_audit_path.write_text(json.dumps({
        "acquisition_digest": parent["acquisition_digest"],
        **{key: parent[key] for key in (
            "pairs", "path_id", "replication", "knowledge",
            "support_envelope", "authority")},
    }))
    audit_payload["source_freeze"] = {"external_inputs": {
        str(acquisitions_path): _sha(acquisitions_path),
        str(parent_audit_path): _sha(parent_audit_path),
    }}
    audit_payload.pop("audit_digest")
    audit_payload["audit_digest"] = _digest(audit_payload)
    audit.write_text(json.dumps(audit_payload))

    reason_bundle = tmp_path / "reason-bundle.json"
    build_p13_state_shift_reason_bundle(
        cohort, audit, output=reason_bundle)
    trigger = tmp_path / "trigger-report.json"
    build_p13_shadow_trigger_report(
        cohort, partition, routing_path=routes,
        typed_reason_bundle_path=reason_bundle, output=trigger)
    admission = tmp_path / "admission-report.json"
    build_p13_state_shift_admission_report(
        cohort, audit, reason_bundle, routes, partition, trigger,
        output=admission)
    proposal = tmp_path / "proposal-report.json"
    build_p13_state_shift_proposal_report(admission, output=proposal)
    plan = tmp_path / "plan-report.json"
    build_p13_state_shift_plan_report(proposal, output=plan)
    return plan, acquisitions_path


def _fake_rebuild(acquisition, parent_audit, *, source_db, artifacts):
    source_db.write_bytes(b"isolated-fixture-snapshot")
    artifacts.mkdir()
    (artifacts / "artifact").write_text("fixture")
    return {
        "campaign_id": "fixture-parent-campaign",
        "target_scope": "fixture-scope",
        "acquisition_digest": acquisition["digest"],
        "transition_ids": ["transition-fixture"],
        "lineage_count": 2,
        "pair_ids": ["pair-a", "pair-b"],
        "path_id": "causal-path-fixture",
        "knowledge_object_id": "knowledge:shared@1",
        "knowledge_content_digest": "sha256:knowledge",
        "support_envelope_digest": "sha256:envelope",
        "authority_receipt_digest": "sha256:authority",
        "logical_database_digest": "sha256:logical",
        "table_counts": {},
        "asset_id": "asset-fixture",
        "resolved_source_state": {
            "resolution_id": "resolution-replayed-fixture",
        },
        "resolved_source_semantic_digest": "sha256:semantic",
    }


def test_source_snapshot_binds_plan_and_keeps_shadow_closed(tmp_path, monkeypatch):
    plan, _acquisitions = _source_bound_plan(tmp_path)
    monkeypatch.setattr(source_builder, "_rebuild_parent", _fake_rebuild)
    report = source_builder.build_p13_state_shift_source_snapshot(
        plan, output_dir=tmp_path / "source-snapshot")
    assert report["source_database_present"] is True
    assert report["source_database"]["role"] == (
        "isolated_scoped_replay_source")
    assert report["scoped_replay_required_for_shadow"] is True
    assert report["anti_forgetting_present"] is False
    assert report["shadow_update_attempted"] is False
    assert report["shadow_execution_ready"] is False
    assert report["campaign_binding"] == {
        "plan_campaign_id": "state-shift-campaign",
        "training_evidence_campaign_id": "fixture-parent-campaign",
        "exact_match": False,
        "training_membership_relabelled": False,
    }
    assert report["state_binding"]["exact_resolution_match"] is False
    assert report["state_binding"]["resolution_rebase_required"] is True
    assert report["state_binding"]["resolution_rebase_authorized"] is False
    assert report["remaining_gates"] == [
        "anti_forgetting_witness",
        "cross_campaign_training_evidence_verification",
        "state_resolution_rebase_authority",
    ]
    assert report["canonical_memory_mutation"] == "none"
    assert report["production_runtime_imported"] is False


def test_source_snapshot_rejects_frozen_acquisition_drift(tmp_path, monkeypatch):
    plan, acquisitions = _source_bound_plan(tmp_path)
    acquisitions.write_text(acquisitions.read_text() + "\n")
    monkeypatch.setattr(source_builder, "_rebuild_parent", _fake_rebuild)
    output = tmp_path / "source-snapshot"
    with pytest.raises(
            source_builder.P13StateShiftSourceSnapshotError,
            match="frozen external input digest mismatch"):
        source_builder.build_p13_state_shift_source_snapshot(
            plan, output_dir=output)
    assert not output.exists()
    assert not (tmp_path / "source-snapshot").exists()
