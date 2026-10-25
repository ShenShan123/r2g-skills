"""Evaluation-only planning for routed-policy MIR sample sizes.

The production readiness gate intentionally keeps ``max_mir_upper_ci=0.0``
as its strict historical default.  A finite Wilson upper confidence bound is
always positive, even when every observed case is non-harmful, so that policy
cannot be established by merely adding a finite number of observations.

This module makes that fact and any explicitly chosen non-zero policy
thresholds inspectable.  It never changes a production threshold, imports a
canonical memory, or grants authority.  The planner assumes that no further
harmful observations are added while accumulating the requested denominator;
the assumption is recorded in the content-addressed receipt.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from tehm.ids import stable_dumps

from .no_skill_calibration import wilson_interval


MIR_SAMPLE_PLAN_VERSION = "r3-policy-mir-sample-plan-v1"
# Include the registered production default explicitly.  It is deliberately
# reported as unattainable with a finite Wilson sample rather than silently
# replacing it with a more permissive planning threshold.
DEFAULT_MIR_SAMPLE_THRESHOLDS = (0.0, 0.10, 0.05, 0.02, 0.01)
DEFAULT_MAX_SEARCH_CASES = 1_000_000


class MIRError(ValueError):
    """Malformed or contradictory MIR sample-planning evidence."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _unit(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise MIRError(f"{name} must be a finite number in [0, 1]")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MIRError(f"{name} must be a finite number in [0, 1]") from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise MIRError(f"{name} must be a finite number in [0, 1]")
    return number


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise MIRError(f"{name} must be a positive integer")
    return value


