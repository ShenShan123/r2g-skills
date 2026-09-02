"""State-shift observations become typed proposals, never automatic mutation."""
from __future__ import annotations

import json

import pytest

from tehm.evolution import (
    StateShiftEvolutionError,
    StateShiftEvolutionProposal,
    append_state_shift_observation,
    append_routed_state_shift_observation,
    load_state_shift_observations,
    propose_repeated_state_shift,
    propose_repeated_state_shift_from_events,
    state_shift_proposal_to_localized_plan,
)
from tehm.knowledge import MechanismKnowledge
from tehm.state import build_support_envelope, evaluate_state_shift
from contracts import MemoryRoutingDecision


def _knowledge() -> MechanismKnowledge:
    return MechanismKnowledge(
        knowledge_id="shift-evolution-k", version=1,
        mechanism_family="HANDSHAKE_COMPLETION",
        compatibility_profile="rtl.fsm.single_guard.v1",
        antecedent={"failure": "completion_not_observed"},
        intervention={"family": "GUARD_RESTORE"},
        mediated_effects=({"effect": "legal_transition"},),
        expected_outcome={"outcome": "PASS"},
        positive_applicability=({
            "mechanism_family": "HANDSHAKE_COMPLETION",
            "compatibility_profile": "rtl.fsm.single_guard.v1",
        },),
        negative_applicability=(), preserved_obligations=("target_trace_pass",),
        known_failure_modes=(), causal_path_ids=("path-shift-evolution",),
        evidence_level="L2_CONTROLLED_INTERVENTION",
        support_lineages=("lineage-shift-evolution",),
    )


def _receipts():
    knowledge = _knowledge()
    envelope = build_support_envelope(knowledge, (), ({
        "transition_id": "support-transition", "split": "training",
        "learner_eligible": True, "verdict": "PASS", "oracle_complete": True,
        "platform": "sky130",
    },))
    return tuple(evaluate_state_shift(
        {"mechanism_family": "HANDSHAKE_COMPLETION",
         "compatibility_profile": "rtl.fsm.single_guard.v1",
         "platform": platform},
        {"resolution_id": resolution}, knowledge, envelope)
        for resolution, platform in (("resolution-a", "asap7"),
                                     ("resolution-b", "asap7")))


def test_repeated_state_shift_proposes_specialization_without_mutation():
    receipts = _receipts()
    proposal = propose_repeated_state_shift(
        receipts, knowledge_object_id=receipts[0].knowledge_object_id,
        transition_ids=("transition-a", "transition-b"),
        no_memory_outcomes=("PASS", "PARTIAL"),
        historical_memory_outcomes=("PASS", "FAIL"),
        evidence_refs=("transition-a", "transition-b", *(
            item.receipt_id for item in receipts)),
    )
    assert proposal.operation == "SPECIALIZE"
    assert proposal.evolution_reason == "KNOWLEDGE_SPECIALIZATION"
    assert proposal.shifted_dimensions == ("flow_shift",)
    assert proposal.shadow_only is True
    replay = StateShiftEvolutionProposal.from_dict({
        **proposal.to_dict(), "proposal_digest": proposal.proposal_digest})
    assert replay == proposal


def test_state_shift_proposal_conversion_requires_p13_trigger():
    receipts = _receipts()
    proposal = propose_repeated_state_shift(
        receipts, knowledge_object_id=receipts[0].knowledge_object_id,
        transition_ids=("transition-a", "transition-b"),
        no_memory_outcomes=("PASS", "PASS"),
        historical_memory_outcomes=("PASS", "FAIL"),
        evidence_refs=("transition-a", "transition-b"),
    )
    with pytest.raises(StateShiftEvolutionError, match="p12_trigger_digest"):
        state_shift_proposal_to_localized_plan(proposal, campaign_id="live")
    plan = state_shift_proposal_to_localized_plan(
        proposal, campaign_id="live", p12_trigger_digest="sha256:p12-trigger")
    assert plan.update_target == "UPDATE_CAUSAL_KNOWLEDGE"
    assert plan.operation == "SPECIALIZE"
    assert plan.failure_type == "STATE_SHIFT"
    assert "sha256:p12-trigger" in plan.evidence_refs
    assert plan.knowledge_refs == (receipts[0].knowledge_object_id,)


