"""P13 trigger-to-isolated-staging orchestration tests."""
from __future__ import annotations

import json
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from scripts import run_p13_shadow_update as shadow_runner
from scripts.run_p13_shadow_update import P13ShadowRunError, run_p13_shadow_update
from tehm.evolution import (
    AntiForgettingWitness, LocalizedUpdatePlan, P12ShadowUpdateTriggerReceipt,
)
from tehm.ids import stable_dumps
from tehm.state.shift_receipts import StateShiftReceipt


def _trigger() -> P12ShadowUpdateTriggerReceipt:
    return P12ShadowUpdateTriggerReceipt(
        cohort_receipt_digest="sha256:cohort",
        campaign_id="live", case_id="case-0", lineage_id="lineage-0",
        memory_arm="ALWAYS_MEMORY", routing_receipt_id="routing-0",
        routing_decision="CONSIDER", routing_decision_digest="sha256:routing",
        no_skill_reason=None, state_shift_receipt_id=None, risk_receipt_id=None,
        baseline_candidate_id="no_memory:case-0", memory_candidate_id="memory:case-0",
        baseline_execution_digest="sha256:baseline", memory_execution_digest="sha256:memory",
        baseline_outcome="PASS", memory_outcome="PASS",
        baseline_oracle_complete=True, memory_oracle_complete=True,
        learner_eligible=True, triggered=True, reason="oracle_complete",
        evolution_reasons=("NOVELTY",))


def _plan(trigger: P12ShadowUpdateTriggerReceipt) -> LocalizedUpdatePlan:
    return LocalizedUpdatePlan(
        transition_id="transition:p13-test", campaign_id="live",
        learner_eligible=True, priority="P1_HIGH", value_score=0.0,
        update_target="UPDATE_NONE", candidate_targets=("UPDATE_NONE",),
        operation="RETAIN", failure_type="NONE",
        evidence_refs=(trigger.receipt_digest,), rationale="P13 retain test")


def _state_shift_trigger() -> P12ShadowUpdateTriggerReceipt:
    payload = {
        "version": "state-shift-v0.1",
        "current_resolution_id": "resolution:case-0",
        "knowledge_object_id": "knowledge:case-0",
        "support_envelope_digest": "sha256:" + "e" * 64,
        "structural_shift": 1.0, "mechanism_shift": 0.0,
        "flow_shift": 0.0, "constraint_shift": 0.0,
        "oracle_shift": 0.0, "history_shift": 0.0,
        "aggregate_shift": 0.166667,
        "shifted_dimensions": ("structural_shift",),
        "transferable": False, "reason": "STATE_SHIFT",
        "evidence_refs": ("event:case-0",),
    }
    receipt = StateShiftReceipt(
        **payload,
        replay_digest="sha256:" + hashlib.sha256(
            stable_dumps(payload).encode()).hexdigest())
    return replace(
        _trigger(), routing_receipt_id="routing:state-shift",
        routing_decision="NO_SKILL", routing_decision_digest="sha256:routing",
        no_skill_reason="STATE_SHIFT", state_shift_receipt_id=receipt.receipt_id,
        state_shift_receipt=receipt.to_dict(),
        evolution_reasons=("STATE_SHIFT",))