def _nonnegative_int(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise MIRError(f"{name} must be a non-negative integer")
    return value


def _digest_text(value: object, name: str) -> str:
    if type(value) is not str or not value.startswith("sha256:") or \
            len(value) <= len("sha256:"):
        raise MIRError(f"{name} must be a sha256 digest")
    return value


def _minimum_known_cases(*, harmful_cases: int, threshold: float,
                         max_search_cases: int) -> tuple[int | None, str]:
    """Find the first denominator whose rounded Wilson upper bound is below.

    The strict inequality mirrors :func:`production_readiness._interference`.
    ``None`` is intentional for the zero threshold and for a bounded search
    that cannot establish an extremely small non-zero threshold.
    """
    if threshold == 0.0:
        return None, "finite_wilson_upper_bound_is_positive"
    lower = max(1, harmful_cases)
    upper = lower
    while upper < max_search_cases and \
            wilson_interval(harmful_cases, upper)["upper"] >= threshold:
        upper = min(max_search_cases, upper * 2)
    if wilson_interval(harmful_cases, upper)["upper"] >= threshold:
        return None, "search_limit_reached"
    lo, hi = lower, upper
    while lo < hi:
        mid = (lo + hi) // 2
        if wilson_interval(harmful_cases, mid)["upper"] < threshold:
            hi = mid
        else:
            lo = mid + 1
    return lo, "threshold_reached"


def _normalize_thresholds(thresholds: Sequence[float]) -> tuple[float, ...]:
    if isinstance(thresholds, (str, bytes)) or not isinstance(thresholds, Sequence) \
            or not thresholds:
        raise MIRError("thresholds must be a non-empty sequence")
    normalized = tuple(sorted({_unit(value, "threshold") for value in thresholds}))
    if len(normalized) != len(thresholds):
        raise MIRError("thresholds must not contain duplicates")
    return normalized


def _normalize_evidence(evidence: Mapping | None, *, known_cases: int,
                        harmful_cases: int) -> dict | None:
    if evidence is None:
        return None
    if not isinstance(evidence, Mapping):
        raise MIRError("current MIR evidence must be an object")
    result = dict(evidence)
    if "path" in result and type(result["path"]) is not str:
        raise MIRError("current MIR evidence path is malformed")
    if "sha256" in result:
        result["sha256"] = _digest_text(result["sha256"], "current MIR evidence sha256")
    if "receipt_digest" in result:
        result["receipt_digest"] = _digest_text(
            result["receipt_digest"], "current MIR receipt_digest")
    if "known_cases" in result and result["known_cases"] != known_cases:
        raise MIRError("current MIR evidence known_cases disagrees with metrics")
    if "harmful_cases" in result and result["harmful_cases"] != harmful_cases:
        raise MIRError("current MIR evidence harmful_cases disagrees with metrics")
    result["known_cases"] = known_cases
    result["harmful_cases"] = harmful_cases
    return result


@dataclass(frozen=True)
class MIRSamplePlanReceipt:
    """Content-addressed, evaluation-only MIR sampling plan."""

    current_known_cases: int
    current_harmful_cases: int
    current_upper_ci: float | None
    thresholds: tuple[dict, ...]
    max_search_cases: int
    current_evidence: dict | None = None
    version: str = MIR_SAMPLE_PLAN_VERSION
    confidence: float = 0.95
    assumption: str = "no_additional_harmful_cases"
    evaluation_only: bool = True
    canonical_memory_mutation: str = "none"
    production_integration: str = "not_attempted"
    memory_docs_submitted: bool = False

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "current_known_cases": self.current_known_cases,
            "current_harmful_cases": self.current_harmful_cases,
            "current_upper_ci": self.current_upper_ci,
            "thresholds": [dict(row) for row in self.thresholds],
            "max_search_cases": self.max_search_cases,
            "current_evidence": (dict(self.current_evidence)
                                  if self.current_evidence is not None else None),
            "confidence": self.confidence,
            "assumption": self.assumption,
            "evaluation_only": self.evaluation_only,
            "canonical_memory_mutation": self.canonical_memory_mutation,
            "production_integration": self.production_integration,
            "memory_docs_submitted": self.memory_docs_submitted,
        }

    @property
    def receipt_digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def receipt_id(self) -> str:
        return "r3_policy_mir_sample_plan_" + self.receipt_digest.split(":", 1)[1][:24]

    @classmethod
    def from_dict(cls, payload: Mapping) -> "MIRSamplePlanReceipt":
        if not isinstance(payload, Mapping):
            raise MIRError("MIR sample plan receipt must be an object")
        required = {
            "version", "current_known_cases", "current_harmful_cases",
            "current_upper_ci", "thresholds", "max_search_cases",
            "current_evidence", "confidence", "assumption", "evaluation_only",
            "canonical_memory_mutation", "production_integration",
            "memory_docs_submitted",
        }
        missing = sorted(required - set(payload))
        if missing:
            raise MIRError("MIR sample plan receipt missing " + ", ".join(missing))
        if not isinstance(payload["thresholds"], list):
            raise MIRError("MIR sample plan thresholds must be a list")
        if any(not isinstance(row, Mapping) for row in payload["thresholds"]):
            raise MIRError("MIR sample plan threshold row is malformed")
        rows = tuple(dict(row) for row in payload["thresholds"])
        raw_evidence = payload["current_evidence"]
        if raw_evidence is not None and not isinstance(raw_evidence, Mapping):
            raise MIRError("current MIR evidence must be an object")
        receipt = cls(
            current_known_cases=payload["current_known_cases"],
            current_harmful_cases=payload["current_harmful_cases"],
            current_upper_ci=payload["current_upper_ci"],
            thresholds=rows,
            max_search_cases=payload["max_search_cases"],
            current_evidence=(dict(raw_evidence) if raw_evidence is not None else None),
            version=payload["version"], confidence=payload["confidence"],
            assumption=payload["assumption"],
            evaluation_only=payload["evaluation_only"],
            canonical_memory_mutation=payload["canonical_memory_mutation"],
            production_integration=payload["production_integration"],
            memory_docs_submitted=payload["memory_docs_submitted"],
        )
        _validate_receipt(receipt)
        if payload.get("receipt_digest") is not None and \
                payload["receipt_digest"] != receipt.receipt_digest:
            raise MIRError("MIR sample plan receipt digest mismatch")
        return receipt


