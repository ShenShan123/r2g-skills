"""P4 failure attribution and localized update-plan tests."""
from __future__ import annotations

from pathlib import Path

from tehm.canonical.capture import capture
from tehm.evolution import (
    MemoryFailureAttributionReceipt, LocalizedUpdatePlan,
    attribute_failure, observe_transition, plan_localized_update,
)
from tehm.evolution.value_receipts import ExperienceValueReceipt
from tehm.rtl.rtl_evidence import build_rtl_execution_record


PROJECT = Path(__file__).resolve().parent / "fixtures" / "rtl_projects" / "req_ack_bug"


def _capture(tmp_tehm, *, record_id: str, verified: bool = True,
             learner: bool = True):
    conn, store, _ = tmp_tehm
    record = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    record.record_id = record_id
    if verified:
        record.verification.update({
            "verdict": "PASS", "oracle_type": "TARGET_TEST",
            "scope": "fixture:target", "confidence_tier": "T",
            "oracle_complete": True, "evidence_refs": [f"fixture-{record_id}"],
        })
    return conn, capture(
        conn, store, record, dataset_campaign_id="live",
        dataset_split="training", dataset_learner_eligible=learner).transition_id


def _value(*, transition_id: str, layers=("CAUSAL",), novelty=1.0):
    return ExperienceValueReceipt(
        transition_id=transition_id, campaign_id="live", novelty=novelty,
        severity=0.0, capability_gap=0.0, causal_discrimination=0.0,
        surprise=0.0, counterexample=0.0, memory_interference=0.0,
        redundancy=0.0, value_score=0.8, priority="P1_HIGH",
        update_layers=layers, reasons=("NOVEL_MECHANISM",))


def test_unverified_transition_is_attributed_to_verification(tmp_tehm):
    conn, transition_id = _capture(
        tmp_tehm, record_id="attribution-unknown", verified=False, learner=False)
    receipt = attribute_failure(conn, transition_id=transition_id)
    assert receipt.failure_type == "VERIFICATION_FAILURE"
    assert receipt.recommended_update_layers == ("UPDATE_NONE",)
    assert receipt.confidence == 1.0
    assert receipt.evidence_refs == (transition_id,)


def test_memory_interference_forces_non_runtime_update_target():
    value = _value(transition_id="transition-a")
    attribution = MemoryFailureAttributionReceipt(
        activation_id="activation-a", transition_id="transition-a",
        failure_type="MEMORY_INTERFERENCE", blamed_objects=("rule:r1",),
        evidence_refs=("transition-a", "activation-a"), confidence=1.0,
        recommended_update_layers=("UPDATE_STATE_RELATION",
                                   "UPDATE_CAUSAL_KNOWLEDGE", "UPDATE_ASSET"))
    plan = plan_localized_update(value, attribution, evidence_refs=("transition-a",))
    assert plan.update_target == "UPDATE_STATE_RELATION"
    assert plan.operation == "INVALIDATE"
    assert plan.shadow_only is True
    assert LocalizedUpdatePlan.from_dict(plan.to_dict()).to_dict() == plan.to_dict()


def test_observer_persists_replayable_p4_receipts_without_extra_event_prefix(tmp_tehm):
    conn, transition_id = _capture(tmp_tehm, record_id="attribution-observer")
    first = observe_transition(conn, transition_id)
    assert first.state_resolution is not None
    assert first.failure_attribution is not None
    assert first.localized_update_plan is not None
    assert first.localized_update_plan.shadow_only is True
    second = observe_transition(conn, transition_id)
    assert second.to_dict() == first.to_dict()
    assert len(first.events) == 5