def _inputs(tmp_path: Path, *, trigger: P12ShadowUpdateTriggerReceipt | None = None):
    import sqlite3

    db = tmp_path / "tehm.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE marker (value TEXT)")
    conn.execute("INSERT INTO marker VALUES ('unchanged')")
    conn.commit()
    conn.close()
    trigger = trigger or _trigger()
    trigger_report = tmp_path / "trigger.json"
    report_payload = {
        "version": "p13-shadow-trigger-report-v1", "campaign_id": "live",
        "p13_eligible": True, "trigger_count": 1, "triggered_count": 1,
        "triggers": [{**trigger.to_dict(), "receipt_digest": trigger.receipt_digest}],
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "production_integration": "not_attempted",
        "memory_docs_submitted": False,
        "shadow_update_policy": "isolated_staging_only",
    }
    report_payload["report_digest"] = "sha256:" + hashlib.sha256(
        stable_dumps(report_payload).encode()).hexdigest()
    trigger_report.write_text(json.dumps(report_payload))
    manifest = tmp_path / "manifest.json"
    plan = _plan(trigger)
    plan_payload = {**plan.to_dict(), "plan_digest": plan.plan_digest}
    manifest.write_text(json.dumps({
        "version": "p13-shadow-update-manifest-v1", "campaign_id": "live",
        "source_db": db.name,
        "source_db_sha256": "sha256:" + hashlib.sha256(
            db.read_bytes()).hexdigest(),
        "trigger_report_digest": report_payload["report_digest"],
        "updates": {"case-0": {"plan": plan_payload,
                                "evidence": {"p12_shadow_trigger": {
                                    **trigger.to_dict(),
                                    "receipt_digest": trigger.receipt_digest}}}},
    }))
    return db, trigger_report, manifest


def test_p13_runner_applies_retain_in_discarded_staging(tmp_path):
    db, trigger_report, manifest = _inputs(tmp_path)
    before = db.read_bytes()
    report = run_p13_shadow_update(
        trigger_report, manifest, output=tmp_path / "report.json")
    assert report["receipt_count"] == 1
    assert report["canonical_memory_mutation"] == "none"
    assert report["staging_discarded"] is True
    assert report["memory_docs_submitted"] is False
    assert db.read_bytes() == before


def test_p13_runner_accepts_typed_state_shift_trigger(tmp_path):
    db, trigger_report, manifest = _inputs(
        tmp_path, trigger=_state_shift_trigger())
    report = run_p13_shadow_update(
        trigger_report, manifest, output=tmp_path / "report.json")
    assert report["receipt_count"] == 1
    assert report["canonical_memory_mutation"] == "none"
    assert report["staging_discarded"] is True


def test_p13_runner_rejects_noneligible_trigger_report(tmp_path):
    db, trigger_report, manifest = _inputs(tmp_path)
    payload = json.loads(trigger_report.read_text())
    payload["p13_eligible"] = False
    trigger_report.write_text(json.dumps(payload))
    with pytest.raises(P13ShadowRunError, match="not eligible"):
        run_p13_shadow_update(
            trigger_report, manifest, output=tmp_path / "report.json")


def test_p13_runner_requires_content_bound_trigger_report(tmp_path):
    db, trigger_report, manifest = _inputs(tmp_path)
    payload = json.loads(trigger_report.read_text())
    payload["report_digest"] = "sha256:" + "0" * 64
    trigger_report.write_text(json.dumps(payload))
    with pytest.raises(P13ShadowRunError, match="report digest mismatch"):
        run_p13_shadow_update(
            trigger_report, manifest, output=tmp_path / "report.json")

    payload.pop("report_digest")
    trigger_report.write_text(json.dumps(payload))
    with pytest.raises(P13ShadowRunError, match="report digest is required"):
        run_p13_shadow_update(
            trigger_report, manifest, output=tmp_path / "report.json")


def test_p13_runner_rejects_trigger_report_crossing_docs_boundary(tmp_path):
    db, trigger_report, manifest = _inputs(tmp_path)
    payload = json.loads(trigger_report.read_text())
    payload["memory_docs_submitted"] = True
    payload["report_digest"] = "sha256:" + hashlib.sha256(
        stable_dumps(payload).encode()).hexdigest()
    trigger_report.write_text(json.dumps(payload))
    with pytest.raises(P13ShadowRunError, match="memory/docs boundary"):
        run_p13_shadow_update(
            trigger_report, manifest, output=tmp_path / "report.json")


def test_p13_runner_requires_content_bound_trigger_receipts(tmp_path):
    db, trigger_report, manifest = _inputs(tmp_path)
    payload = json.loads(trigger_report.read_text())
    payload["triggers"][0].pop("receipt_digest")
    payload.pop("report_digest")
    payload["report_digest"] = "sha256:" + hashlib.sha256(
        stable_dumps(payload).encode()).hexdigest()
    trigger_report.write_text(json.dumps(payload))
    with pytest.raises(P13ShadowRunError, match="receipt digest mismatch"):
        run_p13_shadow_update(
            trigger_report, manifest, output=tmp_path / "report.json")


