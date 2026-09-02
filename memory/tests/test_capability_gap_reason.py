"""Revision3 non-P12 CAPABILITY_GAP admission tests."""
from __future__ import annotations

import pytest

from contracts import MemoryRoutingDecision
from tehm.assets.receipts import CapabilityGapReceipt
from tehm.evolution.admission import admit_evolution_reason
from tehm.evolution.capability_gap import (
    CapabilityGapProposalError, propose_capability_gap_expansion,
)
from tehm.evolution.reason_derivation import (
    EvolutionReasonDerivationError, derive_capability_gap_reason,
)


def _gap(**coverage) -> CapabilityGapReceipt:
    current = {
        "promoted_asset": False, "promoted_rule": False,
        "successful_action_family": False, "failures": 0,
        "initial_failure_evidence": 2, "failure_evidence": 2,
    }
    current.update(coverage)
    return CapabilityGapReceipt(
        gap_id="gap-test-r3", mechanism_family="HANDSHAKE_COMPLETION",
        compatibility_profile="rtl.fsm.single_guard.v1",
        evidence_transitions=("transition-a", "transition-b"),
        evidence_lineages=("lineage-a", "lineage-b"),
        missing_asset_types=("RTL_REWRITE_TEMPLATE",),
        reason="repeated_unsupported_mechanism+structural_coverage_gap",
        current_action_coverage=current, confidence=1.0)


def _route(gap: CapabilityGapReceipt) -> MemoryRoutingDecision:
    return MemoryRoutingDecision(
        decision="NO_SKILL", resolved_state_id="gap-state:" + gap.gap_id,
        selected_rule_ids=(), selected_path_ids=(), selected_asset_ids=(),
        applicability={}, causal_support={}, risk={}, abstain_reasons=(),
        no_memory_budget=1, memory_budget=0, no_skill_reason="NO_MATCH")


def test_capability_gap_admits_without_paired_counterfactual():
    gap = _gap()
    route = _route(gap)
    derivation = derive_capability_gap_reason(
        gap, campaign_id="r3-capability-gap", case_id="case-gap",
        failure_transition_ids=gap.evidence_transitions, routing=route)
    assert derivation is not None
    admission = admit_evolution_reason(
        derivation, campaign_id="r3-capability-gap", learner_eligible=True,
        capability_gap=gap, failure_transition_ids=gap.evidence_transitions,
        routing=route)
    assert admission.admitted is True
    assert "paired_counterfactual" not in admission.required_evidence
    proposal = propose_capability_gap_expansion(
        gap, derivation, admission,
        failure_transition_ids=gap.evidence_transitions, routing=route)
    assert proposal.operation == "ADD"
    assert proposal.canonical_memory_mutation == "none"
    assert proposal.production_runtime_eligible is False
    assert proposal.from_dict({
        **proposal.to_dict(), "proposal_id": proposal.proposal_id,
        "proposal_digest": proposal.proposal_digest}) == proposal


def test_capability_gap_blocks_covered_or_under_supported_gap():
    covered = _gap(promoted_asset=True)
    assert derive_capability_gap_reason(
        covered, campaign_id="r3-capability-gap", case_id="case-gap",
        routing=_route(covered)) is None
    under_supported = _gap(failure_evidence=1, initial_failure_evidence=1)
    assert derive_capability_gap_reason(
        under_supported, campaign_id="r3-capability-gap", case_id="case-gap",
        routing=_route(under_supported)) is None


def test_capability_gap_rejects_failure_ids_outside_typed_gap():
    gap = _gap()
    with pytest.raises(EvolutionReasonDerivationError, match="outside"):
        derive_capability_gap_reason(
            gap, campaign_id="r3-capability-gap", case_id="case-gap",
            failure_transition_ids=("not-in-gap", "transition-a"),
            routing=_route(gap))

    derivation = derive_capability_gap_reason(
        gap, campaign_id="r3-capability-gap", case_id="case-gap",
        routing=_route(gap))
    admission = admit_evolution_reason(
        derivation, campaign_id="r3-capability-gap", learner_eligible=True,
        capability_gap=gap, routing=_route(gap))
    assert admission.admitted is True
    with pytest.raises(CapabilityGapProposalError, match="does not match"):
        propose_capability_gap_expansion(
            gap, derivation, admission,
            failure_transition_ids=("not-in-gap", "transition-a"),
            routing=_route(gap))
