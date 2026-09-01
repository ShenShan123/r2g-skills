"""Reason-aware, evaluation-only calibration for the NO_SKILL router.

P15 needs more than one aggregate abstention number.  This module consumes
explicit router predictions and an independent oracle label, then emits a
content-addressed receipt with binary (USE_MEMORY/NO_SKILL) metrics, typed
NO_SKILL-reason metrics, confusion matrices, Wilson 95% intervals, calibration
error, and one-dimensional strata.  It never reads or mutates canonical
memory and never grants routing or promotion authority.
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from contracts import MemoryRoutingDecision
from tehm.ids import stable_dumps


NO_SKILL_CALIBRATION_VERSION = "no-skill-calibration-v1"
CALIBRATION_DECISIONS = ("USE_MEMORY", "NO_SKILL")
CALIBRATION_REASONS = ("NO_MATCH", "STATE_SHIFT", "RISK")
CALIBRATION_LABELS = ("USE_MEMORY", *CALIBRATION_REASONS)
CALIBRATION_STRATA = (
    "mechanism_family", "design", "platform", "flow_regime",
    "model_identity", "state_shift_dimension",
)
_ROUTER_MEMORY_DECISIONS = frozenset({"APPLY", "CONSIDER"})
_Z95 = 1.959963984540054


class NoSkillCalibrationError(ValueError):
    """Malformed or unsafe NO_SKILL calibration evidence."""


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise NoSkillCalibrationError(f"{name} must be a non-empty string")
    return value.strip()


def _unit(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise NoSkillCalibrationError(f"{name} must be a finite number in [0, 1]")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NoSkillCalibrationError(
            f"{name} must be a finite number in [0, 1]") from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise NoSkillCalibrationError(f"{name} must be a finite number in [0, 1]")
    return number


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise NoSkillCalibrationError(f"{name} must be a positive integer")
    return value


def _nonnegative_int(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise NoSkillCalibrationError(f"{name} must be a non-negative integer")
    return value


def wilson_interval(successes: int, total: int, *, confidence: float = 0.95) -> dict:
    """Return a Wilson interval for a binomial proportion.

    P15 freezes a 95% interval.  Other confidence levels are rejected rather
    than silently using the wrong critical value.  A zero denominator is
    represented explicitly with null bounds and is never treated as a pass.
    """
    successes = _nonnegative_int(successes, "successes")
    total = _nonnegative_int(total, "total")
    confidence = _unit(confidence, "confidence")
    if confidence != 0.95:
        raise NoSkillCalibrationError("only 95% Wilson intervals are supported")
    if successes > total:
        raise NoSkillCalibrationError("successes cannot exceed total")
    if total == 0:
        return {
            "successes": 0, "total": 0, "point": None,
            "lower": None, "upper": None, "confidence": 0.95,
        }
    n = float(total)
    p = float(successes) / n
    z2 = _Z95 * _Z95
    denominator = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denominator
    half = (_Z95 * math.sqrt((p * (1.0 - p) / n) + z2 / (4.0 * n * n)) /
            denominator)
    return {
        "successes": successes, "total": total,
        "point": round(p, 6), "lower": round(max(0.0, centre - half), 6),
        "upper": round(min(1.0, centre + half), 6), "confidence": 0.95,
    }


@dataclass(frozen=True)
class NoSkillCalibrationSample:
    """One prediction paired with an explicit, independent oracle label.

    ``confidence`` is the probability assigned to the predicted binary
    decision (not to the reason).  Missing confidence keeps descriptive
    metrics available but makes the calibration gate NOT_ESTABLISHED.
    """

    case_id: str
    predicted_decision: str
    expected_decision: str
    predicted_reason: str | None = None
    expected_reason: str | None = None
    confidence: float | None = None
    strata: dict[str, str] = field(default_factory=dict)
    routing_receipt_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _text(self.case_id, "case_id"))
        for name in ("predicted_decision", "expected_decision"):
            value = getattr(self, name)
            if value not in CALIBRATION_DECISIONS:
                raise NoSkillCalibrationError(f"{name} is invalid")
        for name in ("predicted_reason", "expected_reason"):
            value = getattr(self, name)
            if value is not None and value not in CALIBRATION_REASONS:
                raise NoSkillCalibrationError(f"{name} is invalid")
        if self.predicted_decision == "NO_SKILL":
            if self.predicted_reason is None:
                raise NoSkillCalibrationError("NO_SKILL prediction requires a reason")
        elif self.predicted_reason is not None:
            raise NoSkillCalibrationError("USE_MEMORY prediction cannot carry a reason")
        if self.expected_decision == "NO_SKILL":
            if self.expected_reason is None:
                raise NoSkillCalibrationError("NO_SKILL oracle label requires a reason")
        elif self.expected_reason is not None:
            raise NoSkillCalibrationError("USE_MEMORY oracle label cannot carry a reason")
        if self.confidence is not None:
            object.__setattr__(self, "confidence", _unit(self.confidence, "confidence"))
        if not isinstance(self.strata, Mapping):
            raise NoSkillCalibrationError("strata must be an object")
        normalized: dict[str, str] = {}
        for key, value in self.strata.items():
            if key not in CALIBRATION_STRATA:
                raise NoSkillCalibrationError(f"unsupported calibration stratum: {key}")
            normalized[key] = _text(value, f"strata.{key}")
        object.__setattr__(self, "strata", normalized)
        if self.routing_receipt_id is not None:
            object.__setattr__(self, "routing_receipt_id",
                               _text(self.routing_receipt_id, "routing_receipt_id"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "predicted_decision": self.predicted_decision,
            "expected_decision": self.expected_decision,
            "predicted_reason": self.predicted_reason,
            "expected_reason": self.expected_reason,
            "confidence": self.confidence,
            "strata": dict(sorted(self.strata.items())),
            "routing_receipt_id": self.routing_receipt_id,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "NoSkillCalibrationSample":
        if not isinstance(payload, Mapping):
            raise NoSkillCalibrationError("calibration sample must be an object")
        # These aliases make it possible to adapt existing oracle manifests
        # without weakening the typed decision/reason contract.
        def pick(*names: str):
            for name in names:
                if name in payload:
                    return payload[name]
            return None
        return cls(
            case_id=pick("case_id", "id"),
            predicted_decision=pick("predicted_decision", "prediction"),
            expected_decision=pick("expected_decision", "oracle_decision", "label"),
            predicted_reason=pick("predicted_reason", "prediction_reason"),
            expected_reason=pick("expected_reason", "oracle_reason", "label_reason"),
            confidence=payload.get("confidence"), strata=payload.get("strata") or {},
            routing_receipt_id=payload.get("routing_receipt_id"),
        )


def _normalise_samples(value: object) -> tuple[NoSkillCalibrationSample, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise NoSkillCalibrationError("calibration samples must be a sequence")
    rows = tuple(item if isinstance(item, NoSkillCalibrationSample)
                 else NoSkillCalibrationSample.from_dict(item) for item in value)
    if not rows:
        raise NoSkillCalibrationError("calibration samples must be non-empty")
    ids = [row.case_id for row in rows]
    if len(set(ids)) != len(ids):
        raise NoSkillCalibrationError("calibration samples contain duplicate case IDs")
    return rows


def _routing_decision(value: object) -> MemoryRoutingDecision:
    if isinstance(value, MemoryRoutingDecision):
        return value
    if not isinstance(value, Mapping):
        raise NoSkillCalibrationError("routing decision must be a typed receipt")
    supplied_id = value.get("routing_receipt_id")
    try:
        decision = MemoryRoutingDecision.from_dict(value)
    except (TypeError, ValueError) as exc:
        raise NoSkillCalibrationError("routing decision receipt is malformed") from exc
    if supplied_id is not None and supplied_id != decision.routing_receipt_id:
        raise NoSkillCalibrationError("routing receipt digest does not match decision")
    return decision


def build_no_skill_calibration_samples(
        paired_receipts: object, routing_decisions: Mapping,
        oracle_labels: Mapping) -> tuple[NoSkillCalibrationSample, ...]:
    """Bind router receipts to explicit, independent oracle labels.

    This adapter intentionally has no outcome argument.  A route is mapped to
    the binary prediction only from its typed decision, while the expected
    label must be supplied separately by an oracle manifest.  ``ABSTAIN`` and
    ``INAPPLICABLE`` are outside the P15 binary calibration contract and are
    rejected instead of being silently relabelled as NO_SKILL.
    """
    if not isinstance(routing_decisions, Mapping) or not isinstance(oracle_labels, Mapping):
        raise NoSkillCalibrationError(
            "routing_decisions and oracle_labels must be objects")
    raw_cases = getattr(paired_receipts, "case_receipts", None)
    if raw_cases is None and isinstance(paired_receipts, Mapping):
        raw_cases = paired_receipts.get("case_receipts", paired_receipts)
    if isinstance(raw_cases, (str, bytes)) or not isinstance(raw_cases, Mapping) or not raw_cases:
        raise NoSkillCalibrationError("paired_receipts must contain case_receipts")
    case_ids = set(raw_cases)
    if set(routing_decisions) != case_ids or set(oracle_labels) != case_ids:
        raise NoSkillCalibrationError(
            "routing_decisions and oracle_labels must cover exactly all cases")
    rows: list[NoSkillCalibrationSample] = []
    for case_id in sorted(case_ids):
        route = _routing_decision(routing_decisions[case_id])
        bundle = raw_cases[case_id]
        bundle_id = (getattr(bundle, "routing_receipt_id", None)
                     if not isinstance(bundle, Mapping)
                     else bundle.get("routing_receipt_id"))
        if not isinstance(bundle_id, str) or not bundle_id:
            raise NoSkillCalibrationError(
                f"paired case {case_id} is missing routing_receipt_id")
        if bundle_id != route.routing_receipt_id:
            raise NoSkillCalibrationError(
                f"paired case {case_id} routing receipt does not match decision")
        if route.decision in _ROUTER_MEMORY_DECISIONS:
            predicted_decision, predicted_reason = "USE_MEMORY", None
        elif route.decision == "NO_SKILL":
            predicted_decision, predicted_reason = "NO_SKILL", route.no_skill_reason
        else:
            raise NoSkillCalibrationError(
                f"paired case {case_id} route {route.decision} is outside P15 binary calibration")
        label = oracle_labels[case_id]
        if not isinstance(label, Mapping):
            raise NoSkillCalibrationError(f"oracle label for {case_id} is malformed")
        rows.append(NoSkillCalibrationSample(
            case_id=str(case_id), predicted_decision=predicted_decision,
            predicted_reason=predicted_reason,
            expected_decision=label.get("expected_decision", label.get("oracle_decision")),
            expected_reason=label.get("expected_reason", label.get("oracle_reason")),
            confidence=label.get("confidence"), strata=label.get("strata") or {},
            routing_receipt_id=bundle_id))
    return tuple(rows)


def _binary_summary(rows: Sequence[NoSkillCalibrationSample], *, include_bins: bool) -> dict:
    tp = sum(row.predicted_decision == "NO_SKILL" and
             row.expected_decision == "NO_SKILL" for row in rows)
    fp = sum(row.predicted_decision == "NO_SKILL" and
             row.expected_decision == "USE_MEMORY" for row in rows)
    fn = sum(row.predicted_decision == "USE_MEMORY" and
             row.expected_decision == "NO_SKILL" for row in rows)
    tn = sum(row.predicted_decision == "USE_MEMORY" and
             row.expected_decision == "USE_MEMORY" for row in rows)
    total = len(rows)
    precision = wilson_interval(tp, tp + fp)
    recall = wilson_interval(tp, tp + fn)
    correct = tp + tn
    ece, bins = _calibration_error(rows) if include_bins else (None, [])
    return {
        "cases": total,
        "confusion": {"true_positive": tp, "false_positive": fp,
                       "false_negative": fn, "true_negative": tn},
        "precision": precision,
        "recall": recall,
        "f1": (round(2.0 * tp / (2 * tp + fp + fn), 6)
               if 2 * tp + fp + fn else None),
        # Coverage means the selective router's USE_MEMORY coverage.  The
        # separate decision_coverage is always one for typed samples.
        "coverage": round(sum(row.predicted_decision == "USE_MEMORY"
                               for row in rows) / total, 6) if total else None,
        "decision_coverage": 1.0 if total else None,
        "correct_rate": wilson_interval(correct, total),
        "calibration_error": ece,
        "calibration_bins": bins,
    }


def _reason_label(row: NoSkillCalibrationSample, *, expected: bool) -> str:
    decision = row.expected_decision if expected else row.predicted_decision
    if decision == "USE_MEMORY":
        return "USE_MEMORY"
    return row.expected_reason if expected else row.predicted_reason  # type: ignore[return-value]


def _reason_summary(rows: Sequence[NoSkillCalibrationSample]) -> tuple[dict, dict]:
    matrix = {actual: {predicted: 0 for predicted in CALIBRATION_LABELS}
              for actual in CALIBRATION_LABELS}
    for row in rows:
        matrix[_reason_label(row, expected=True)][_reason_label(row, expected=False)] += 1
    per_reason: dict[str, dict] = {}
    total = len(rows)
    for reason in CALIBRATION_REASONS:
        tp = matrix[reason][reason]
        fp = sum(matrix[actual][reason] for actual in CALIBRATION_LABELS if actual != reason)
        fn = sum(matrix[reason][predicted] for predicted in CALIBRATION_LABELS
                 if predicted != reason)
        tn = total - tp - fp - fn
        per_reason[reason] = {
            "support": sum(matrix[reason].values()),
            "predicted": sum(matrix[actual][reason] for actual in CALIBRATION_LABELS),
            "confusion": {"true_positive": tp, "false_positive": fp,
                           "false_negative": fn, "true_negative": tn},
            "precision": wilson_interval(tp, tp + fp),
            "recall": wilson_interval(tp, tp + fn),
        }
    return per_reason, matrix


def _calibration_error(rows: Sequence[NoSkillCalibrationSample], *, bins: int = 10):
    if not rows or any(row.confidence is None for row in rows):
        return None, []
    if type(bins) is not int or not 2 <= bins <= 20:
        raise NoSkillCalibrationError("calibration bins must be an integer in [2, 20]")
    grouped = [{"count": 0, "confidence_sum": 0.0, "correct": 0}
               for _ in range(bins)]
    for row in rows:
        confidence = float(row.confidence)
        index = min(bins - 1, int(confidence * bins))
        grouped[index]["count"] += 1
        grouped[index]["confidence_sum"] += confidence
        grouped[index]["correct"] += int(row.predicted_decision == row.expected_decision)
    result = []
    total = len(rows)
    ece = 0.0
    for item in grouped:
        count = item["count"]
        if not count:
            result.append({"count": 0, "mean_confidence": None,
                           "accuracy": None, "absolute_gap": None})
            continue
        mean_conf = item["confidence_sum"] / count
        accuracy = item["correct"] / count
        gap = abs(mean_conf - accuracy)
        ece += (count / total) * gap
        result.append({"count": count, "mean_confidence": round(mean_conf, 6),
                       "accuracy": round(accuracy, 6), "absolute_gap": round(gap, 6)})
    return round(ece, 6), result


def _strata_summary(rows: Sequence[NoSkillCalibrationSample]) -> tuple[dict, dict]:
    report: dict[str, dict[str, dict]] = {}
    coverage: dict[str, float] = {}
    for dimension in CALIBRATION_STRATA:
        groups: dict[str, list[NoSkillCalibrationSample]] = {}
        present = 0
        for row in rows:
            value = row.strata.get(dimension)
            if value is not None:
                present += 1
                groups.setdefault(value, []).append(row)
        coverage[dimension] = round(present / len(rows), 6) if rows else 0.0
        report[dimension] = {}
        for value, group in sorted(groups.items()):
            overall = _binary_summary(group, include_bins=True)
            per_reason, matrix = _reason_summary(group)
            report[dimension][value] = {
                "cases": len(group), "overall": overall,
                "per_reason": per_reason,
                "reason_confusion_matrix": matrix,
            }
    return report, coverage


@dataclass(frozen=True)
class NoSkillCalibrationReceipt:
    """Replayable P15 report; it is never a production authority token."""

    eligible: bool
    status: str
    sample_count: int
    sample_ids: tuple[str, ...]
    samples_digest: str
    minimum_sample_count: int
    minimum_reason_cases: int
    overall: dict
    per_reason: dict
    reason_confusion_matrix: dict
    strata: dict
    strata_coverage: dict
    confidence_coverage: float
    calibration_error: float | None
    calibration_bins: tuple[dict, ...]
    routing_receipt_coverage: float
    missing: tuple[str, ...]
    failed: tuple[str, ...]
    reasons: tuple[str, ...]
    evaluation_only: bool = True
    canonical_memory_mutation: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": NO_SKILL_CALIBRATION_VERSION,
            "eligible": self.eligible, "status": self.status,
            "sample_count": self.sample_count, "sample_ids": list(self.sample_ids),
            "samples_digest": self.samples_digest,
            "minimum_sample_count": self.minimum_sample_count,
            "minimum_reason_cases": self.minimum_reason_cases,
            "overall": self.overall, "per_reason": self.per_reason,
            "reason_confusion_matrix": self.reason_confusion_matrix,
            "strata": self.strata, "strata_coverage": self.strata_coverage,
            "confidence_coverage": self.confidence_coverage,
            "calibration_error": self.calibration_error,
            "calibration_bins": list(self.calibration_bins),
            "routing_receipt_coverage": self.routing_receipt_coverage,
            "missing": list(self.missing), "failed": list(self.failed),
            "reasons": list(self.reasons), "evaluation_only": self.evaluation_only,
            "canonical_memory_mutation": self.canonical_memory_mutation,
        }

    @property
    def receipt_digest(self) -> str:
        return "sha256:" + hashlib.sha256(
            stable_dumps(self.to_dict()).encode()).hexdigest()

    @property
    def receipt_id(self) -> str:
        return "no_skill_calibration_" + self.receipt_digest.split(":", 1)[1][:24]

    @classmethod
    def from_dict(cls, payload: object) -> "NoSkillCalibrationReceipt":
        if not isinstance(payload, Mapping):
            raise NoSkillCalibrationError("calibration receipt must be an object")
        required = {
            "version", "eligible", "status", "sample_count", "sample_ids",
            "samples_digest", "minimum_sample_count", "minimum_reason_cases",
            "overall", "per_reason", "reason_confusion_matrix", "strata",
            "strata_coverage", "confidence_coverage", "calibration_error",
            "calibration_bins", "routing_receipt_coverage", "missing", "failed", "reasons",
            "evaluation_only", "canonical_memory_mutation",
        }
        if not required <= set(payload):
            raise NoSkillCalibrationError("calibration receipt is missing required fields")
        if payload.get("version") != NO_SKILL_CALIBRATION_VERSION:
            raise NoSkillCalibrationError("calibration receipt version mismatch")
        try:
            receipt = cls(
                eligible=payload["eligible"], status=payload["status"],
                sample_count=payload["sample_count"],
                sample_ids=tuple(payload["sample_ids"]),
                samples_digest=payload["samples_digest"],
                minimum_sample_count=payload["minimum_sample_count"],
                minimum_reason_cases=payload["minimum_reason_cases"],
                overall=dict(payload["overall"]), per_reason=dict(payload["per_reason"]),
                reason_confusion_matrix=dict(payload["reason_confusion_matrix"]),
                strata=dict(payload["strata"]), strata_coverage=dict(payload["strata_coverage"]),
                confidence_coverage=payload["confidence_coverage"],
                calibration_error=payload["calibration_error"],
                calibration_bins=tuple(dict(item) for item in payload["calibration_bins"]),
                routing_receipt_coverage=payload["routing_receipt_coverage"],
                missing=tuple(payload["missing"]), failed=tuple(payload["failed"]),
                reasons=tuple(payload["reasons"]),
                evaluation_only=payload["evaluation_only"],
                canonical_memory_mutation=payload["canonical_memory_mutation"],
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise NoSkillCalibrationError("calibration receipt fields are malformed") from exc
        if type(receipt.eligible) is not bool or receipt.status not in {
                "PASS", "FAIL", "NOT_ESTABLISHED"}:
            raise NoSkillCalibrationError("calibration receipt status is malformed")
        if receipt.evaluation_only is not True or receipt.canonical_memory_mutation != "none":
            raise NoSkillCalibrationError("calibration receipt is not evaluation-only")
        expected_status = ("FAIL" if receipt.failed else
                           "NOT_ESTABLISHED" if receipt.missing else "PASS")
        if receipt.status != expected_status or receipt.eligible != (receipt.status == "PASS"):
            raise NoSkillCalibrationError("calibration receipt eligibility projection mismatch")
        if type(receipt.sample_count) is not int or receipt.sample_count < 1:
            raise NoSkillCalibrationError("calibration receipt sample_count is invalid")
        if len(receipt.sample_ids) != receipt.sample_count or any(
                type(item) is not str or not item for item in receipt.sample_ids):
            raise NoSkillCalibrationError("calibration receipt sample_ids are invalid")
        if len(set(receipt.sample_ids)) != len(receipt.sample_ids):
            raise NoSkillCalibrationError("calibration receipt sample_ids are duplicated")
        _unit(receipt.confidence_coverage, "confidence_coverage")
        _unit(receipt.routing_receipt_coverage, "routing_receipt_coverage")
        if receipt.calibration_error is not None:
            _unit(receipt.calibration_error, "calibration_error")
        if payload.get("receipt_digest") is not None and payload["receipt_digest"] != receipt.receipt_digest:
            raise NoSkillCalibrationError("calibration receipt digest mismatch")
        return receipt


def evaluate_no_skill_calibration(
        samples: Sequence[NoSkillCalibrationSample | Mapping], *,
        minimum_sample_count: int = 20, minimum_reason_cases: int = 1,
        calibration_bins: int = 10) -> NoSkillCalibrationReceipt:
    """Evaluate explicit P15 labels and return a fail-closed receipt."""
    minimum_sample_count = _positive_int(minimum_sample_count, "minimum_sample_count")
    minimum_reason_cases = _positive_int(minimum_reason_cases, "minimum_reason_cases")
    rows = _normalise_samples(samples)
    digest = "sha256:" + hashlib.sha256(stable_dumps(
        [row.to_dict() for row in rows]).encode()).hexdigest()
    overall = _binary_summary(rows, include_bins=True)
    per_reason, matrix = _reason_summary(rows)
    strata, strata_coverage = _strata_summary(rows)
    ece, bins = _calibration_error(rows, bins=calibration_bins)
    confidence_coverage = round(
        sum(row.confidence is not None for row in rows) / len(rows), 6)
    routing_receipt_coverage = round(
        sum(row.routing_receipt_id is not None for row in rows) / len(rows), 6)
    missing: set[str] = set()
    failed: set[str] = set()
    reasons: list[str] = []
    if len(rows) < minimum_sample_count:
        missing.add("minimum_sample_count")
        reasons.append("minimum_sample_count_not_met")
    for reason in CALIBRATION_REASONS:
        support = per_reason[reason]["support"]
        if support < minimum_reason_cases:
            missing.add(f"minimum_reason_cases:{reason}")
            reasons.append(f"minimum_reason_cases_not_met:{reason}")
    if confidence_coverage < 1.0:
        missing.add("confidence_coverage")
        reasons.append("confidence_required_for_calibration_error")
    if routing_receipt_coverage < 1.0:
        missing.add("routing_receipt_coverage")
        reasons.append("routing_receipt_required_for_calibration")
    for dimension, coverage in strata_coverage.items():
        if coverage < 1.0:
            missing.add(f"strata_coverage:{dimension}")
            reasons.append(f"stratum_required:{dimension}")
    status = "PASS" if not missing and not failed else (
        "FAIL" if failed else "NOT_ESTABLISHED")
    return NoSkillCalibrationReceipt(
        eligible=status == "PASS", status=status, sample_count=len(rows),
        sample_ids=tuple(row.case_id for row in rows), samples_digest=digest,
        minimum_sample_count=minimum_sample_count,
        minimum_reason_cases=minimum_reason_cases, overall=overall,
        per_reason=per_reason, reason_confusion_matrix=matrix, strata=strata,
        strata_coverage=strata_coverage, confidence_coverage=confidence_coverage,
        calibration_error=ece, calibration_bins=tuple(bins),
        routing_receipt_coverage=routing_receipt_coverage,
        missing=tuple(sorted(missing)), failed=tuple(sorted(failed)),
        reasons=tuple(sorted(set(reasons))), evaluation_only=True,
        canonical_memory_mutation="none")


__all__ = [
    "NO_SKILL_CALIBRATION_VERSION", "CALIBRATION_DECISIONS",
    "CALIBRATION_REASONS", "CALIBRATION_LABELS", "CALIBRATION_STRATA",
    "NoSkillCalibrationError", "NoSkillCalibrationSample",
    "NoSkillCalibrationReceipt", "wilson_interval",
    "build_no_skill_calibration_samples", "evaluate_no_skill_calibration",
]