def test_p13_runner_rejects_tampered_plan_digest(tmp_path):
    db, trigger_report, manifest = _inputs(tmp_path)
    payload = json.loads(manifest.read_text())
    payload["updates"]["case-0"]["plan"]["rationale"] = "tampered"
    manifest.write_text(json.dumps(payload))
    with pytest.raises(P13ShadowRunError, match="plan digest mismatch"):
        run_p13_shadow_update(
            trigger_report, manifest, output=tmp_path / "report.json")


def test_p13_runner_rejects_trigger_evidence_from_another_report(tmp_path):
    db, trigger_report, manifest = _inputs(tmp_path)
    payload = json.loads(manifest.read_text())
    trigger = _trigger()
    forged = replace(trigger, case_id="other-case")
    payload["updates"]["case-0"]["evidence"]["p12_shadow_trigger"] = {
        **forged.to_dict(), "receipt_digest": forged.receipt_digest}
    manifest.write_text(json.dumps(payload))
    with pytest.raises(P13ShadowRunError, match="trigger digest disagrees"):
        run_p13_shadow_update(
            trigger_report, manifest, output=tmp_path / "report.json")


def test_p13_runner_rejects_input_output_collision(tmp_path):
    db, trigger_report, manifest = _inputs(tmp_path)
    with pytest.raises(P13ShadowRunError, match="separate"):
        run_p13_shadow_update(trigger_report, manifest, output=db)


def test_p13_runner_rejects_manifest_cross_file_digest_drift(tmp_path):
    db, trigger_report, manifest = _inputs(tmp_path)
    payload = json.loads(manifest.read_text())
    payload["trigger_report_digest"] = "sha256:" + "0" * 64
    manifest.write_text(json.dumps(payload))
    with pytest.raises(P13ShadowRunError, match="trigger report digest disagrees"):
        run_p13_shadow_update(
            trigger_report, manifest, output=tmp_path / "report.json")

    payload = json.loads(manifest.read_text())
    payload["trigger_report_digest"] = json.loads(
        trigger_report.read_text())["report_digest"]
    payload["source_db_sha256"] = "sha256:" + "1" * 64
    manifest.write_text(json.dumps(payload))
    with pytest.raises(P13ShadowRunError, match="source DB digest disagrees"):
        run_p13_shadow_update(
            trigger_report, manifest, output=tmp_path / "report.json")


def test_p13_runner_preflights_anti_forgetting_before_opening_source(tmp_path, monkeypatch):
    db, trigger_report, manifest = _inputs(tmp_path)
    trigger = _trigger()
    plan = replace(
        _plan(trigger), learner_eligible=True,
        update_target="UPDATE_STATE_RELATION",
        candidate_targets=("UPDATE_STATE_RELATION",), operation="INVALIDATE",
        failure_type="STATE_RESOLUTION_FAILURE")
    payload = json.loads(manifest.read_text())
    payload["updates"]["case-0"] = {
        "plan": {**plan.to_dict(), "plan_digest": plan.plan_digest},
        "evidence": {
            "p12_shadow_trigger": {
                **trigger.to_dict(), "receipt_digest": trigger.receipt_digest},
            "relation": {
                "source_type": "transition", "source_id": "transition:p13-test",
                "relation_type": "INVALIDATES", "target_type": "transition",
                "target_id": "transition:other",
                "evidence_refs": [trigger.receipt_digest]},
        },
    }
    manifest.write_text(json.dumps(payload))

    def fail_open(_path):
        raise AssertionError("source must not open before anti-forgetting preflight")

    monkeypatch.setattr(shadow_runner, "_open_read_only", fail_open)
    with pytest.raises(P13ShadowRunError, match="anti-forgetting witness"):
        run_p13_shadow_update(
            trigger_report, manifest, output=tmp_path / "report.json")


