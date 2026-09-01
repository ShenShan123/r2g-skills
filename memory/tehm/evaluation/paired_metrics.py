"""Unknown-safe metrics for P12 paired execution evidence.

This module is deliberately downstream of the execution receipt.  It computes
descriptive, evaluation-only statistics and never infers a verdict, updates
memory, or evaluates promotion authority.  A pair containing ``UNKNOWN`` is
excluded from the corresponding paired denominator; UNKNOWN is not converted
to a failure or a pass.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tehm.canonical.transition import HARMFUL_OUTCOMES, OUTCOMES, POSITIVE_OUTCOMES

from .candidate_executor import (
    P12_ARMS, PairedCandidateExecutionReceipt,
)
from .no_skill_calibration import mcnemar_regression_test, wilson_interval


PAIRED_METRICS_VERSION = "p12-paired-metrics-v0.2"
_MEMORY_ARMS = P12_ARMS[1:]


class PairedMetricsError(ValueError):
    """Paired receipts cannot be safely summarized."""


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 6)


@dataclass(frozen=True)
class PairedCohortMetrics:
    """Denominator-explicit, replay-friendly P12 descriptive metrics."""

    cases: int
    lineage_count: int
    outcome_counts: dict[str, dict[str, int]]
    paired_cases: dict[str, int]
    unknown_pairs: dict[str, int]
    baseline_passes: dict[str, int]
    memory_passes: dict[str, int]
    repair_deltas: dict[str, float | None]
    memory_interference_cases: dict[str, int]
    memory_interference_denominators: dict[str, int]
    memory_interference_rates: dict[str, float | None]
    memory_interference_intervals: dict[str, dict]
    repair_regression_cases: dict[str, int]
    repair_improvement_cases: dict[str, int]
    repair_mcnemar: dict[str, dict]
    no_skill_reason_counts: dict[str, int]
    routing_receipt_coverage: float

    def __post_init__(self) -> None:
        if type(self.cases) is not int or self.cases < 1:
            raise PairedMetricsError("paired metrics cases must be positive")
        if type(self.lineage_count) is not int or not 1 <= self.lineage_count <= self.cases:
            raise PairedMetricsError("paired metrics lineage_count is invalid")
        if set(self.outcome_counts) != set(P12_ARMS):
            raise PairedMetricsError("paired metrics outcome arms are incomplete")
        for arm in P12_ARMS:
            counts = self.outcome_counts[arm]
            if set(counts) != set(OUTCOMES):
                raise PairedMetricsError("paired metrics outcome counts are invalid")
            if any(type(value) is not int or value < 0 for value in counts.values()):
                raise PairedMetricsError("paired metrics outcome count is invalid")
            if sum(counts.values()) != self.cases:
                raise PairedMetricsError("paired metrics outcome count total is invalid")
        for mapping_name in (
                "paired_cases", "unknown_pairs", "baseline_passes", "memory_passes",
                "memory_interference_cases", "memory_interference_denominators"):
            mapping = getattr(self, mapping_name)
            if set(mapping) != set(_MEMORY_ARMS) or any(
                    type(value) is not int or value < 0 or value > self.cases
                    for value in mapping.values()):
                raise PairedMetricsError(f"paired metrics {mapping_name} is invalid")
        for mapping_name in ("memory_interference_intervals", "repair_mcnemar"):
            mapping = getattr(self, mapping_name)
            if set(mapping) != set(_MEMORY_ARMS) or any(
                    not isinstance(value, Mapping) for value in mapping.values()):
                raise PairedMetricsError(f"paired metrics {mapping_name} is invalid")
        for mapping_name in ("repair_regression_cases", "repair_improvement_cases"):
            mapping = getattr(self, mapping_name)
            if set(mapping) != set(_MEMORY_ARMS) or any(
                    type(value) is not int or value < 0 or value > self.cases
                    for value in mapping.values()):
                raise PairedMetricsError(f"paired metrics {mapping_name} is invalid")
        for arm in _MEMORY_ARMS:
            if self.paired_cases[arm] + self.unknown_pairs[arm] != self.cases:
                raise PairedMetricsError("paired metrics known/unknown partition is invalid")
            if self.memory_interference_cases[arm] > self.memory_interference_denominators[arm]:
                raise PairedMetricsError("paired metrics interference count is invalid")
            if (self.repair_regression_cases[arm] +
                    self.repair_improvement_cases[arm] > self.paired_cases[arm]):
                raise PairedMetricsError("paired metrics repair discordance is invalid")
        for mapping_name in ("repair_deltas", "memory_interference_rates"):
            mapping = getattr(self, mapping_name)
            if set(mapping) != set(_MEMORY_ARMS):
                raise PairedMetricsError(f"paired metrics {mapping_name} is incomplete")
            lower_bound = -1.0 if mapping_name == "repair_deltas" else 0.0
            if any(value is not None and (not isinstance(value, (int, float)) or
                                          not lower_bound <= float(value) <= 1.0)
                   for value in mapping.values()):
                raise PairedMetricsError(f"paired metrics {mapping_name} is invalid")
        expected_reasons = {"NO_MATCH", "STATE_SHIFT", "RISK"}
        if set(self.no_skill_reason_counts) != expected_reasons or any(
                type(value) is not int or value < 0 or value > self.cases
                for value in self.no_skill_reason_counts.values()):
            raise PairedMetricsError("paired metrics no-skill reason counts are invalid")
        if (not isinstance(self.routing_receipt_coverage, (int, float)) or
                not 0.0 <= float(self.routing_receipt_coverage) <= 1.0):
            raise PairedMetricsError("paired metrics routing coverage is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": PAIRED_METRICS_VERSION,
            "cases": self.cases,
            "lineage_count": self.lineage_count,
            "outcome_counts": self.outcome_counts,
            "paired_cases": self.paired_cases,
            "unknown_pairs": self.unknown_pairs,
            "baseline_passes": self.baseline_passes,
            "memory_passes": self.memory_passes,
            "repair_deltas": self.repair_deltas,
            "memory_interference_cases": self.memory_interference_cases,
            "memory_interference_denominators": self.memory_interference_denominators,
            "memory_interference_rates": self.memory_interference_rates,
            "memory_interference_intervals": self.memory_interference_intervals,
            "repair_regression_cases": self.repair_regression_cases,
            "repair_improvement_cases": self.repair_improvement_cases,
            "repair_mcnemar": self.repair_mcnemar,
            "no_skill_reason_counts": self.no_skill_reason_counts,
            "routing_receipt_coverage": self.routing_receipt_coverage,
            "evaluation_only": True,
        }


def _receipt_rows(value: object) -> tuple[PairedCandidateExecutionReceipt, ...]:
    if hasattr(value, "case_receipts"):
        value = getattr(value, "case_receipts")
    if isinstance(value, Mapping):
        value = tuple(value.values())
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PairedMetricsError("paired metrics require paired receipts")
    rows = tuple(value)
    if not rows or any(not isinstance(row, PairedCandidateExecutionReceipt) for row in rows):
        raise PairedMetricsError("paired metrics require non-empty paired receipts")
    if len({row.case_id for row in rows}) != len(rows):
        raise PairedMetricsError("paired metrics contain duplicate case IDs")
    return rows


def summarize_paired_cohort(value: object) -> PairedCohortMetrics:
    """Summarize a cohort receipt or paired-receipt sequence.

    ``UNKNOWN`` is excluded only from the paired denominator for each memory
    arm.  The raw outcome counts retain it so incomplete oracle coverage stays
    visible to later calibration and authority gates.
    """
    rows = _receipt_rows(value)
    outcome_counts = {
        arm: {outcome: 0 for outcome in OUTCOMES} for arm in P12_ARMS}
    paired_cases: dict[str, int] = {}
    unknown_pairs: dict[str, int] = {}
    baseline_passes: dict[str, int] = {}
    memory_passes: dict[str, int] = {}
    repair_deltas: dict[str, float | None] = {}
    interference: dict[str, int] = {}
    interference_denominators: dict[str, int] = {}
    repair_regressions: dict[str, int] = {}
    repair_improvements: dict[str, int] = {}
    for bundle in rows:
        for arm in P12_ARMS:
            outcome_counts[arm][bundle.arm_receipts[arm].outcome] += 1
        baseline = bundle.arm_receipts["NO_MEMORY"].outcome
        for arm in _MEMORY_ARMS:
            memory = bundle.arm_receipts[arm].outcome
            known = baseline != "UNKNOWN" and memory != "UNKNOWN"
            paired_cases[arm] = paired_cases.get(arm, 0) + int(known)
            unknown_pairs[arm] = unknown_pairs.get(arm, 0) + int(not known)
            baseline_passes[arm] = baseline_passes.get(arm, 0) + int(
                known and baseline in POSITIVE_OUTCOMES)
            memory_passes[arm] = memory_passes.get(arm, 0) + int(
                known and memory in POSITIVE_OUTCOMES)
            interference_denominators[arm] = paired_cases[arm]
            interference[arm] = interference.get(arm, 0) + int(
                known and baseline in POSITIVE_OUTCOMES and memory in HARMFUL_OUTCOMES)
            repair_regressions[arm] = repair_regressions.get(arm, 0) + int(
                known and baseline in POSITIVE_OUTCOMES and
                memory not in POSITIVE_OUTCOMES)
            repair_improvements[arm] = repair_improvements.get(arm, 0) + int(
                known and baseline not in POSITIVE_OUTCOMES and
                memory in POSITIVE_OUTCOMES)
    for arm in _MEMORY_ARMS:
        denominator = paired_cases[arm]
        repair_deltas[arm] = _rate(memory_passes[arm], denominator)
        if repair_deltas[arm] is not None:
            repair_deltas[arm] = round(
                (memory_passes[arm] - baseline_passes[arm]) / denominator, 6)
        else:
            repair_deltas[arm] = None
    reasons = {"NO_MATCH": 0, "STATE_SHIFT": 0, "RISK": 0}
    for bundle in rows:
        if bundle.no_skill_reason is not None:
            reasons[bundle.no_skill_reason] += 1
    lineage_ids = {bundle.lineage_id or bundle.case_id for bundle in rows}
    return PairedCohortMetrics(
        cases=len(rows), lineage_count=len(lineage_ids),
        outcome_counts=outcome_counts, paired_cases=paired_cases,
        unknown_pairs=unknown_pairs, baseline_passes=baseline_passes,
        memory_passes=memory_passes, repair_deltas=repair_deltas,
        memory_interference_cases=interference,
        memory_interference_denominators=interference_denominators,
        memory_interference_rates={
            arm: _rate(interference[arm], interference_denominators[arm])
            for arm in _MEMORY_ARMS}, no_skill_reason_counts=reasons,
        memory_interference_intervals={
            arm: wilson_interval(interference[arm], interference_denominators[arm])
            for arm in _MEMORY_ARMS},
        repair_regression_cases=repair_regressions,
        repair_improvement_cases=repair_improvements,
        repair_mcnemar={
            arm: mcnemar_regression_test(
                repair_regressions[arm], repair_improvements[arm])
            for arm in _MEMORY_ARMS},
        routing_receipt_coverage=round(
            sum(bundle.routing_receipt_id is not None for bundle in rows) / len(rows), 6))


__all__ = [
    "PAIRED_METRICS_VERSION", "PairedMetricsError", "PairedCohortMetrics",
    "summarize_paired_cohort",
]
