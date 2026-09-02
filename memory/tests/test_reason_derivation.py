"""Revision3 typed evolution-reason derivation tests."""
from __future__ import annotations

import hashlib

import pytest

from contracts import MemoryRoutingDecision
from tehm.evaluation.candidate_executor import P12_ARMS, execute_paired_candidates
from tehm.evolution.reason_derivation import (
    EvolutionReasonDerivationError, EvolutionReasonDerivationReceipt,
    derive_memory_interference_reason, derive_state_shift_reason,
    p13_reason_receipt_from_derivations,
)
from tehm.evolution.admission import (
    EvolutionAdmissionReceipt, admit_evolution_reason,
)
from tehm.ids import stable_dumps
from tehm.retrieval.structured_candidate import StructuredRepairCandidate
from tehm.state import StateShiftReceipt


def _shift(*, transferable: bool = False, reason: str = "STATE_SHIFT") -> StateShiftReceipt:
    payload = {
        "version": "state-shift-v0.1",
        "current_resolution_id": "resolution-r3",
        "knowledge_object_id": "knowledge-r3@1",
        "support_envelope_digest": "sha256:support-r3",
        "structural_shift": 0.0, "mechanism_shift": 0.0,
        "flow_shift": 1.0, "constraint_shift": 0.0,
        "oracle_shift": 0.0, "history_shift": 0.0,
        "aggregate_shift": 1.0,
        "shifted_dimensions": ["flow_shift"],
        "transferable": transferable, "reason": reason,
        "evidence_refs": ["transition-r3"],
    }
    if transferable:
        payload["flow_shift"] = 0.0
        payload["aggregate_shift"] = 0.0
        payload["shifted_dimensions"] = []
    return StateShiftReceipt(
        current_resolution_id=payload["current_resolution_id"],
        knowledge_object_id=payload["knowledge_object_id"],
        support_envelope_digest=payload["support_envelope_digest"],
        structural_shift=payload["structural_shift"],
        mechanism_shift=payload["mechanism_shift"],
        flow_shift=payload["flow_shift"],
        constraint_shift=payload["constraint_shift"],
        oracle_shift=payload["oracle_shift"],
        history_shift=payload["history_shift"],
        aggregate_shift=payload["aggregate_shift"],
        shifted_dimensions=tuple(payload["shifted_dimensions"]),
        transferable=payload["transferable"], reason=payload["reason"],
        evidence_refs=tuple(payload["evidence_refs"]),
        replay_digest="sha256:" + hashlib.sha256(
            stable_dumps(payload).encode()).hexdigest(),
    )


def _shift_route(shift: StateShiftReceipt) -> MemoryRoutingDecision:
    return MemoryRoutingDecision(
        decision="NO_SKILL", resolved_state_id=shift.current_resolution_id,
        selected_rule_ids=(), selected_path_ids=(), selected_asset_ids=(),
        applicability={}, causal_support={}, risk={}, abstain_reasons=(),
        no_memory_budget=2, memory_budget=0, no_skill_reason="STATE_SHIFT",
        state_shift_receipt_id=shift.receipt_id,
        state_shift_receipt=shift.to_dict(),
    )


def _candidate() -> StructuredRepairCandidate:
    return StructuredRepairCandidate(
        candidate_id="r3-candidate", resolved_state_id="resolution-r3",
        knowledge_object_id="knowledge-r3@1", causal_path_ids=("path-r3",),
        asset_id="asset-r3", action_family="GUARD_STRENGTHEN",
        concrete_action={"domain": "rtl.GUARD_STRENGTHEN",
                         "transformation_family": "GUARD_STRENGTHEN",
                         "payload": {"module": "top", "add_condition": "ack"}},
        applicability_receipt_id="app-r3", binding_receipt_id="binding-r3",
        obligations=("TARGET_PASS",), evidence_level="L3_REPLICATED_EFFECT",
        authority={"eligible": True}, risk={},
        provenance={"evaluation_only": True, "source": "test"})