def test_p13_runner_consumes_file_bound_witness(tmp_path, monkeypatch):
    db, trigger_report, manifest = _inputs(tmp_path)
    trigger = _trigger()
    witness = AntiForgettingWitness(
        target_replay_receipt_id="target:case-0",
        target_replay_digest="sha256:" + "1" * 64,
        target_replay_passed=True,
        non_target_regression_receipt_id="non-target:case-0",
        non_target_regression_digest="sha256:" + "2" * 64,
        non_target_regression_free=True,
        heldout_audit_receipt_id="heldout:case-0",
        heldout_audit_digest="sha256:" + "3" * 64,
        heldout_audit_passed=True,
        rollback_pointer="snapshot:before",
        rollback_receipt_digest="sha256:" + "4" * 64,
        rollback_verified=True,
        evidence_refs=("target:case-0", "non-target:case-0",
                       "heldout:case-0", "rollback:case-0"),
    )
    witness_report = tmp_path / "witness-report.json"
    witness_report.write_text(json.dumps({
        "version": "p13-anti-forgetting-witness-report-v1",
        "campaign_id": "live", "case_id": "case-0",
        "eligible": True,
        "memory_docs_submitted": False,
        "witness": {**witness.to_dict(),
                     "receipt_digest": witness.receipt_digest},
    }))
    plan = replace(
        _plan(trigger), learner_eligible=True,
        update_target="UPDATE_STATE_RELATION",
        candidate_targets=("UPDATE_STATE_RELATION",), operation="INVALIDATE",
        failure_type="STATE_RESOLUTION_FAILURE",
        evidence_refs=(trigger.receipt_digest, witness.receipt_digest))
    payload = json.loads(manifest.read_text())
    payload["updates"]["case-0"]["plan"] = {
        **plan.to_dict(), "plan_digest": plan.plan_digest}
    payload["updates"]["case-0"]["evidence"] = {
        "p12_shadow_trigger": {
            **trigger.to_dict(), "receipt_digest": trigger.receipt_digest},
        "relation": {
            "source_type": "transition", "source_id": "transition:p13-test",
            "relation_type": "INVALIDATES", "target_type": "transition",
            "target_id": "transition:other",
            "evidence_refs": [trigger.receipt_digest]},
    }
    payload["anti_forgetting_receipts"] = {
        "case-0": {"path": witness_report.name,
                   "sha256": "sha256:" + hashlib.sha256(
                       witness_report.read_bytes()).hexdigest()}}
    manifest.write_text(json.dumps(payload))
    seen = {}

    class _Receipt:
        campaign_id = "live"
        receipt_digest = "sha256:" + "a" * 64

        def to_dict(self):
            return {"campaign_id": self.campaign_id}

    def fake_apply(plan, _conn, evidence):
        seen["evidence"] = evidence
        return _Receipt()

    monkeypatch.setattr(shadow_runner, "apply_localized_update_shadow", fake_apply)
    report = run_p13_shadow_update(
        trigger_report, manifest, output=tmp_path / "report.json")
    assert report["receipt_count"] == 1
    assert seen["evidence"]["anti_forgetting"]["receipt_digest"] == witness.receipt_digest
    assert report["anti_forgetting_receipts"]["case-0"][
        "witness_receipt_digest"] == witness.receipt_digest


def test_p13_runner_rejects_legacy_trigger_for_current_mutation(tmp_path):
    db, trigger_report, manifest = _inputs(tmp_path)
    legacy = replace(_trigger(), version="p12-shadow-trigger-v0.1",
                     evolution_reasons=())
    payload = json.loads(trigger_report.read_text())
    payload["triggers"] = [{**legacy.to_dict(),
                             "receipt_digest": legacy.legacy_receipt_digest}]
    payload.pop("report_digest", None)
    payload["report_digest"] = "sha256:" + hashlib.sha256(
        stable_dumps(payload).encode()).hexdigest()
    trigger_report.write_text(json.dumps(payload))
    with pytest.raises(P13ShadowRunError, match="legacy trigger"):
        run_p13_shadow_update(
            trigger_report, manifest, output=tmp_path / "report.json")
