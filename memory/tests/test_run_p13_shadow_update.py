"""P13 trigger-to-isolated-staging orchestration tests."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from scripts import run_p13_shadow_update as shadow_runner
from scripts.run_p13_shadow_update import P13ShadowRunError, run_p13_shadow_update
from tehm.evolution import LocalizedUpdatePlan, P12ShadowUpdateTriggerReceipt


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


def _inputs(tmp_path: Path):
    import sqlite3

    db = tmp_path / "tehm.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE marker (value TEXT)")
    conn.execute("INSERT INTO marker VALUES ('unchanged')")
    conn.commit()
    conn.close()
    trigger = _trigger()
    trigger_report = tmp_path / "trigger.json"
    trigger_report.write_text(json.dumps({
        "version": "p13-shadow-trigger-report-v1", "campaign_id": "live",
        "p13_eligible": True, "trigger_count": 1, "triggered_count": 1,
        "triggers": [{**trigger.to_dict(), "receipt_digest": trigger.receipt_digest}],
    }))
    manifest = tmp_path / "manifest.json"
    plan = _plan(trigger)
    manifest.write_text(json.dumps({
        "version": "p13-shadow-update-manifest-v1", "campaign_id": "live",
        "source_db": db.name,
        "updates": {"case-0": {"plan": plan.to_dict(),
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
    assert db.read_bytes() == before


def test_p13_runner_rejects_noneligible_trigger_report(tmp_path):
    db, trigger_report, manifest = _inputs(tmp_path)
    payload = json.loads(trigger_report.read_text())
    payload["p13_eligible"] = False
    trigger_report.write_text(json.dumps(payload))
    with pytest.raises(P13ShadowRunError, match="not eligible"):
        run_p13_shadow_update(
            trigger_report, manifest, output=tmp_path / "report.json")


def test_p13_runner_rejects_input_output_collision(tmp_path):
    db, trigger_report, manifest = _inputs(tmp_path)
    with pytest.raises(P13ShadowRunError, match="separate"):
        run_p13_shadow_update(trigger_report, manifest, output=db)


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
        "plan": plan.to_dict(),
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


def test_p13_runner_rejects_legacy_trigger_for_current_mutation(tmp_path):
    db, trigger_report, manifest = _inputs(tmp_path)
    legacy = replace(_trigger(), version="p12-shadow-trigger-v0.1",
                     evolution_reasons=())
    payload = json.loads(trigger_report.read_text())
    payload["triggers"] = [{**legacy.to_dict(),
                             "receipt_digest": legacy.legacy_receipt_digest}]
    trigger_report.write_text(json.dumps(payload))
    with pytest.raises(P13ShadowRunError, match="legacy trigger"):
        run_p13_shadow_update(
            trigger_report, manifest, output=tmp_path / "report.json")