def test_state_shift_derivation_requires_nontransferable_typed_route():
    shift = _shift()
    receipt = derive_state_shift_reason(
        shift, campaign_id="r3-campaign", case_id="r3-case",
        routing=_shift_route(shift), lineage_id="lineage-r3")
    assert isinstance(receipt, EvolutionReasonDerivationReceipt)
    assert receipt.reason == "STATE_SHIFT"
    assert receipt.derivation_mode == "EX_ANTE"
    assert receipt.detector_name == "state_shift_receipt_adapter"
    assert EvolutionReasonDerivationReceipt.from_dict({
        **receipt.to_dict(), "receipt_digest": receipt.receipt_digest,
        "receipt_id": receipt.receipt_id}) == receipt
    assert derive_state_shift_reason(
        _shift(transferable=True, reason="NO_SHIFT"), campaign_id="r3-campaign",
        case_id="r3-case", routing=_shift_route(shift), lineage_id="lineage-r3") is None


def test_state_shift_derivation_rejects_route_or_receipt_tampering():
    shift = _shift()
    route = _shift_route(shift)
    bad = MemoryRoutingDecision(
        decision=route.decision, resolved_state_id=route.resolved_state_id,
        selected_rule_ids=route.selected_rule_ids,
        selected_path_ids=route.selected_path_ids,
        selected_asset_ids=route.selected_asset_ids,
        applicability=route.applicability, causal_support=route.causal_support,
        risk=route.risk, abstain_reasons=route.abstain_reasons,
        no_memory_budget=route.no_memory_budget, memory_budget=route.memory_budget,
        no_skill_reason="NO_MATCH")
    with pytest.raises(EvolutionReasonDerivationError, match="route binding"):
        derive_state_shift_reason(
            shift, campaign_id="r3-campaign", case_id="r3-case",
            routing=bad, lineage_id="lineage-r3")
    with pytest.raises(EvolutionReasonDerivationError, match="invalid"):
        derive_state_shift_reason(
            {**shift.to_dict(), "flow_shift": 0.2}, campaign_id="r3-campaign",
            case_id="r3-case", routing=route, lineage_id="lineage-r3")


def test_memory_interference_derives_only_complete_pass_fail_counterfactual():
    candidate = _candidate()

    def oracle(current, _case, _budget):
        if current is None:
            return {"compile_result": "PASS", "functional_result": "PASS",
                    "signoff_result": "PASS", "toolchain_digest": "sha256:tool",
                    "oracle_digest": "sha256:oracle"}
        return {"compile_result": "PASS", "functional_result": "FAIL",
                "signoff_result": "FAIL", "created_regressions": ["target"],
                "toolchain_digest": "sha256:tool", "oracle_digest": "sha256:oracle"}

    paired = execute_paired_candidates(
        {"case_id": "r3-interference", "toolchain_digest": "sha256:tool"},
        {arm: None if arm == "NO_MEMORY" else candidate for arm in P12_ARMS},
        oracle=oracle, budget=3, routing_decision="CONSIDER",
        lineage_id="lineage-interference")
    receipt = derive_memory_interference_reason(
        paired, campaign_id="r3-campaign")
    assert receipt is not None
    assert receipt.reason == "MEMORY_INTERFERENCE"
    assert receipt.derivation_mode == "EX_POST_COUNTERFACTUAL"
    assert receipt.input_digests[0] == paired.receipt_digest
    assert EvolutionReasonDerivationReceipt.from_dict({
        **receipt.to_dict(), "receipt_digest": receipt.receipt_digest}) == receipt

    tampered = {**receipt.to_dict(), "canonical_memory_mutation": "write"}
    with pytest.raises(EvolutionReasonDerivationError, match="cannot mutate"):
        EvolutionReasonDerivationReceipt.from_dict(tampered)


def test_memory_interference_never_uses_unknown_or_missing_lineage():
    candidate = _candidate()

    def unknown_oracle(_current, _case, _budget):
        return {"compile_result": "PASS", "functional_result": "UNKNOWN",
                "signoff_result": "UNKNOWN", "toolchain_digest": "sha256:tool",
                "oracle_digest": "sha256:oracle"}

    paired = execute_paired_candidates(
        {"case_id": "r3-unknown", "toolchain_digest": "sha256:tool"},
        {arm: None if arm == "NO_MEMORY" else candidate for arm in P12_ARMS},
        oracle=unknown_oracle, lineage_id="lineage-unknown")
    with pytest.raises(EvolutionReasonDerivationError, match="complete oracle"):
        derive_memory_interference_reason(paired, campaign_id="r3-campaign")


