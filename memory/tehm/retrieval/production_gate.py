"""Fail-closed empirical gate for a future production memory router.

P9 is an authority seam, not a runtime switch.  The evaluator consumes
explicit, paired oracle metrics and content-bound authority/rollback evidence,
then emits a replayable receipt.  It never writes SQLite, promotes a rule or
asset, or changes :mod:`memory_router` (which remains shadow-only).
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tehm.ids import stable_dumps
from tehm.evaluation.no_skill_calibration import (
    NoSkillCalibrationError, NoSkillCalibrationReceipt, wilson_interval,
)


PRODUCTION_GATE_VERSION = "production-memory-gate-v1"
PRODUCTION_GATE_GATES = (
    "efficacy",
    "no_skill_calibration",
    "candidate_pool",
    "authority",
    "rollback",
    "evidence",
)
PRODUCTION_GATE_STATUS = ("NOT_ESTABLISHED", "FAIL", "PASS")


class ProductionGateError(ValueError):
    """Malformed P9 evidence cannot be evaluated safely."""


def _finite_number(value: object, name: str, *, unit: bool = True) -> float:
    if isinstance(value, bool):
        raise ProductionGateError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ProductionGateError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ProductionGateError(f"{name} must be a finite number")
    if unit and not 0.0 <= number <= 1.0:
        raise ProductionGateError(f"{name} must be between 0 and 1")
    return number


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ProductionGateError(f"{name} must be a positive integer")
    return value


def _pick(source: Mapping, *names: str):
    for name in names:
        if name in source:
            return source[name]
    return None


def _flatten_evidence(evidence: Mapping | None) -> dict:
    if evidence is None:
        return {}
    if hasattr(evidence, "to_dict"):
        evidence = evidence.to_dict()
    if not isinstance(evidence, Mapping):
        raise ProductionGateError("production gate evidence must be an object")
    result = dict(evidence)
    # Reports naturally group these metrics.  Flattening is deterministic and
    # keeps the public evaluator usable with either report or direct inputs.
    for group_name in ("empirical", "metrics", "candidate_pool_metrics",
                       "no_skill_calibration"):
        group = evidence.get(group_name)
        if hasattr(group, "to_dict"):
            group = group.to_dict()
        if isinstance(group, Mapping):
            for key, value in group.items():
                result.setdefault(key, value)
    return result


def _normalise_refs(value: object) -> tuple[dict, ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping) or isinstance(value, (str, bytes)):
        raise ProductionGateError("evidence_refs must be a sequence of objects")
    if not isinstance(value, Sequence):
        raise ProductionGateError("evidence_refs must be a sequence of objects")
    refs: list[dict] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise ProductionGateError("each evidence_ref must be an object")
        ref = dict(item)
        locator = _pick(ref, "path", "uri", "id", "receipt_id")
        digest = _pick(ref, "sha256", "digest", "receipt_digest")
        if not isinstance(locator, str) or not locator.strip():
            raise ProductionGateError("evidence_ref requires a non-empty locator")
        if not isinstance(digest, str) or not digest.strip():
            raise ProductionGateError("evidence_ref requires a content digest")
        key = stable_dumps(ref)
        if key in seen:
            raise ProductionGateError("evidence_refs contain duplicates")
        seen.add(key)
        refs.append(ref)
    return tuple(refs)


def _proof_bool(source: Mapping, name: str, *, aliases: tuple[str, ...] = ()) -> tuple[bool | None, bool]:
    """Return (value, malformed) while distinguishing absent from false."""
    keys = (name, *aliases)
    present = [key for key in keys if key in source]
    if not present:
        return None, False
    value = source[present[0]]
    return (value if type(value) is bool else False), type(value) is not bool


def _authority_check(source: Mapping) -> tuple[bool | None, list[str]]:
    reasons: list[str] = []
    nested = source.get("authority")
    authority = dict(nested) if isinstance(nested, Mapping) else {}
    verified = _pick(authority, "verified", "eligible")
    if verified is None:
        verified = _pick(source, "authority_verified", "production_authority_verified")
    if verified is None:
        return None, reasons
    if type(verified) is not bool:
        return False, ["authority_verification_malformed"]
    if not verified:
        return False, []
    receipt_id = _pick(authority, "receipt_id", "authority_receipt_id")
    if receipt_id is None:
        receipt_id = _pick(source, "authority_receipt_id", "production_authority_receipt_id")
    receipt_digest = _pick(authority, "receipt_digest", "digest")
    if receipt_digest is None:
        receipt_digest = _pick(source, "authority_receipt_digest")
    if not isinstance(receipt_id, str) or not receipt_id.strip():
        reasons.append("authority_receipt_id_required")
    if not isinstance(receipt_digest, str) or not receipt_digest.strip():
        reasons.append("authority_receipt_digest_required")
    return (False if reasons else True), reasons


def _rollback_check(source: Mapping) -> tuple[bool | None, list[str]]:
    value, malformed = _proof_bool(source, "rollback_verified")
    if value is None:
        return None, []
    if malformed:
        return False, ["rollback_verification_malformed"]
    if not value:
        return False, []
    receipt_id = _pick(source, "rollback_receipt_id", "rollback_id")
    digest = _pick(source, "rollback_receipt_digest", "rollback_digest")
    reasons: list[str] = []
    if not isinstance(receipt_id, str) or not receipt_id.strip():
        reasons.append("rollback_receipt_id_required")
    if not isinstance(digest, str) or not digest.strip():
        reasons.append("rollback_receipt_digest_required")
    return (False if reasons else True), reasons


def _calibration_metric(report: NoSkillCalibrationReceipt, name: str,
                        bound: str) -> float | None:
    value = report.overall.get(name)
    if not isinstance(value, Mapping):
        return None
    raw = value.get(bound)
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and 0.0 <= number <= 1.0 else None


def _mcnemar_regression_guard(source: Mapping, metrics: dict, *, alpha: float) -> tuple[bool, bool]:
    """Return ``(evidence_present, safe)`` for paired repair outcomes.

    ``b`` is baseline-pass/memory-fail and ``c`` is baseline-fail/memory-pass.
    The continuity-corrected McNemar statistic is compared with a two-sided
    chi-square(1) survival function.  Missing discordant counts are not
    guessed from aggregate rates; callers stay on the legacy compatibility
    path unless a complete paired-count witness is supplied.
    """
    fields = {
        "paired": _pick(source, "repair_paired_cases", "paired_repair_cases"),
        "regression": _pick(source, "repair_regression_cases",
                             "baseline_pass_memory_fail_cases"),
        "improvement": _pick(source, "repair_improvement_cases",
                              "baseline_fail_memory_pass_cases"),
    }
    present = any(value is not None for value in fields.values())
    if not present:
        return False, True
    if any(value is None for value in fields.values()):
        raise ProductionGateError(
            "paired repair evidence requires paired, regression, and improvement counts")
    paired = fields["paired"]
    regression = fields["regression"]
    improvement = fields["improvement"]
    if (type(paired) is not int or paired <= 0 or
            type(regression) is not int or regression < 0 or
            type(improvement) is not int or improvement < 0 or
            regression + improvement > paired):
        raise ProductionGateError("paired repair counts are invalid")
    discordant = regression + improvement
    if discordant == 0:
        p_value = 1.0
    else:
        statistic = (abs(regression - improvement) - 1.0) ** 2 / discordant
        p_value = math.erfc(math.sqrt(max(0.0, statistic)) / math.sqrt(2.0))
    significant_regression = regression > improvement and p_value < alpha
    metrics["repair_paired_cases"] = paired
    metrics["repair_regression_cases"] = regression
    metrics["repair_improvement_cases"] = improvement
    metrics["repair_mcnemar"] = {
        "regression_cases": regression, "improvement_cases": improvement,
        "discordant_cases": discordant, "p_value": round(p_value, 6),
        "alpha": alpha, "significant_regression": significant_regression,
    }
    return True, not significant_regression


@dataclass(frozen=True)
class ProductionGateReceipt:
    """Content-addressed, evaluation-only result of the P9 conjunction."""

    eligible: bool
    checks: dict[str, bool]
    gate_status: dict[str, str]
    missing: tuple[str, ...]
    failed: tuple[str, ...]
    not_established: tuple[str, ...]
    metrics: dict
    thresholds: dict
    evidence_refs: tuple[dict, ...]
    reasons: tuple[str, ...]
    evaluation_only: bool = True
    production_integration: str = "not_attempted"

    def to_dict(self) -> dict:
        return {
            "version": PRODUCTION_GATE_VERSION,
            "eligible": self.eligible,
            "checks": dict(self.checks),
            "gate_status": dict(self.gate_status),
            "missing": list(self.missing),
            "failed": list(self.failed),
            "not_established": list(self.not_established),
            "metrics": dict(self.metrics),
            "thresholds": dict(self.thresholds),
            "evidence_refs": [dict(ref) for ref in self.evidence_refs],
            "reasons": list(self.reasons),
            "evaluation_only": self.evaluation_only,
            "production_integration": self.production_integration,
        }

    @property
    def receipt_digest(self) -> str:
        return "sha256:" + hashlib.sha256(
            stable_dumps(self.to_dict()).encode()).hexdigest()

    @property
    def receipt_id(self) -> str:
        return "production_gate_" + self.receipt_digest.split(":", 1)[1][:24]

    @classmethod
    def from_dict(cls, payload: object) -> "ProductionGateReceipt":
        if not isinstance(payload, Mapping):
            raise ProductionGateError("production gate receipt must be an object")
        required = {
            "version", "eligible", "checks", "gate_status", "missing", "failed",
            "not_established", "metrics", "thresholds", "evidence_refs", "reasons",
            "evaluation_only", "production_integration",
        }
        if any(key not in payload for key in required):
            raise ProductionGateError("production gate receipt is missing required fields")
        if payload.get("version") != PRODUCTION_GATE_VERSION:
            raise ProductionGateError("production gate receipt version mismatch")
        try:
            receipt = cls(
                eligible=payload["eligible"], checks=dict(payload["checks"]),
                gate_status=dict(payload["gate_status"]),
                missing=tuple(payload["missing"]), failed=tuple(payload["failed"]),
                not_established=tuple(payload["not_established"]),
                metrics=dict(payload["metrics"]), thresholds=dict(payload["thresholds"]),
                evidence_refs=tuple(dict(item) for item in payload["evidence_refs"]),
                reasons=tuple(payload["reasons"]),
                evaluation_only=payload["evaluation_only"],
                production_integration=payload["production_integration"],
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise ProductionGateError("production gate receipt fields are malformed") from exc
        if type(receipt.eligible) is not bool or receipt.evaluation_only is not True:
            raise ProductionGateError("production gate receipt flags are invalid")
        if receipt.production_integration != "not_attempted":
            raise ProductionGateError("production integration must remain not_attempted")
        _validate_receipt_semantics(receipt)
        if payload.get("receipt_digest") is not None and payload["receipt_digest"] != receipt.receipt_digest:
            raise ProductionGateError("production gate receipt digest mismatch")
        return receipt


def _validate_receipt_semantics(receipt: ProductionGateReceipt) -> None:
    """Check the conjunction/status projection independently of the digest."""
    names = tuple(PRODUCTION_GATE_GATES)
    if set(receipt.checks) != set(names) or any(
            type(receipt.checks[name]) is not bool for name in names):
        raise ProductionGateError("production gate checks are malformed")
    if set(receipt.gate_status) != set(names) or any(
            receipt.gate_status[name] not in PRODUCTION_GATE_STATUS for name in names):
        raise ProductionGateError("production gate statuses are malformed")
    expected_missing = tuple(sorted(
        name for name in names if receipt.gate_status[name] == "NOT_ESTABLISHED"))
    expected_failed = tuple(sorted(
        name for name in names if receipt.gate_status[name] == "FAIL"))
    expected_not_established = tuple(
        name for name in names if receipt.gate_status[name] == "NOT_ESTABLISHED")
    if (tuple(receipt.missing) != expected_missing or
            tuple(receipt.failed) != expected_failed or
            tuple(receipt.not_established) != expected_not_established):
        raise ProductionGateError("production gate status projection mismatch")
    if receipt.eligible is not all(receipt.checks.values()):
        raise ProductionGateError("production gate eligible projection mismatch")
    if any(receipt.gate_status[name] == "PASS" and not receipt.checks[name]
           for name in names):
        raise ProductionGateError("production gate PASS check mismatch")
    try:
        _normalise_refs(receipt.evidence_refs)
    except ProductionGateError as exc:
        raise ProductionGateError("production gate evidence_refs are malformed") from exc
    if not isinstance(receipt.metrics, Mapping) or not isinstance(receipt.thresholds, Mapping):
        raise ProductionGateError("production gate metrics/thresholds are malformed")
    if any(type(reason) is not str or not reason.strip() for reason in receipt.reasons):
        raise ProductionGateError("production gate reasons are malformed")


def evaluate_production_gate(
        evidence: Mapping | None = None, *,
        max_memory_interference_rate: float = 0.0,
        min_candidate_diversity: float = 0.5,
        min_no_skill_precision: float = 0.80,
        min_no_skill_recall: float = 0.80,
        max_no_skill_calibration_error: float = 0.20,
        repair_regression_alpha: float = 0.05,
) -> ProductionGateReceipt:
    """Evaluate P9 empirical evidence without mutating any runtime state.

    Efficacy is an explicit OR: paired harmful activation must strictly
    decrease, *or* repair rate must increase under an explicit controlled-harm
    marker.  Both branches require concrete rates; caller-provided booleans or
    a single pass count are not accepted.  All absent metrics are
    ``NOT_ESTABLISHED`` and never become production authority.
    """
    source = _flatten_evidence(evidence)
    thresholds = {
        "max_memory_interference_rate": _finite_number(
            max_memory_interference_rate, "max_memory_interference_rate"),
        "min_candidate_diversity": _finite_number(
            min_candidate_diversity, "min_candidate_diversity"),
        "min_no_skill_precision": _finite_number(
            min_no_skill_precision, "min_no_skill_precision"),
        "min_no_skill_recall": _finite_number(
            min_no_skill_recall, "min_no_skill_recall"),
        "max_no_skill_calibration_error": _finite_number(
            max_no_skill_calibration_error, "max_no_skill_calibration_error"),
        "repair_regression_alpha": _finite_number(
            repair_regression_alpha, "repair_regression_alpha"),
    }
    checks = {name: False for name in PRODUCTION_GATE_GATES}
    reasons: list[str] = []
    missing: set[str] = set()
    failed: set[str] = set()
    metrics: dict = {}

    # 1. Empirical efficacy.  The two accepted branches are deliberately
    # derived from paired rates rather than a caller-supplied ``gain=True``.
    no_mem_harm = _pick(source, "baseline_harmful_activation_rate",
                         "no_memory_harmful_activation_rate",
                         "baseline_harmful_rate", "no_memory_harm_rate")
    mem_harm = _pick(source, "memory_harmful_activation_rate",
                     "memory_harmful_rate", "candidate_memory_harm_rate")
    baseline_repair = _pick(source, "baseline_repair_rate",
                            "no_memory_repair_rate")
    memory_repair = _pick(source, "memory_repair_rate")
    controlled_harm = _pick(source, "controlled_harm",
                            "controlled_harm_evidence")
    efficacy_present = any(value is not None for value in (
        no_mem_harm, mem_harm, baseline_repair, memory_repair, controlled_harm))
    try:
        if ((baseline_repair is None) != (memory_repair is None)):
            raise ProductionGateError(
                "baseline_repair_rate and memory_repair_rate must be provided together")
        harm_branch = False
        repair_branch = False
        if no_mem_harm is not None:
            metrics["baseline_harmful_activation_rate"] = _finite_number(
                no_mem_harm, "baseline_harmful_activation_rate")
        if mem_harm is not None:
            metrics["memory_harmful_activation_rate"] = _finite_number(
                mem_harm, "memory_harmful_activation_rate")
        if no_mem_harm is not None and mem_harm is not None:
            harm_branch = metrics["memory_harmful_activation_rate"] < metrics[
                "baseline_harmful_activation_rate"]
        if baseline_repair is not None:
            metrics["baseline_repair_rate"] = _finite_number(
                baseline_repair, "baseline_repair_rate")
        if memory_repair is not None:
            metrics["memory_repair_rate"] = _finite_number(
                memory_repair, "memory_repair_rate")
        if controlled_harm is not None and type(controlled_harm) is not bool:
            raise ProductionGateError("controlled_harm must be a boolean")
        repair_rate_safe = True
        if baseline_repair is not None and memory_repair is not None:
            # A harm decrease is not a Pareto improvement if repair collapses.
            # The tolerance is intentionally zero until a caller freezes a
            # domain-specific epsilon in a future gate version.
            repair_rate_safe = metrics["memory_repair_rate"] >= metrics[
                "baseline_repair_rate"]
        repair_evidence_present, repair_regression_safe = _mcnemar_regression_guard(
            source, metrics, alpha=thresholds["repair_regression_alpha"])
        metrics["repair_regression_guard"] = (
            "paired_mcnemar_safe" if repair_evidence_present and repair_regression_safe
            else "paired_mcnemar_regression" if repair_evidence_present
            else "not_provided")
        repair_rate_safe = repair_rate_safe and repair_regression_safe
        if (baseline_repair is not None and memory_repair is not None and
                controlled_harm is True):
            repair_branch = (metrics["memory_repair_rate"] >
                             metrics["baseline_repair_rate"] and repair_rate_safe)
        if no_mem_harm is not None and mem_harm is not None:
            harm_branch = harm_branch and repair_rate_safe
    except ProductionGateError as exc:
        reasons.append(f"efficacy:{exc}")
        failed.add("efficacy")
    else:
        if not efficacy_present:
            missing.add("efficacy")
            reasons.append("efficacy_evidence_required")
        elif not (harm_branch or repair_branch):
            failed.add("efficacy")
            reasons.append("neither_harm_reduction_nor_controlled_harm_repair_gain")
        else:
            checks["efficacy"] = True
            metrics["efficacy_branch"] = (
                "harmful_activation_decrease" if harm_branch else "controlled_harm_repair_gain")

    # 2. NO_SKILL calibration.  A Revision2 receipt is preferred: it carries
    # reason-stratified confusion, denominator-aware Wilson intervals and ECE.
    # The scalar path remains a compatibility adapter for pre-P15 reports and
    # deliberately does not claim CI-backed calibration.
    structured_calibration = _pick(
        source, "no_skill_calibration_report", "no_skill_calibration")
    if structured_calibration is not None:
        try:
            if hasattr(structured_calibration, "to_dict"):
                structured_calibration = structured_calibration.to_dict()
            report = NoSkillCalibrationReceipt.from_dict(structured_calibration)
            metrics["no_skill_calibration_report_digest"] = report.receipt_digest
            metrics["no_skill_cases"] = report.sample_count
            metrics["no_skill_confidence_coverage"] = report.confidence_coverage
            metrics["no_skill_routing_receipt_coverage"] = report.routing_receipt_coverage
            metrics["no_skill_reason_metrics"] = report.per_reason
            metrics["no_skill_reason_confusion_matrix"] = report.reason_confusion_matrix
            precision_lcb = _calibration_metric(report, "precision", "lower")
            recall_lcb = _calibration_metric(report, "recall", "lower")
            precision_point = _calibration_metric(report, "precision", "point")
            recall_point = _calibration_metric(report, "recall", "point")
            if precision_lcb is None or recall_lcb is None:
                raise NoSkillCalibrationError("overall precision/recall CI is unavailable")
            metrics["no_skill_precision"] = precision_point
            metrics["no_skill_recall"] = recall_point
            metrics["no_skill_precision_lower_ci"] = precision_lcb
            metrics["no_skill_recall_lower_ci"] = recall_lcb
            if report.calibration_error is None:
                missing.add("no_skill_calibration")
                reasons.append("no_skill_calibration_error_required")
            else:
                metrics["no_skill_calibration_error"] = report.calibration_error
            if report.missing:
                missing.add("no_skill_calibration")
                reasons.extend(f"no_skill_calibration:{item}" for item in report.missing)
            if report.failed:
                failed.add("no_skill_calibration")
                reasons.extend(f"no_skill_calibration:{item}" for item in report.failed)
            checks["no_skill_calibration"] = (
                report.eligible and not report.missing and not report.failed and
                precision_lcb >= thresholds["min_no_skill_precision"] and
                recall_lcb >= thresholds["min_no_skill_recall"] and
                report.calibration_error is not None and
                report.calibration_error <= thresholds["max_no_skill_calibration_error"])
            if (not checks["no_skill_calibration"] and
                    not report.missing and not report.failed):
                failed.add("no_skill_calibration")
                reasons.append("no_skill_calibration_lower_ci_below_threshold")
        except (NoSkillCalibrationError, TypeError, ValueError) as exc:
            failed.add("no_skill_calibration")
            reasons.append(f"no_skill_calibration:{exc}")
    else:
        precision = _pick(source, "no_skill_precision", "abstention_precision")
        recall = _pick(source, "no_skill_recall", "abstention_recall")
        no_skill_cases = _pick(source, "no_skill_cases", "abstention_cases")
        calibration_error = _pick(source, "no_skill_calibration_error",
                                  "abstention_calibration_error", "no_skill_ece")
        try:
            if precision is None or recall is None or no_skill_cases is None:
                missing.add("no_skill_calibration")
                reasons.append("no_skill_precision_recall_and_cases_required")
            else:
                _positive_int(no_skill_cases, "no_skill_cases")
                metrics["no_skill_precision"] = _finite_number(precision, "no_skill_precision")
                metrics["no_skill_recall"] = _finite_number(recall, "no_skill_recall")
                metrics["no_skill_cases"] = no_skill_cases
                if calibration_error is not None:
                    metrics["no_skill_calibration_error"] = _finite_number(
                        calibration_error, "no_skill_calibration_error")
                checks["no_skill_calibration"] = (
                    metrics["no_skill_precision"] >= thresholds["min_no_skill_precision"] and
                    metrics["no_skill_recall"] >= thresholds["min_no_skill_recall"] and
                    (calibration_error is None or metrics["no_skill_calibration_error"] <=
                     thresholds["max_no_skill_calibration_error"]))
                if not checks["no_skill_calibration"]:
                    failed.add("no_skill_calibration")
                    reasons.append("no_skill_calibration_below_threshold")
        except ProductionGateError as exc:
            failed.add("no_skill_calibration")
            reasons.append(f"no_skill_calibration:{exc}")

    # 3. Candidate-pool evidence must be paired and must report diversity and
    # interference explicitly.  UNKNOWN or an absent denominator is a veto.
    paired_cases = _pick(source, "paired_cases")
    interference = _pick(source, "memory_interference_rate")
    interference_cases = _pick(source, "memory_interference_cases")
    diversity = _pick(source, "candidate_diversity")
    try:
        if paired_cases is None or interference is None or diversity is None:
            missing.add("candidate_pool")
            reasons.append("paired_candidate_pool_metrics_required")
        else:
            _positive_int(paired_cases, "paired_cases")
            metrics["paired_cases"] = paired_cases
            metrics["memory_interference_rate"] = _finite_number(
                interference, "memory_interference_rate")
            if interference_cases is not None:
                if (type(interference_cases) is not int or interference_cases < 0 or
                        interference_cases > paired_cases):
                    raise ProductionGateError(
                        "memory_interference_cases must be an integer in paired_cases")
                interference_ci = wilson_interval(interference_cases, paired_cases)
                metrics["memory_interference_cases"] = interference_cases
                metrics["memory_interference_ci"] = interference_ci
                interference_gate_value = interference_ci["upper"]
            else:
                # Legacy reports have only a point estimate.  Keep them
                # replayable, while making the stronger P15 CI path explicit.
                interference_gate_value = metrics["memory_interference_rate"]
            metrics["candidate_diversity"] = _finite_number(
                diversity, "candidate_diversity")
            checks["candidate_pool"] = ((
                interference_gate_value < thresholds["max_memory_interference_rate"]
                if interference_cases is not None else
                interference_gate_value <= thresholds["max_memory_interference_rate"]) and
                metrics["candidate_diversity"] >= thresholds["min_candidate_diversity"])
            if not checks["candidate_pool"]:
                failed.add("candidate_pool")
                reasons.append("candidate_pool_safety_or_diversity_below_threshold")
    except ProductionGateError as exc:
        failed.add("candidate_pool")
        reasons.append(f"candidate_pool:{exc}")

    # 4/5. Authority and rollback are independent, content-bound proofs.
    authority, authority_reasons = _authority_check(source)
    reasons.extend(f"authority:{item}" for item in authority_reasons)
    if authority is None:
        missing.add("authority")
        reasons.append("authority_receipt_required")
    elif authority:
        checks["authority"] = True
    else:
        failed.add("authority")
    rollback, rollback_reasons = _rollback_check(source)
    reasons.extend(f"rollback:{item}" for item in rollback_reasons)
    if rollback is None:
        missing.add("rollback")
        reasons.append("rollback_receipt_required")
    elif rollback:
        checks["rollback"] = True
    else:
        failed.add("rollback")

    # 6. Every production claim must point to at least one immutable evidence
    # object.  The digest is required even if the path is outside this repo.
    try:
        refs = _normalise_refs(source.get("evidence_refs"))
    except ProductionGateError as exc:
        refs = ()
        failed.add("evidence")
        reasons.append(f"evidence:{exc}")
    if not refs and "evidence" not in failed:
        missing.add("evidence")
        reasons.append("evidence_refs_required")
    elif refs:
        checks["evidence"] = True

    for name in PRODUCTION_GATE_GATES:
        if checks[name]:
            continue
        if name not in missing and name not in failed:
            # Defensive classification for future gate additions.
            missing.add(name)
    gate_status = {
        name: ("PASS" if checks[name] else
               "NOT_ESTABLISHED" if name in missing else "FAIL")
        for name in PRODUCTION_GATE_GATES
    }
    return ProductionGateReceipt(
        eligible=all(checks.values()), checks=checks, gate_status=gate_status,
        missing=tuple(sorted(missing)), failed=tuple(sorted(failed)),
        not_established=tuple(name for name in PRODUCTION_GATE_GATES
                              if gate_status[name] == "NOT_ESTABLISHED"),
        metrics=metrics, thresholds=thresholds, evidence_refs=refs,
        reasons=tuple(sorted(set(reasons))), evaluation_only=True,
        production_integration="not_attempted")


__all__ = [
    "PRODUCTION_GATE_VERSION", "PRODUCTION_GATE_GATES", "PRODUCTION_GATE_STATUS",
    "ProductionGateError", "ProductionGateReceipt", "evaluate_production_gate",
]
