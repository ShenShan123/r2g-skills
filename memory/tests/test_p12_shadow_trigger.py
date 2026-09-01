"""P12-to-P13 shadow trigger bridge tests."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from contracts import MemoryRoutingDecision
from tehm.evaluation.candidate_executor import (
    CandidateExecutionReceipt, PairedCandidateExecutionReceipt,
)
from tehm.evolution import (
    P12ShadowTriggerError, P12ShadowUpdateTriggerReceipt,
    build_p12_shadow_update_triggers,
)


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
        routing_decisions={case_id: _routing(case_id) for case_id in ("case-0", "case-1")})
    assert len(triggers) == 2
    assert all(item.triggered for item in triggers)
    assert all(item.reason == "oracle_complete" for item in triggers)
    assert triggers[0].cohort_receipt_digest == "sha256:cohort-receipt"
    replay = P12ShadowUpdateTriggerReceipt.from_dict({
        **triggers[0].to_dict(), "receipt_digest": triggers[0].receipt_digest})
    assert replay == triggers[0]


def test_incomplete_or_routingless_evidence_is_non_triggering():
    missing_route = build_p12_shadow_update_triggers(
        _cohort(routing=False), memory_arm="ALWAYS_MEMORY", learner_eligible=True)
    assert [item.reason for item in missing_route] == [
        "missing_routing_receipt", "missing_routing_receipt"]
    unknown = build_p12_shadow_update_triggers(
        _cohort(unknown_baseline=True), memory_arm="ALWAYS_MEMORY",
        learner_eligible=True,
        routing_decisions={case_id: _routing(case_id) for case_id in ("case-0", "case-1")})
    assert unknown[0].triggered is False
    assert unknown[0].reason == "baseline_oracle_incomplete"
    assert unknown[1].triggered is True

    audit_only = build_p12_shadow_update_triggers(
        _cohort(), memory_arm="ALWAYS_MEMORY", learner_eligible=False,
        routing_decisions={case_id: _routing(case_id) for case_id in ("case-0", "case-1")})
    assert all(not item.triggered and item.reason == "not_learner_eligible"
               for item in audit_only)


def test_structural_cohort_gates_fail_closed_before_expensive_p13_work():
    with pytest.raises(P12ShadowTriggerError, match="distinct lineages"):
        build_p12_shadow_update_triggers(
            _cohort(same_lineage=True), memory_arm="ALWAYS_MEMORY",
            learner_eligible=True)
    bad = _cohort()
    bad.source_disjoint = False
    with pytest.raises(P12ShadowTriggerError, match="source-disjoint"):
        build_p12_shadow_update_triggers(
            bad, memory_arm="ALWAYS_MEMORY", learner_eligible=True)
