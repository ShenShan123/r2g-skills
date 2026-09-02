"""Revision2 reason-aware NO_SKILL and training-only support envelopes."""
from __future__ import annotations

import pytest

from contracts import MemoryQuery, MemoryRoutingDecision
from tehm.knowledge import MechanismKnowledge
from tehm.state import (
    ResolvedMemoryState, StateShiftReceipt, SupportEnvelopeError,
    build_support_envelope, evaluate_state_shift,
)
from tehm.retrieval import memory_router
from tehm.retrieval.memory_router import route_memory


def _knowledge() -> MechanismKnowledge:
    return MechanismKnowledge(
        knowledge_id="shift-k", version=1, mechanism_family="HANDSHAKE_COMPLETION",
        compatibility_profile="rtl.fsm.single_guard.v1", antecedent={"failure": "x"},
        intervention={"family": "GUARD_RESTORE"}, mediated_effects=({"effect": "y"},),
        expected_outcome={"outcome": "PASS"}, positive_applicability=({
            "mechanism_family": "HANDSHAKE_COMPLETION",
            "compatibility_profile": "rtl.fsm.single_guard.v1",
        },), negative_applicability=(), preserved_obligations=("TARGET",),
        known_failure_modes=(), causal_path_ids=("path-shift",),
        evidence_level="L2_CONTROLLED_INTERVENTION", support_lineages=("lineage-shift",),
    )


def test_support_envelope_training_only_and_replayable():
    envelope = build_support_envelope(_knowledge(), (), ({
        "transition_id": "t-shift", "split": "training", "learner_eligible": True,
        "verdict": "PASS", "oracle_complete": True, "platform": "sky130",
    },))
    assert envelope.training_only is True
    assert envelope.source_transition_ids == ("t-shift",)
    assert envelope.from_dict(envelope.to_dict()) == envelope
    with pytest.raises(SupportEnvelopeError, match="training split"):
        build_support_envelope(_knowledge(), (), ({
            "transition_id": "heldout", "split": "heldout", "learner_eligible": False,
            "verdict": "PASS", "oracle_complete": True, "platform": "sky130",
        },))


def test_support_envelope_requires_explicit_training_oracle_binding():
    base = {"transition_id": "t-shift", "platform": "sky130"}
    with pytest.raises(SupportEnvelopeError, match="requires training split"):
        build_support_envelope(_knowledge(), (), (base,))
    ineligible = {
        **base, "split": "training", "learner_eligible": False,
        "verdict": "PASS", "oracle_complete": True,
    }
    with pytest.raises(SupportEnvelopeError, match="learner-eligible"):
        build_support_envelope(_knowledge(), (), (ineligible,))
    incomplete = {
        **base, "split": "training", "learner_eligible": True,
        "verdict": "PASS", "oracle_complete": False,
    }
    with pytest.raises(SupportEnvelopeError, match="complete oracle"):
        build_support_envelope(_knowledge(), (), (incomplete,))
    no_id = {
        "split": "training", "learner_eligible": True,
        "verdict": "PASS", "oracle_complete": True, "platform": "sky130",
    }
    with pytest.raises(SupportEnvelopeError, match="requires transition_id"):
        build_support_envelope(_knowledge(), (), (no_id,))
    with pytest.raises(SupportEnvelopeError, match="verified training transitions"):
        build_support_envelope(_knowledge(), (), ())


def test_state_shift_detects_flow_change_and_replays():
    knowledge = _knowledge()
    envelope = build_support_envelope(knowledge, (), ({
        "transition_id": "t-shift", "split": "training", "learner_eligible": True,
        "verdict": "PASS", "oracle_complete": True, "platform": "sky130",
    },))
    receipt = evaluate_state_shift(
        {"mechanism_family": "HANDSHAKE_COMPLETION",
         "compatibility_profile": "rtl.fsm.single_guard.v1", "platform": "asap7"},
        {"resolution_id": "resolution-shift"}, knowledge, envelope)
    assert receipt.reason == "STATE_SHIFT"
    assert receipt.transferable is False
    assert receipt.flow_shift == 1.0
    assert "flow_shift" in receipt.shifted_dimensions
    assert StateShiftReceipt.from_dict(receipt.to_dict()) == receipt