def test_typed_derivations_aggregate_into_p13_reason_envelope():
    shift = _shift()
    derivation = derive_state_shift_reason(
        shift, campaign_id="r3-campaign", case_id="r3-case",
        routing=_shift_route(shift), lineage_id="lineage-r3")
    reason = p13_reason_receipt_from_derivations(
        {"r3-case": (derivation,)}, campaign_id="r3-campaign",
        cohort_receipt_digest="sha256:r3-cohort")
    assert reason.label_source == "typed-detector:state_shift_receipt_adapter"
    assert reason.evolution_reasons == {"r3-case": ("STATE_SHIFT",)}
    assert reason.case_evidence_refs["r3-case"][0]["id"] == derivation.receipt_id
    assert reason.to_dict()["canonical_memory_mutation"] == "none"


def test_state_shift_admission_requires_typed_reason_and_real_pair():
    shift = _shift()
    route = _shift_route(shift)
    candidate = _candidate()

    def oracle(_current, _case, _budget):
        return {"compile_result": "PASS", "functional_result": "PASS",
                "signoff_result": "PASS", "toolchain_digest": "sha256:tool",
                "oracle_digest": "sha256:oracle"}

    paired = execute_paired_candidates(
        {"case_id": "r3-case", "toolchain_digest": "sha256:tool"},
        {arm: None if arm == "NO_MEMORY" else candidate for arm in P12_ARMS},
        oracle=oracle, routing_decision="NO_SKILL",
        no_skill_reason="STATE_SHIFT", state_shift_receipt_id=shift.receipt_id,
        routing_receipt_id=route.routing_receipt_id, lineage_id="lineage-r3")
    derivation = derive_state_shift_reason(
        shift, campaign_id="r3-campaign", case_id="r3-case",
        routing=route, lineage_id="lineage-r3")
    admission = admit_evolution_reason(
        derivation, campaign_id="r3-campaign", learner_eligible=True,
        paired=paired, state_shift=shift, routing=route)
    assert isinstance(admission, EvolutionAdmissionReceipt)
    assert admission.admitted is True
    assert set(admission.required_evidence) <= set(admission.satisfied_evidence)
    assert EvolutionAdmissionReceipt.from_dict({
        **admission.to_dict(), "receipt_digest": admission.receipt_digest,
        "receipt_id": admission.receipt_id}) == admission

    blocked = admit_evolution_reason(
        derivation, campaign_id="r3-campaign", learner_eligible=True,
        state_shift=shift, routing=route)
    assert blocked.admitted is False
    assert blocked.blocked_reason == "missing_paired_counterfactual"


def test_interference_admission_revalidates_detector_against_paired_receipt():
    candidate = _candidate()

    def oracle(current, _case, _budget):
        if current is None:
            return {"compile_result": "PASS", "functional_result": "PASS",
                    "signoff_result": "PASS", "toolchain_digest": "sha256:tool",
                    "oracle_digest": "sha256:oracle"}
        return {"compile_result": "PASS", "functional_result": "FAIL",
                "signoff_result": "FAIL", "toolchain_digest": "sha256:tool",
                "oracle_digest": "sha256:oracle"}

    paired = execute_paired_candidates(
        {"case_id": "r3-interference-admit", "toolchain_digest": "sha256:tool"},
        {arm: None if arm == "NO_MEMORY" else candidate for arm in P12_ARMS},
        oracle=oracle, routing_decision="CONSIDER",
        lineage_id="lineage-interference-admit")
    derivation = derive_memory_interference_reason(
        paired, campaign_id="r3-campaign")
    admission = admit_evolution_reason(
        derivation, campaign_id="r3-campaign", learner_eligible=True,
        paired=paired)
    assert admission.admitted is True
    assert admission.reason == "MEMORY_INTERFERENCE"

    tampered = {**derivation.to_dict(), "detector_version": "other"}
    with pytest.raises(EvolutionReasonDerivationError, match="digest mismatch"):
        # A detector receipt changed without recomputing its digest cannot be
        # admitted or reinterpreted as a different detector.
        EvolutionReasonDerivationReceipt.from_dict({
            **tampered, "receipt_digest": derivation.receipt_digest})


def test_derivation_receipt_rejects_mutation_provenance_fields():
    shift = _shift()
    receipt = derive_state_shift_reason(
        shift, campaign_id="r3-campaign", case_id="r3-case",
        routing=_shift_route(shift), lineage_id="lineage-r3")
    with pytest.raises(EvolutionReasonDerivationError, match="mutation field"):
        EvolutionReasonDerivationReceipt.from_dict({
            **receipt.to_dict(), "localized_update_plan": {"operation": "REVISE"}})