def test_repeated_safe_shift_proposes_support_envelope_revision():
    receipts = _receipts()
    proposal = propose_repeated_state_shift(
        receipts, knowledge_object_id=receipts[0].knowledge_object_id,
        transition_ids=("transition-a", "transition-b"),
        no_memory_outcomes=("PASS", "PASS"),
        historical_memory_outcomes=("PASS", "PARTIAL"),
        evidence_refs=("transition-a", "transition-b"),
    )
    assert proposal.operation == "REVISE"
    assert proposal.evolution_reason == "SUPPORT_ENVELOPE_EXPANSION"


def test_unsafe_current_execution_is_retained_and_split_is_explicit():
    receipts = _receipts()
    retained = propose_repeated_state_shift(
        receipts, knowledge_object_id=receipts[0].knowledge_object_id,
        transition_ids=("transition-a", "transition-b"),
        no_memory_outcomes=("FAIL", "PASS"),
        historical_memory_outcomes=("PASS", "PASS"), evidence_refs=("w",),
    )
    assert retained.operation == "RETAIN"
    with pytest.raises(StateShiftEvolutionError, match="partition evidence"):
        propose_repeated_state_shift(
            receipts, knowledge_object_id=receipts[0].knowledge_object_id,
            transition_ids=("transition-a", "transition-b"),
            no_memory_outcomes=("PASS", "PASS"),
            historical_memory_outcomes=("PASS", "PASS"), evidence_refs=("w",),
            requested_operation="SPLIT",
        )
    split = propose_repeated_state_shift(
        receipts, knowledge_object_id=receipts[0].knowledge_object_id,
        transition_ids=("transition-a", "transition-b"),
        no_memory_outcomes=("PASS", "PASS"),
        historical_memory_outcomes=("PASS", "PASS"), evidence_refs=("w",),
        requested_operation="SPLIT", partition_evidence_refs=("branch-a", "branch-b"),
    )
    assert split.operation == "SPLIT"


def test_state_shift_observation_event_is_replayable_and_tamper_evident(tmp_tehm):
    conn, _, _ = tmp_tehm
    receipt = _receipts()[0]
    event = append_state_shift_observation(
        conn, receipt, transition_id="audit-transition", campaign_id="audit",
        learner_eligible=False, created_at="2026-09-01T00:00:00Z")
    assert event.event_type == "STATE_SHIFT_OBSERVED"
    loaded = load_state_shift_observations(conn, campaign_id="audit")
    assert loaded == ((event, receipt),)
    replay = append_state_shift_observation(
        conn, receipt, transition_id="audit-transition", campaign_id="audit",
        learner_eligible=False, created_at="2026-09-01T00:00:00Z")
    assert replay == event
    row = conn.execute(
        "SELECT payload_json FROM tehm_memory_events WHERE event_id=?",
        (event.event_id,),
    ).fetchone()
    payload = json.loads(row["payload_json"])
    payload["no_skill_reason"] = "NO_MATCH"
    conn.execute(
        "UPDATE tehm_memory_events SET payload_json=? WHERE event_id=?",
        (json.dumps(payload), event.event_id),
    )
    conn.commit()
    with pytest.raises(ValueError, match="event chain is invalid|event_digest|payload"):
        load_state_shift_observations(conn, campaign_id="audit")