def test_no_skill_contract_maps_legacy_reason_to_no_match():
    decision = MemoryRoutingDecision(
        decision="NO_SKILL", resolved_state_id="state", selected_rule_ids=(),
        selected_path_ids=(), selected_asset_ids=(), applicability={},
        causal_support={}, risk={}, abstain_reasons=("legacy",),
        no_memory_budget=3, memory_budget=0)
    assert decision.no_skill_reason == "NO_MATCH"
    assert MemoryRoutingDecision.from_dict(decision.to_dict()) == decision


def test_fresh_router_emits_reason_aware_no_match(tmp_tehm):
    conn, _, _ = tmp_tehm
    decision = route_memory(conn, MemoryQuery(query_plan={
        "mechanism_family": "HANDSHAKE_COMPLETION",
        "compatibility_profile": "rtl.fsm.single_guard.v1",
    }))
    assert decision.decision == "NO_SKILL"
    assert decision.no_skill_reason == "NO_MATCH"


def test_risk_reason_requires_typed_evidence(tmp_tehm):
    conn, _, _ = tmp_tehm
    decision = route_memory(conn, MemoryQuery(query_plan={
        "mechanism_family": "HANDSHAKE_COMPLETION",
        "risk_evidence": {"expected_utility": -0.5, "evidence_refs": ["ab-1"]},
    }))
    # No validated claim exists, so coverage takes precedence over a risk
    # signal; this remains a reason-aware NO_MATCH rather than guessed RISK.
    assert decision.no_skill_reason == "NO_MATCH"


def test_router_emits_state_shift_reason_and_preserves_no_memory_arm(
        tmp_tehm, monkeypatch):
    conn, _, _ = tmp_tehm
    knowledge = _knowledge()
    envelope = build_support_envelope(knowledge, (), ({
        "transition_id": "t-shift", "split": "training", "learner_eligible": True,
        "verdict": "PASS", "oracle_complete": True, "platform": "sky130",
    },))
    state = ResolvedMemoryState(
        resolution_id="resolution-shift", input_memory_digest="sha256:input",
        scope={}, active_rules=(), active_causal_paths=("path-shift",),
        active_knowledge_claims=(), active_assets=(), active_capabilities=(),
        suppressed=(), unresolved_conflicts=(), relation_ids=(),
        shadow_relation_ids=(), resolution_digest="sha256:resolution",
        resolver_version="test",
    )
    monkeypatch.setattr(memory_router, "resolve_current_state", lambda *args, **kwargs: state)
    monkeypatch.setattr(
        memory_router, "_knowledge_for_state",
        lambda *args, **kwargs: ([{"claim": knowledge, "path_ids": ("path-shift",)}], (), (), ()),
    )
    decision = route_memory(conn, MemoryQuery(query_plan={
        "mechanism_family": "HANDSHAKE_COMPLETION",
        "compatibility_profile": "rtl.fsm.single_guard.v1",
        "support_envelope": envelope.to_dict(),
        "current_state_facts": {
            "mechanism_family": "HANDSHAKE_COMPLETION",
            "compatibility_profile": "rtl.fsm.single_guard.v1",
            "platform": "asap7",
        },
    }), memory_budget=1)
    assert decision.decision == "NO_SKILL"
    assert decision.no_skill_reason == "STATE_SHIFT"
    assert decision.state_shift_receipt_id.startswith("state_shift_")
    assert decision.state_shift_receipt is not None
    replay = MemoryRoutingDecision.from_dict({
        **decision.to_dict(), "decision_digest": decision.decision_digest})
    assert replay == decision
    tampered = {**decision.to_dict(), "decision_digest": decision.decision_digest}
    tampered["state_shift_receipt"] = {
        **tampered["state_shift_receipt"], "reason": "NO_SHIFT"}
    with pytest.raises(ValueError, match="malformed|digest mismatch|state_shift_receipt"):
        MemoryRoutingDecision.from_dict(tampered)
    assert decision.memory_budget == 0
    assert decision.no_memory_budget >= 1
