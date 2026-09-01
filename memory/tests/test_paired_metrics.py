"""P12 unknown-safe paired cohort metrics."""
from __future__ import annotations

import pytest

from tehm.evaluation.candidate_executor import (
    P12_ARMS, CandidateExecutionReceipt, PairedCandidateExecutionReceipt,
)
from tehm.evaluation.paired_metrics import summarize_paired_cohort


def _arm(case_id: str, arm: str, outcome: str) -> CandidateExecutionReceipt:
    source = "no_memory" if arm == "NO_MEMORY" else "structured_memory"
    return CandidateExecutionReceipt(
        case_id=case_id, candidate_id=f"{case_id}:{arm}", source=source,
        action_digest="sha256:action", candidate_digest="sha256:candidate",
        compile_result="PASS", functional_result="PASS", signoff_result="PASS",
        outcome=outcome, created_regressions=(), obligations={},
        toolchain_digest="sha256:tool", oracle_digest="sha256:oracle",
        produced_transition_id=None, budget=3)


def _bundle(case_id: str, lineage: str, baseline: str, *, always: str,
            gated: str, causal: str, reason: str, routed: bool = True):
    return PairedCandidateExecutionReceipt(
        case_id=case_id,
        arm_receipts={
            arm: _arm(case_id, arm, outcome)
            for arm, outcome in {
                "NO_MEMORY": baseline, "ALWAYS_MEMORY": always,
                "APPLICABILITY_GATED": gated, "CAUSAL_NO_SKILL": causal,
            }.items()},
        candidate_budget=3, case_digest="sha256:case",
        toolchain_digest="sha256:tool", oracle_digest="sha256:oracle",
        no_skill_reason=reason, lineage_id=lineage,
        routing_receipt_id=f"routing:{case_id}" if routed else None)


def test_unknown_outcomes_are_visible_but_excluded_from_pair_denominators():
    rows = [
        _bundle("case-1", "lineage-a", "PASS", always="FAIL", gated="PASS",
                causal="PASS", reason="NO_MATCH"),
        _bundle("case-2", "lineage-b", "PASS", always="UNKNOWN", gated="UNKNOWN",
                causal="PASS", reason="STATE_SHIFT"),
        _bundle("case-3", "lineage-b", "UNKNOWN", always="PASS", gated="PASS",
                causal="UNKNOWN", reason="RISK", routed=False),
    ]
    metrics = summarize_paired_cohort(rows)
    assert metrics.cases == 3
    assert metrics.lineage_count == 2
    assert metrics.outcome_counts["ALWAYS_MEMORY"]["UNKNOWN"] == 1
    assert metrics.paired_cases["ALWAYS_MEMORY"] == 1
    assert metrics.unknown_pairs["ALWAYS_MEMORY"] == 2
    assert metrics.memory_interference_cases["ALWAYS_MEMORY"] == 1
    assert metrics.memory_interference_denominators["ALWAYS_MEMORY"] == 1
    assert metrics.memory_interference_rates["ALWAYS_MEMORY"] == 1.0
    assert metrics.memory_interference_intervals["ALWAYS_MEMORY"] == {
        "successes": 1, "total": 1, "point": 1.0,
        "lower": pytest.approx(0.206549), "upper": 1.0,
        "confidence": 0.95,
    }
    assert metrics.repair_regression_cases["ALWAYS_MEMORY"] == 1
    assert metrics.repair_improvement_cases["ALWAYS_MEMORY"] == 0
    assert metrics.repair_mcnemar["ALWAYS_MEMORY"] == {
        "regression_cases": 1, "improvement_cases": 0,
        "discordant_cases": 1, "p_value": 1.0, "alpha": 0.05,
        "significant_regression": False,
    }
    assert metrics.repair_regression_cases["APPLICABILITY_GATED"] == 0
    assert metrics.repair_improvement_cases["APPLICABILITY_GATED"] == 0
    improved = summarize_paired_cohort([_bundle(
        "case-4", "lineage-c", "FAIL", always="FAIL", gated="PASS",
        causal="FAIL", reason="NO_MATCH")])
    assert improved.repair_improvement_cases["APPLICABILITY_GATED"] == 1
    assert improved.repair_mcnemar["APPLICABILITY_GATED"]["p_value"] == 1.0
    assert metrics.repair_deltas["ALWAYS_MEMORY"] == -1.0
    assert metrics.no_skill_reason_counts == {
        "NO_MATCH": 1, "STATE_SHIFT": 1, "RISK": 1}
    assert metrics.routing_receipt_coverage == pytest.approx(2 / 3)
    assert metrics.to_dict()["evaluation_only"] is True