def _validate_receipt(receipt: MIRSamplePlanReceipt) -> None:
    if receipt.version != MIR_SAMPLE_PLAN_VERSION:
        raise MIRError("MIR sample plan version mismatch")
    _nonnegative_int(receipt.current_known_cases, "current_known_cases")
    _nonnegative_int(receipt.current_harmful_cases, "current_harmful_cases")
    if receipt.current_harmful_cases > receipt.current_known_cases:
        raise MIRError("current_harmful_cases cannot exceed current_known_cases")
    _positive_int(receipt.max_search_cases, "max_search_cases")
    if receipt.current_known_cases > receipt.max_search_cases:
        raise MIRError("current_known_cases exceeds max_search_cases")
    if receipt.current_upper_ci is not None:
        _unit(receipt.current_upper_ci, "current_upper_ci")
    if receipt.confidence != 0.95:
        raise MIRError("only 95% Wilson intervals are supported")
    if receipt.assumption != "no_additional_harmful_cases":
        raise MIRError("MIR sample plan assumption is not supported")
    if receipt.evaluation_only is not True or receipt.canonical_memory_mutation != "none" or \
            receipt.production_integration != "not_attempted" or \
            receipt.memory_docs_submitted is not False:
        raise MIRError("MIR sample plan crosses an authority/docs boundary")
    expected_upper = (wilson_interval(receipt.current_harmful_cases,
                                      receipt.current_known_cases)["upper"]
                      if receipt.current_known_cases else None)
    if receipt.current_upper_ci != expected_upper:
        raise MIRError("current MIR upper confidence bound does not replay")
    if receipt.current_evidence is not None:
        _normalize_evidence(receipt.current_evidence,
                            known_cases=receipt.current_known_cases,
                            harmful_cases=receipt.current_harmful_cases)
    thresholds = []
    seen = set()
    for row in receipt.thresholds:
        if not isinstance(row, Mapping):
            raise MIRError("MIR sample plan threshold row is malformed")
        threshold = _unit(row.get("threshold"), "threshold")
        if threshold in seen:
            raise MIRError("MIR sample plan thresholds contain duplicates")
        seen.add(threshold)
        required = row.get("minimum_known_cases")
        if required is not None:
            _positive_int(required, "minimum_known_cases")
            if required > receipt.max_search_cases:
                raise MIRError("minimum_known_cases exceeds max_search_cases")
        status = row.get("status")
        if status not in {"threshold_reached", "finite_wilson_upper_bound_is_positive",
                          "search_limit_reached"}:
            raise MIRError("MIR sample plan threshold status is malformed")
        expected_required, expected_status = _minimum_known_cases(
            harmful_cases=receipt.current_harmful_cases, threshold=threshold,
            max_search_cases=receipt.max_search_cases)
        if required != expected_required or status != expected_status:
            raise MIRError("MIR sample plan threshold row does not replay")
        observed = row.get("upper_ci_at_minimum")
        if required is None:
            if observed is not None:
                raise MIRError("MIR sample plan has an upper bound without a plan")
        elif observed != wilson_interval(receipt.current_harmful_cases,
                                          required)["upper"]:
            raise MIRError("MIR sample plan upper bound does not replay")
        thresholds.append(threshold)
    if tuple(thresholds) != tuple(sorted(thresholds)):
        raise MIRError("MIR sample plan thresholds are not normalized")