def test_routed_state_shift_event_requires_matching_no_skill_receipt(tmp_tehm):
    conn, _, _ = tmp_tehm
    receipt = _receipts()[0]
    route = MemoryRoutingDecision(
        decision="NO_SKILL", resolved_state_id=receipt.current_resolution_id,
        selected_rule_ids=(), selected_path_ids=(), selected_asset_ids=(),
        applicability={"state_shift_status": "SHIFTED"},
        causal_support={"status": "SUPPORTED"}, risk={"state_shift_status": "SHIFTED"},
        abstain_reasons=("state_shift",), no_memory_budget=1, memory_budget=0,
        no_skill_reason="STATE_SHIFT", state_shift_receipt_id=receipt.receipt_id)
    event = append_routed_state_shift_observation(
        conn, receipt, route, transition_id="routed-transition", campaign_id="audit",
        learner_eligible=False, created_at="2026-09-01T00:00:00Z")
    payload = json.loads(conn.execute(
        "SELECT payload_json FROM tehm_memory_events WHERE event_id=?",
        (event.event_id,)).fetchone()[0])
    assert payload["routing_decision"]["routing_receipt_id"] == route.routing_receipt_id
    assert payload["routing_decision"]["decision_digest"] == route.decision_digest
    assert load_state_shift_observations(conn, campaign_id="audit") == ((event, receipt),)

    with pytest.raises(ValueError, match="NO_SKILL/STATE_SHIFT"):
        append_routed_state_shift_observation(
            conn, receipt,
            MemoryRoutingDecision(
                decision="NO_SKILL", resolved_state_id=receipt.current_resolution_id,
                selected_rule_ids=(), selected_path_ids=(), selected_asset_ids=(),
                applicability={}, causal_support={}, risk={}, abstain_reasons=(),
                no_memory_budget=1, memory_budget=0, no_skill_reason="NO_MATCH"),
            transition_id="other-transition", campaign_id="audit",
            learner_eligible=False)


def test_event_bound_proposal_replays_pairing_and_rejects_missing_witness(tmp_tehm):
    conn, _, _ = tmp_tehm
    receipts = _receipts()
    events = tuple(
        append_state_shift_observation(
            conn, receipt, transition_id=transition_id, campaign_id="audit",
            learner_eligible=False, created_at=f"2026-09-01T00:00:0{index}Z")
        for index, (receipt, transition_id) in enumerate(
            zip(receipts, ("transition-a", "transition-b")))
    )
    refs = tuple(ref for event, receipt in zip(events, receipts)
                 for ref in (event.event_digest, receipt.receipt_id))
    proposal = propose_repeated_state_shift_from_events(
        conn, campaign_id="audit", knowledge_object_id=receipts[0].knowledge_object_id,
        transition_ids=("transition-a", "transition-b"),
        no_memory_outcomes=("PASS", "PASS"),
        historical_memory_outcomes=("PASS", "FAIL"), evidence_refs=refs)
    assert proposal.operation == "RETAIN"
    assert proposal.evolution_reason == "NOT_LEARNER_ELIGIBLE"
    with pytest.raises(StateShiftEvolutionError, match="witness events and receipts"):
        propose_repeated_state_shift_from_events(
            conn, campaign_id="audit", knowledge_object_id=receipts[0].knowledge_object_id,
            transition_ids=("transition-a", "transition-b"),
            no_memory_outcomes=("PASS", "PASS"),
            historical_memory_outcomes=("PASS", "FAIL"), evidence_refs=("manual",))


def test_state_shift_observation_rejects_transferable_receipt():
    knowledge = _knowledge()
    envelope = build_support_envelope(knowledge, (), ({
        "transition_id": "support-transition", "split": "training",
        "learner_eligible": True, "verdict": "PASS", "oracle_complete": True,
        "platform": "sky130",
    },))
    receipt = evaluate_state_shift(
        {"mechanism_family": "HANDSHAKE_COMPLETION",
         "compatibility_profile": "rtl.fsm.single_guard.v1", "platform": "sky130"},
        {"resolution_id": "resolution"}, knowledge, envelope)
    assert receipt.reason == "NO_SHIFT"
    with pytest.raises(ValueError, match="non-transferable"):
        # The event bridge must not turn a transferable observation into a
        # state-shift teaching signal.
        append_state_shift_observation(
            __import__("sqlite3").connect(":memory:"), receipt,
            transition_id="t", campaign_id="c", learner_eligible=False)
