"""P12-to-P13 shadow trigger bridge tests."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

import pytest

from contracts import MemoryRoutingDecision
from tehm.evaluation.candidate_executor import (
    P12_ARMS, CandidateExecutionReceipt, PairedCandidateExecutionReceipt,
)
from tehm.evolution import (
    P12ShadowTriggerError, P12ShadowUpdateTriggerReceipt,
    P13EvolutionReasonReceipt,
    build_p12_shadow_update_triggers,
    build_p12_shadow_update_triggers_from_reason_receipt,
    derive_state_shift_reason, p13_reason_receipt_from_derivations,
)
from tehm.ids import stable_dumps
from tehm.state.shift_receipts import StateShiftReceipt


@dataclass
class _Cohort:
    campaign_id: str
    case_receipts: dict
    source_disjoint: bool = True
    source_restore_verified: bool = True
    evaluation_only: bool = True

    @property
    def receipt_digest(self) -> str:
        return "sha256:cohort-receipt"


def _routing(case_id: str) -> MemoryRoutingDecision:
    return MemoryRoutingDecision(
        decision="CONSIDER", resolved_state_id=f"state:{case_id}",
        selected_rule_ids=(f"rule:{case_id}",), selected_path_ids=(f"path:{case_id}",),
        selected_asset_ids=(), applicability={"status": "APPLICABLE"},
        causal_support={"status": "SUPPORTED"}, risk={}, abstain_reasons=(),
        no_memory_budget=2, memory_budget=1)


def _state_shift_routing(case_id: str) -> MemoryRoutingDecision:
    """A deliberate no-memory route with a typed transfer-boundary witness."""
    payload = {
        "version": "state-shift-v0.1",
        "current_resolution_id": f"state:{case_id}",
        "knowledge_object_id": f"knowledge:{case_id}",
        "support_envelope_digest": "sha256:" + "e" * 64,
        "structural_shift": 1.0, "mechanism_shift": 0.0,
        "flow_shift": 0.0, "constraint_shift": 0.0,
        "oracle_shift": 0.0, "history_shift": 0.0,
        "aggregate_shift": 0.166667,
        "shifted_dimensions": ("structural_shift",),
        "transferable": False, "reason": "STATE_SHIFT",
        "evidence_refs": (f"event:{case_id}",),
    }
    receipt = StateShiftReceipt(
        **payload,
        replay_digest="sha256:" + hashlib.sha256(
            stable_dumps(payload).encode()).hexdigest())
    return MemoryRoutingDecision(
        decision="NO_SKILL", resolved_state_id=f"state:{case_id}",
        selected_rule_ids=(), selected_path_ids=(), selected_asset_ids=(),
        applicability={"status": "APPLICABLE", "state_shift_status": "SHIFTED"},
        causal_support={"status": "SUPPORTED"},
        risk={"state_shift_status": "SHIFTED"},
        abstain_reasons=("state_shift",), no_memory_budget=3, memory_budget=0,
        no_skill_reason="STATE_SHIFT",
        state_shift_receipt_id=receipt.receipt_id,
        state_shift_receipt=receipt.to_dict())


def _state_shift_cohort() -> tuple[_Cohort, dict[str, MemoryRoutingDecision]]:
    cases = {}
    routes = {}
    for index, lineage in enumerate(("lineage-a", "lineage-b")):
        case_id = f"state-shift-case-{index}"
        baseline = _execution(case_id, f"no-memory-{index}", "no_memory", "PASS")
        memory = _execution(case_id, f"memory-{index}", "structured_memory", "FAIL")
        route = _state_shift_routing(case_id)
        routes[case_id] = route
        cases[case_id] = PairedCandidateExecutionReceipt(
            case_id=case_id,
            arm_receipts={arm: baseline if arm == "NO_MEMORY" else memory
                          for arm in P12_ARMS},
            candidate_budget=3, case_digest=f"sha256:state-case-{index}",
            toolchain_digest="sha256:tool", oracle_digest="sha256:oracle",
            no_skill_reason="STATE_SHIFT",
            state_shift_receipt_id=route.state_shift_receipt_id,
            lineage_id=lineage, routing_receipt_id=route.routing_receipt_id)
    return _Cohort("campaign-state-shift", cases), routes


def _execution(case_id: str, candidate_id: str, source: str, outcome: str,
               *, oracle: bool = True) -> CandidateExecutionReceipt:
    return CandidateExecutionReceipt(
        case_id=case_id, candidate_id=candidate_id, source=source,
        action_digest="sha256:action-" + candidate_id,
        candidate_digest="sha256:candidate-" + candidate_id,
        compile_result="PASS", functional_result="PASS", signoff_result="PASS",
        outcome=outcome, created_regressions=(), obligations={},
        toolchain_digest="sha256:tool", oracle_digest="sha256:oracle",
        produced_transition_id=None, budget=3,
        metadata={"oracle_available": oracle})


def _cohort(*, routing: bool = True, unknown_baseline: bool = False,
            same_lineage: bool = False) -> _Cohort:
    cases = {}
    for index, lineage in enumerate(("lineage-a", "lineage-a" if same_lineage else "lineage-b")):
        case_id = f"case-{index}"
        baseline = _execution(
            case_id, f"no_memory:{case_id}", "no_memory",
            "UNKNOWN" if unknown_baseline and index == 0 else "PASS")
        memory = _execution(case_id, f"memory:{case_id}", "structured_memory", "PASS")
        cases[case_id] = PairedCandidateExecutionReceipt(
            case_id=case_id,
            arm_receipts={
                "NO_MEMORY": baseline,
                "ALWAYS_MEMORY": memory,
                "APPLICABILITY_GATED": memory,
                "CAUSAL_NO_SKILL": memory,
            },
            candidate_budget=3, case_digest="sha256:case-" + case_id,
            toolchain_digest="sha256:tool", oracle_digest="sha256:oracle",
            lineage_id=lineage,
            routing_receipt_id=(_routing(case_id).routing_receipt_id if routing else None),
        )
    return _Cohort("campaign-training", cases)


def test_complete_multilineage_oracle_builds_replayable_trigger():
    triggers = build_p12_shadow_update_triggers(
        _cohort(), memory_arm="ALWAYS_MEMORY", learner_eligible=True,
        routing_decisions={case_id: _routing(case_id) for case_id in ("case-0", "case-1")},
        case_learner_eligibility={"case-0": True, "case-1": True},
        evolution_reasons={"case-0": ("CAPABILITY_GAP",),
                           "case-1": ("NOVELTY",)})
    assert len(triggers) == 2
    assert all(item.triggered for item in triggers)
    assert all(item.reason == "oracle_complete" for item in triggers)
    assert triggers[0].cohort_receipt_digest == "sha256:cohort-receipt"
    replay = P12ShadowUpdateTriggerReceipt.from_dict({
        **triggers[0].to_dict(), "receipt_digest": triggers[0].receipt_digest})
    assert replay == triggers[0]
    legacy = replace(triggers[0], version="p12-shadow-trigger-v0.1",
                     evolution_reasons=())
    legacy_replay = P12ShadowUpdateTriggerReceipt.from_dict({
        **legacy.to_dict(), "receipt_digest": legacy.legacy_receipt_digest})
    assert legacy_replay == legacy


def test_evolution_reason_receipt_is_bound_and_replayable():
    receipt = P13EvolutionReasonReceipt(
        campaign_id="campaign-training",
        cohort_receipt_digest="sha256:cohort",
        label_source="independent-event-review-v1",
        evidence_refs=({"id": "event", "path": "/tmp/event.json",
                        "sha256": "sha256:event"},),
        evolution_reasons={"case-0": ("CAPABILITY_GAP",),
                           "case-1": ("NOVELTY",)},
    )
    replay = P13EvolutionReasonReceipt.from_dict({
        **receipt.to_dict(), "receipt_digest": receipt.receipt_digest})
    assert replay == receipt
    assert receipt.receipt_id.startswith("p13_evolution_reason_")
    tampered = {**receipt.to_dict(), "label_source": "unbound"}
    tampered["receipt_digest"] = receipt.receipt_digest
    with pytest.raises(P12ShadowTriggerError, match="digest mismatch"):
        P13EvolutionReasonReceipt.from_dict(tampered)


def test_incomplete_or_routingless_evidence_is_non_triggering():
    missing_route = build_p12_shadow_update_triggers(
        _cohort(routing=False), memory_arm="ALWAYS_MEMORY", learner_eligible=True,
        case_learner_eligibility={"case-0": True, "case-1": True})
    assert [item.reason for item in missing_route] == [
        "missing_routing_receipt", "missing_routing_receipt"]
    unknown = build_p12_shadow_update_triggers(
        _cohort(unknown_baseline=True), memory_arm="ALWAYS_MEMORY",
        learner_eligible=True,
        routing_decisions={case_id: _routing(case_id) for case_id in ("case-0", "case-1")},
        case_learner_eligibility={"case-0": True, "case-1": True},
        evolution_reasons={"case-0": ("REPEATED_FAILURE",),
                           "case-1": ("CAPABILITY_GAP",)})
    assert unknown[0].triggered is False
    assert unknown[0].reason == "baseline_oracle_incomplete"
    assert unknown[1].triggered is True

    audit_only = build_p12_shadow_update_triggers(
        _cohort(), memory_arm="ALWAYS_MEMORY", learner_eligible=False,
        routing_decisions={case_id: _routing(case_id) for case_id in ("case-0", "case-1")})
    assert all(not item.triggered and item.reason == "not_learner_eligible"
               for item in audit_only)


def test_complete_pass_without_evolution_signal_is_retain_only():
    triggers = build_p12_shadow_update_triggers(
        _cohort(), memory_arm="ALWAYS_MEMORY", learner_eligible=True,
        routing_decisions={case_id: _routing(case_id) for case_id in ("case-0", "case-1")},
        case_learner_eligibility={"case-0": True, "case-1": True})
    assert all(not item.triggered for item in triggers)
    assert all(item.reason == "no_evolution_signal" for item in triggers)


def test_state_shift_no_skill_route_can_trigger_p13_observation():
    """8A.9 must admit only the typed STATE_SHIFT refusal exception."""
    cohort, routes = _state_shift_cohort()
    triggers = build_p12_shadow_update_triggers(
        cohort, memory_arm="ALWAYS_MEMORY", learner_eligible=True,
        routing_decisions=routes,
        case_learner_eligibility={case_id: True for case_id in cohort.case_receipts},
        evolution_reasons={case_id: ("STATE_SHIFT",)
                           for case_id in cohort.case_receipts})
    assert len(triggers) == 2
    assert all(item.triggered for item in triggers)
    assert all(item.routing_decision == "NO_SKILL" for item in triggers)
    assert all(item.no_skill_reason == "STATE_SHIFT" for item in triggers)
    assert all(item.state_shift_receipt_id for item in triggers)
    assert all(item.state_shift_receipt is not None for item in triggers)


def test_typed_reason_receipt_is_the_formal_trigger_input():
    cohort, routes = _state_shift_cohort()
    derivations = {
        case_id: (derive_state_shift_reason(
            StateShiftReceipt.from_dict(route.state_shift_receipt),
            campaign_id=cohort.campaign_id, case_id=case_id,
            routing=route, lineage_id=cohort.case_receipts[case_id].lineage_id),)
        for case_id, route in routes.items()
    }
    reason_receipt = p13_reason_receipt_from_derivations(
        derivations, campaign_id=cohort.campaign_id,
        cohort_receipt_digest=cohort.receipt_digest)
    triggers = build_p12_shadow_update_triggers_from_reason_receipt(
        cohort, memory_arm="ALWAYS_MEMORY", learner_eligible=True,
        reason_receipt=reason_receipt, min_lineages=2,
        routing_decisions=routes,
        case_learner_eligibility={case_id: True for case_id in routes},
        derivation_receipts=derivations)
    assert all(item.triggered for item in triggers)
    assert all(item.evolution_reasons == ("STATE_SHIFT",) for item in triggers)


def test_typed_reason_receipt_requires_replayed_derivations():
    cohort, routes = _state_shift_cohort()
    derivations = {
        case_id: (derive_state_shift_reason(
            StateShiftReceipt.from_dict(route.state_shift_receipt),
            campaign_id=cohort.campaign_id, case_id=case_id,
            routing=route, lineage_id=cohort.case_receipts[case_id].lineage_id),)
        for case_id, route in routes.items()
    }
    reason_receipt = p13_reason_receipt_from_derivations(
        derivations, campaign_id=cohort.campaign_id,
        cohort_receipt_digest=cohort.receipt_digest)
    with pytest.raises(P12ShadowTriggerError, match="requires derivation receipts"):
        build_p12_shadow_update_triggers_from_reason_receipt(
            cohort, memory_arm="ALWAYS_MEMORY", learner_eligible=True,
            reason_receipt=reason_receipt, routing_decisions=routes,
            case_learner_eligibility={case_id: True for case_id in routes})


def test_typed_reason_receipt_rejects_forged_derivation_reference():
    cohort, routes = _state_shift_cohort()
    derivations = {
        case_id: (derive_state_shift_reason(
            StateShiftReceipt.from_dict(route.state_shift_receipt),
            campaign_id=cohort.campaign_id, case_id=case_id,
            routing=route, lineage_id=cohort.case_receipts[case_id].lineage_id),)
        for case_id, route in routes.items()
    }
    valid = p13_reason_receipt_from_derivations(
        derivations, campaign_id=cohort.campaign_id,
        cohort_receipt_digest=cohort.receipt_digest)
    forged_refs = {
        case_id: tuple(
            {"path": "receipt://forged", "sha256": "sha256:forged", "id": "forged"}
            if case_id == "state-shift-case-0" else ref
            for ref in refs)
        for case_id, refs in valid.case_evidence_refs.items()
    }
    forged_global = tuple(ref for case_id in sorted(forged_refs)
                          for ref in forged_refs[case_id])
    forged = P13EvolutionReasonReceipt(
        campaign_id=valid.campaign_id,
        cohort_receipt_digest=valid.cohort_receipt_digest,
        label_source=valid.label_source, evidence_refs=forged_global,
        evolution_reasons=valid.evolution_reasons,
        case_evidence_refs=forged_refs)
    with pytest.raises(P12ShadowTriggerError, match="do not match derivation"):
        build_p12_shadow_update_triggers_from_reason_receipt(
            cohort, memory_arm="ALWAYS_MEMORY", learner_eligible=True,
            reason_receipt=forged, routing_decisions=routes,
            case_learner_eligibility={case_id: True for case_id in routes},
            derivation_receipts=derivations)


def test_state_shift_trigger_rejects_id_only_route_witness():
    cohort, routes = _state_shift_cohort()
    id_only_routes = {
        case_id: replace(route, state_shift_receipt=None)
        for case_id, route in routes.items()
    }
    id_only_cases = {
        case_id: replace(bundle,
                         routing_receipt_id=id_only_routes[case_id].routing_receipt_id)
        for case_id, bundle in cohort.case_receipts.items()
    }
    cohort = replace(cohort, case_receipts=id_only_cases)
    triggers = build_p12_shadow_update_triggers(
        cohort, memory_arm="ALWAYS_MEMORY", learner_eligible=True,
        routing_decisions=id_only_routes,
        case_learner_eligibility={case_id: True for case_id in cohort.case_receipts},
        evolution_reasons={case_id: ("STATE_SHIFT",)
                           for case_id in cohort.case_receipts})
    assert all(not item.triggered for item in triggers)
    assert all(item.reason == "routing_not_memory_eligible" for item in triggers)


def test_no_skill_risk_route_remains_non_triggering():
    """RISK has no state-shift observation and cannot enter this P13 seam."""
    cohort, _routes = _state_shift_cohort()
    risk_routes = {}
    risk_cases = {}
    for case_id, bundle in cohort.case_receipts.items():
        route = MemoryRoutingDecision(
            decision="NO_SKILL", resolved_state_id=f"state:{case_id}",
            selected_rule_ids=(), selected_path_ids=(), selected_asset_ids=(),
            applicability={"status": "APPLICABLE"},
            causal_support={"status": "SUPPORTED"},
            risk={"risk_status": "HIGH"}, abstain_reasons=(),
            no_memory_budget=3, memory_budget=0, no_skill_reason="RISK",
            risk_receipt_id=f"risk:{case_id}")
        risk_routes[case_id] = route
        risk_cases[case_id] = replace(
            bundle, no_skill_reason="RISK", state_shift_receipt_id=None,
            risk_receipt_id=route.risk_receipt_id,
            routing_receipt_id=route.routing_receipt_id)
    risk_cohort = replace(cohort, case_receipts=risk_cases)
    triggers = build_p12_shadow_update_triggers(
        risk_cohort, memory_arm="ALWAYS_MEMORY", learner_eligible=True,
        routing_decisions=risk_routes,
        case_learner_eligibility={case_id: True for case_id in risk_cases},
        evolution_reasons={case_id: ("MEMORY_INTERFERENCE",)
                           for case_id in risk_cases})
    assert all(not item.triggered for item in triggers)
    assert all(item.reason == "routing_not_memory_eligible" for item in triggers)


def test_evolution_reasons_are_explicit_and_typed():
    kwargs = dict(
        cohort=_cohort(), memory_arm="ALWAYS_MEMORY", learner_eligible=True,
        routing_decisions={case_id: _routing(case_id) for case_id in ("case-0", "case-1")},
        case_learner_eligibility={"case-0": True, "case-1": True})
    with pytest.raises(P12ShadowTriggerError, match="cover exactly all cases"):
        build_p12_shadow_update_triggers(
            **kwargs, evolution_reasons={"case-0": ("NOVELTY",)})
    with pytest.raises(P12ShadowTriggerError, match="reasons.*invalid"):
        build_p12_shadow_update_triggers(
            **kwargs, evolution_reasons={"case-0": ("invented",),
                                          "case-1": ("NOVELTY",)})


def test_structural_cohort_gates_fail_closed_before_expensive_p13_work():
    with pytest.raises(P12ShadowTriggerError, match="distinct lineages"):
        build_p12_shadow_update_triggers(
            _cohort(same_lineage=True), memory_arm="ALWAYS_MEMORY",
            learner_eligible=True,
            case_learner_eligibility={"case-0": True, "case-1": True})
    bad = _cohort()
    bad.source_disjoint = False
    with pytest.raises(P12ShadowTriggerError, match="source-disjoint"):
        build_p12_shadow_update_triggers(
            bad, memory_arm="ALWAYS_MEMORY", learner_eligible=True,
            case_learner_eligibility={"case-0": True, "case-1": True})


def test_learner_trigger_requires_explicit_all_training_case_partition():
    with pytest.raises(P12ShadowTriggerError, match="explicit per-case learner eligibility"):
        build_p12_shadow_update_triggers(
            _cohort(), memory_arm="ALWAYS_MEMORY", learner_eligible=True,
            routing_decisions={case_id: _routing(case_id)
                               for case_id in ("case-0", "case-1")})

    with pytest.raises(P12ShadowTriggerError, match="mixes learner-eligible"):
        build_p12_shadow_update_triggers(
            _cohort(), memory_arm="ALWAYS_MEMORY", learner_eligible=True,
            routing_decisions={case_id: _routing(case_id)
                               for case_id in ("case-0", "case-1")},
            case_learner_eligibility={"case-0": True, "case-1": False})

    audit_only = build_p12_shadow_update_triggers(
        _cohort(), memory_arm="ALWAYS_MEMORY", learner_eligible=False,
        routing_decisions={case_id: _routing(case_id)
                           for case_id in ("case-0", "case-1")},
        case_learner_eligibility={"case-0": True, "case-1": False})
    assert all(item.reason == "not_learner_eligible" for item in audit_only)