def build_mir_sample_plan(*, current_known_cases: int,
                          current_harmful_cases: int,
                          thresholds: Sequence[float] = DEFAULT_MIR_SAMPLE_THRESHOLDS,
                          max_search_cases: int = DEFAULT_MAX_SEARCH_CASES,
                          current_evidence: Mapping | None = None,
                          output: Path | None = None) -> dict:
    """Create a replayable sample-size plan without changing any gate."""
    _nonnegative_int(current_known_cases, "current_known_cases")
    _nonnegative_int(current_harmful_cases, "current_harmful_cases")
    if current_harmful_cases > current_known_cases:
        raise MIRError("current_harmful_cases cannot exceed current_known_cases")
    _positive_int(max_search_cases, "max_search_cases")
    normalized = _normalize_thresholds(thresholds)
    current_upper = (wilson_interval(current_harmful_cases, current_known_cases)["upper"]
                     if current_known_cases else None)
    rows = []
    for threshold in normalized:
        required, status = _minimum_known_cases(
            harmful_cases=current_harmful_cases, threshold=threshold,
            max_search_cases=max_search_cases)
        rows.append({
            "threshold": threshold,
            "minimum_known_cases": required,
            "additional_known_cases": (max(0, required - current_known_cases)
                                        if required is not None else None),
            "upper_ci_at_minimum": (wilson_interval(current_harmful_cases, required)["upper"]
                                     if required is not None else None),
            "status": status,
        })
    receipt = MIRSamplePlanReceipt(
        current_known_cases=current_known_cases,
        current_harmful_cases=current_harmful_cases,
        current_upper_ci=current_upper, thresholds=tuple(rows),
        max_search_cases=max_search_cases,
        current_evidence=_normalize_evidence(
            current_evidence, known_cases=current_known_cases,
            harmful_cases=current_harmful_cases),
    )
    _validate_receipt(receipt)
    payload = {**receipt.to_dict(), "receipt_id": receipt.receipt_id,
               "receipt_digest": receipt.receipt_digest}
    report = {
        "mir_sample_plan": payload,
        "receipt_id": receipt.receipt_id,
        "receipt_digest": receipt.receipt_digest,
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "production_integration": "not_attempted",
        "memory_docs_submitted": False,
    }
    if output is not None:
        output = Path(output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def replay_mir_sample_plan(report_path: Path) -> MIRSamplePlanReceipt:
    """Replay a sample plan and verify its content-addressed contract."""
    report_path = Path(report_path).expanduser().resolve()
    try:
        report = json.loads(report_path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MIRError(f"MIR sample plan is not valid JSON: {report_path}") from exc
    if not isinstance(report, Mapping):
        raise MIRError("MIR sample plan report must be an object")
    payload = report.get("mir_sample_plan")
    receipt = MIRSamplePlanReceipt.from_dict(payload)
    if report.get("receipt_id") != receipt.receipt_id or \
            report.get("receipt_digest") != receipt.receipt_digest:
        raise MIRError("MIR sample plan report digest/id mismatch")
    if report.get("evaluation_only") is not True or \
            report.get("canonical_memory_mutation") != "none" or \
            report.get("production_integration") != "not_attempted" or \
            report.get("memory_docs_submitted") is not False:
        raise MIRError("MIR sample plan report crosses an authority/docs boundary")
    evidence = receipt.current_evidence
    if evidence is not None and evidence.get("path") is not None:
        evidence_path = Path(evidence["path"]).expanduser()
        if not evidence_path.is_absolute():
            evidence_path = (report_path.parent / evidence_path).resolve()
        try:
            actual_digest = "sha256:" + hashlib.sha256(
                evidence_path.read_bytes()).hexdigest()
        except OSError as exc:
            raise MIRError(
                f"bound current MIR evidence is unreadable: {evidence_path}") from exc
        if evidence.get("sha256") != actual_digest:
            raise MIRError("bound current MIR evidence digest mismatch")
        if evidence.get("version") == "r3-policy-mir-v2":
            # Import lazily to keep the evaluation modules acyclic at import
            # time: policy_mir itself depends on the Wilson helper only.
            from .policy_mir import PolicyMIRError, replay_routed_policy_mir
            try:
                raw_evidence = json.loads(evidence_path.read_text())
                aggregate = raw_evidence.get("policy_mir") or raw_evidence
                metrics = replay_routed_policy_mir(
                    aggregate, base=evidence_path.parent)
            except (OSError, UnicodeError, json.JSONDecodeError,
                    PolicyMIRError) as exc:
                raise MIRError(
                    f"bound current MIR evidence cannot replay: {evidence_path}") from exc
            if (metrics["total_cases"] != receipt.current_known_cases or
                    metrics["harmful_cases"] != receipt.current_harmful_cases or
                    metrics["upper_ci"] != receipt.current_upper_ci or
                    evidence.get("receipt_digest") != aggregate.get("receipt_digest")):
                raise MIRError("bound current MIR evidence metrics drifted")
    return receipt


__all__ = [
    "MIR_SAMPLE_PLAN_VERSION", "DEFAULT_MIR_SAMPLE_THRESHOLDS",
    "DEFAULT_MAX_SEARCH_CASES", "MIRError", "MIRSamplePlanReceipt",
    "build_mir_sample_plan", "replay_mir_sample_plan",
]
