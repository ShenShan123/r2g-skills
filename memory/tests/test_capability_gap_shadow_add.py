"""P13 gap-plan/sink units. Mocked source facts carry no empirical credit."""
from dataclasses import replace
import importlib
import sqlite3

import pytest
from tehm.evolution import LocalizedUpdatePlan, ShadowUpdateError, apply_localized_update_shadow
from tehm.evolution.apply_update import _gap_source_for_plan
from tehm.evolution.admission import EvolutionAdmissionReceipt
from tehm.evolution.reason_derivation import EvolutionReasonDerivationReceipt
from test_capability_gap_source_witness import bound


def plan(receipt):
    reason = EvolutionReasonDerivationReceipt.from_dict(receipt.reason)
    admission = EvolutionAdmissionReceipt.from_dict(receipt.admission)
    return LocalizedUpdatePlan(transition_id=receipt.transition_ids[0], campaign_id=receipt.campaign_id,
        learner_eligible=True, priority="P1_HIGH", value_score=0.8,
        update_target="UPDATE_CAUSAL_KNOWLEDGE", candidate_targets=("UPDATE_CAUSAL_KNOWLEDGE",),
        operation="ADD", failure_type="CAPABILITY_GAP", rationale="unit source-bound gap ADD",
        evidence_refs=(*receipt.transition_ids, receipt.receipt_digest, reason.receipt_digest, admission.receipt_digest))


def test_gap_plan_replays_source_before_any_staging_write(bound):
    conn, _, receipt = bound
    before = "\n".join(conn.iterdump())
    assert _gap_source_for_plan(plan(receipt), conn, {"capability_gap_source": receipt}) == receipt
    assert "\n".join(conn.iterdump()) == before


@pytest.mark.parametrize("kind", ["missing", "digest_missing", "reason_missing", "admission_missing", "foreign_campaign",
    "foreign_transition", "existing_knowledge", "existing_rule", "rule_target", "revision", "wrong_reason", "source_modified"])
def test_invalid_gap_plan_cannot_enter_staging(bound, kind):
    conn, _, receipt = bound
    proposed = plan(receipt);evidence = {"capability_gap_source": receipt}
    conn.execute("SAVEPOINT negative_plan")
    try:
        if kind == "missing": evidence = {}
        elif kind in {"digest_missing", "reason_missing", "admission_missing"}:
            removed = {"digest_missing": receipt.receipt_digest,
                "reason_missing": EvolutionReasonDerivationReceipt.from_dict(receipt.reason).receipt_digest,
                "admission_missing": EvolutionAdmissionReceipt.from_dict(receipt.admission).receipt_digest}[kind]
            proposed = replace(proposed, evidence_refs=tuple(v for v in proposed.evidence_refs if v != removed))
        elif kind == "foreign_campaign": proposed = replace(proposed, campaign_id="foreign")
        elif kind == "foreign_transition": proposed = replace(proposed, transition_id="not-source")
        elif kind == "existing_knowledge": proposed = replace(proposed, knowledge_refs=("existing@1",))
        elif kind == "existing_rule": proposed = replace(proposed, rule_refs=("existing",))
        elif kind == "rule_target": proposed = replace(proposed, update_target="UPDATE_RULE", candidate_targets=("UPDATE_RULE",))
        elif kind == "revision": proposed = replace(proposed, operation="REVISE")
        elif kind == "wrong_reason": proposed = replace(proposed, failure_type="CAUSAL_MODEL_FAILURE")
        else: conn.execute("UPDATE tehm_dataset_membership SET split='heldout'")
        with pytest.raises(ShadowUpdateError): _gap_source_for_plan(proposed, conn, evidence)
    finally:
        conn.execute("ROLLBACK TO negative_plan");conn.execute("RELEASE negative_plan")


def retain():
    return LocalizedUpdatePlan(transition_id="unit-retain", campaign_id="live", learner_eligible=False,
        priority="P1_HIGH", value_score=0.0, update_target="UPDATE_NONE", candidate_targets=("UPDATE_NONE",),
        operation="RETAIN", failure_type="NO_FAILURE", rationale="unit immutable snapshot sink")


def test_sink_only_receives_immutable_snapshot_bytes(tmp_tehm):
    conn, _, _ = tmp_tehm;before = "\n".join(conn.iterdump());captured = []
    receipt = apply_localized_update_shadow(retain(), conn, staging_artifact_sink=captured.append)
    assert receipt.staging_discarded and receipt.source_digest_before == receipt.source_digest_after
    assert len(captured) == 1 and type(captured[0]) is bytes
    assert captured[0][18:20] == b"\x01\x01"
    copied = sqlite3.connect(":memory:")
    try:
        copied.deserialize(captured[0])
        assert copied.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally: copied.close()
    assert "\n".join(conn.iterdump()) == before


def test_invalid_sink_rejected_before_stage(tmp_tehm):
    conn, _, _ = tmp_tehm
    with pytest.raises(TypeError, match="sink must be callable"):
        apply_localized_update_shadow(retain(), conn, staging_artifact_sink=True)


def test_sink_exception_closes_staging(tmp_tehm, monkeypatch):
    conn, _, _ = tmp_tehm;module = importlib.import_module("tehm.evolution.apply_update")
    real_copy = module._staging_copy;stages = []
    def copy(source):
        staged = real_copy(source);stages.append(staged);return staged
    def fail(_): raise RuntimeError("artifact collector failed")
    monkeypatch.setattr(module, "_staging_copy", copy)
    with pytest.raises(RuntimeError, match="collector failed"):
        apply_localized_update_shadow(retain(), conn, staging_artifact_sink=fail)
    with pytest.raises(sqlite3.ProgrammingError): stages[0].execute("SELECT 1")


def test_sink_cannot_hide_source_side_effect(tmp_tehm):
    conn, _, _ = tmp_tehm;before = "\n".join(conn.iterdump())
    conn.execute("SAVEPOINT sink_negative")
    try:
        def side_effect(_): conn.execute("CREATE TABLE forbidden_sink_side_effect (x TEXT)")
        with pytest.raises(ShadowUpdateError, match="source TEHM connection changed"):
            apply_localized_update_shadow(retain(), conn, staging_artifact_sink=side_effect)
    finally:
        conn.execute("ROLLBACK TO sink_negative");conn.execute("RELEASE sink_negative")
    assert "\n".join(conn.iterdump()) == before


def test_causal_add_never_falls_back_to_rule_crystallization(tmp_tehm, monkeypatch):
    conn, _, _ = tmp_tehm;module = importlib.import_module("tehm.evolution.apply_update")
    called = []
    monkeypatch.setattr(module, "crystallize_affected_groups", lambda *a, **k: called.append(True))
    proposed = replace(retain(), learner_eligible=True, update_target="UPDATE_CAUSAL_KNOWLEDGE",
        candidate_targets=("UPDATE_CAUSAL_KNOWLEDGE",), operation="ADD", failure_type="CAPABILITY_GAP")
    with pytest.raises(ShadowUpdateError, match="source witness"):
        apply_localized_update_shadow(proposed, conn)
    assert not called
