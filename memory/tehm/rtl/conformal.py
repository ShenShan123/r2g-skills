"""Evaluation-only split-conformal calibration for RTL oracle obligations.

The RTL rule-authority gate needs a calibration witness from the same typed
action population as the rule under review.  A binary Icarus PASS by itself is
not a conformal interval, so this module makes the prediction target explicit:
the three executable obligations (target test, frozen regression and compile)
must be satisfied by a typed action contract.  The base prediction is the
contract's ``PASS`` label; split-conformal nonconformity scores widen the
prediction set to include ``FAIL`` when calibration failures require it.

This is a read-only/evaluation artifact.  It never captures transitions,
updates canonical memory, or grants lifecycle authority.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tehm.ids import stable_dumps
from tehm.rtl.rtl_actions import RTL_ACTION_DOMAINS


RTL_CONFORMAL_VERSION = "rtl-split-conformal-obligation-v1"
RTL_CONFORMAL_METHOD = "split_conformal_rtl_obligation_set_v1"
RTL_CONFORMAL_PREDICTION_RULE = "typed_rtl_action_contract_v1"
RTL_CONFORMAL_OBLIGATIONS = (
    "RTL_TARGET_TEST_PASS", "RTL_FROZEN_REGRESSION_PASS", "RTL_COMPILE_PASS",
)
_LABELS = frozenset({"PASS", "FAIL"})
_PROFILE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{2,127}$")


class RTLConformalError(ValueError):
    """Malformed or unsafe RTL conformal calibration evidence."""


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise RTLConformalError(f"{name} must be a non-empty string")
    return value.strip()


def _digest(value: object, name: str) -> str:
    text = _text(value, name)
    if (not text.startswith("sha256:") or len(text) != len("sha256:") + 64 or
            any(char not in "0123456789abcdef" for char in text[len("sha256:"):])):
        raise RTLConformalError(f"{name} must be a sha256 digest")
    return text


def _unit(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise RTLConformalError(f"{name} must be finite and in [0, 1]")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RTLConformalError(f"{name} must be finite and in [0, 1]") from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise RTLConformalError(f"{name} must be finite and in [0, 1]")
    return number


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise RTLConformalError(f"{name} must be a positive integer")
    return value


def _observed_label(value: object, name: str) -> str:
    label = _text(value, name).upper()
    if label not in _LABELS:
        raise RTLConformalError(f"{name} must be PASS or FAIL")
    return label


def _conformal_quantile(scores: Sequence[int], target_coverage: float) -> int:
    """Return the finite-sample split-conformal quantile in {0, 1}."""
    if not scores:
        raise RTLConformalError("conformal scores must be non-empty")
    ordered = sorted(scores)
    # The ceil((n+1)*alpha)/n rank is the conservative finite-sample choice.
    rank = max(1, min(len(ordered), math.ceil(
        (len(ordered) + 1) * target_coverage)))
    return int(ordered[rank - 1])


def _action_identity(action: Mapping) -> dict[str, str]:
    domain = _text(action.get("domain"), "action.domain")
    if domain not in RTL_ACTION_DOMAINS:
        raise RTLConformalError("RTL conformal action domain is unsupported")
    family = _text(action.get("transformation_family"),
                   "action.transformation_family")
    payload = action.get("payload")
    if not isinstance(payload, Mapping):
        payload = {}
    profile = (action.get("compatibility_profile")
               if action.get("compatibility_profile") is not None
               else payload.get("compatibility_profile"))
    profile = _text(profile, "action.compatibility_profile")
    if not _PROFILE_RE.fullmatch(profile):
        raise RTLConformalError("action.compatibility_profile is malformed")
    return {
        "action_domain": domain,
        "transformation_family": family,
        "compatibility_profile": profile,
    }


def predict_rtl_obligations(action: Mapping) -> dict[str, str]:
    """Build the typed action-contract base prediction.

    The prediction is intentionally simple and auditable: a parser-backed RTL
    action with a complete typed identity predicts PASS for each declared
    executable obligation.  The independent Icarus result is still required
    to measure coverage; this function never reads an observed outcome.
    """
    _action_identity(action)
    return {obligation: "PASS" for obligation in RTL_CONFORMAL_OBLIGATIONS}


def observed_rtl_obligations(verification: Mapping) -> dict[str, str]:
    """Extract definitive obligation labels from one Icarus verification."""
    if not isinstance(verification, Mapping):
        raise RTLConformalError("verification must be an object")
    if verification.get("oracle_complete") is not True:
        raise RTLConformalError("verification.oracle_complete must be true")
    _observed_label(verification.get("verdict"), "verification.verdict")
    rows = {}
    for key, obligation, label in (
            ("target", "RTL_TARGET_TEST_PASS", "target"),
            ("regression", "RTL_FROZEN_REGRESSION_PASS", "regression")):
        run = verification.get(key)
        if not isinstance(run, Mapping):
            raise RTLConformalError(f"verification.{label} is missing")
        rows[obligation] = _observed_label(
            run.get("verdict"), f"verification.{label}.verdict")
    compile_labels = []
    for label in ("target", "regression"):
        run = verification[label]
        compile_value = run.get("compile_verdict")
        compile_label = _text(
            compile_value, f"verification.{label}.compile_verdict").upper()
        if compile_label not in _LABELS:
            raise RTLConformalError(
                f"verification.{label}.compile_verdict must be PASS or FAIL")
        compile_labels.append(compile_label)
    rows["RTL_COMPILE_PASS"] = (
        "PASS" if all(label == "PASS" for label in compile_labels) else "FAIL")
    aggregate = "PASS" if all(label == "PASS" for label in rows.values()) else "FAIL"
    if verification.get("verdict") != aggregate:
        raise RTLConformalError("verification.verdict disagrees with obligation labels")
    return rows


@dataclass(frozen=True)
class RTLConformalSample:
    """One source-disjoint typed RTL calibration sample."""

    case_id: str
    lineage_id: str
    action_domain: str
    transformation_family: str
    compatibility_profile: str
    predicted: dict[str, str]
    observed: dict[str, str]
    split: str = "calibration"

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _text(self.case_id, "case_id"))
        object.__setattr__(self, "lineage_id", _text(self.lineage_id, "lineage_id"))
        object.__setattr__(self, "action_domain", _text(
            self.action_domain, "action_domain"))
        object.__setattr__(self, "transformation_family", _text(
            self.transformation_family, "transformation_family"))
        object.__setattr__(self, "compatibility_profile", _text(
            self.compatibility_profile, "compatibility_profile"))
        if not self.action_domain.startswith("rtl."):
            raise RTLConformalError("action_domain must start with rtl.")
        if self.split != "calibration":
            raise RTLConformalError("RTL conformal samples require calibration split")
        for name, values in (("predicted", self.predicted),
                             ("observed", self.observed)):
            if not isinstance(values, Mapping):
                raise RTLConformalError(f"{name} must be an object")
            if set(values) != set(RTL_CONFORMAL_OBLIGATIONS):
                raise RTLConformalError(f"{name} obligations are incomplete")
            normalized = {
                key: _observed_label(value, f"{name}.{key}")
                for key, value in values.items()
            }
            object.__setattr__(self, name, normalized)

    @classmethod
    def from_record(cls, record: Mapping, *, case_id: str | None = None,
                    split: str = "calibration") -> "RTLConformalSample":
        if not isinstance(record, Mapping):
            raise RTLConformalError("RTL calibration record must be an object")
        action = record.get("action")
        if not isinstance(action, Mapping):
            raise RTLConformalError("RTL calibration record action is missing")
        identity = _action_identity(action)
        resolved_case = case_id if case_id is not None else record.get("record_id")
        lineage = record.get("lineage_id")
        return cls(
            case_id=_text(resolved_case, "case_id"),
            lineage_id=_text(lineage, "lineage_id"),
            action_domain=identity["action_domain"],
            transformation_family=identity["transformation_family"],
            compatibility_profile=identity["compatibility_profile"],
            predicted=predict_rtl_obligations(action),
            observed=observed_rtl_obligations(record.get("verification") or {}),
            split=split,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "lineage_id": self.lineage_id,
            "action_domain": self.action_domain,
            "transformation_family": self.transformation_family,
            "compatibility_profile": self.compatibility_profile,
            "predicted": dict(sorted(self.predicted.items())),
            "observed": dict(sorted(self.observed.items())),
            "split": self.split,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RTLConformalSample":
        if not isinstance(value, Mapping):
            raise RTLConformalError("RTL conformal sample must be an object")
        return cls(
            case_id=value.get("case_id"),
            lineage_id=value.get("lineage_id"),
            action_domain=value.get("action_domain"),
            transformation_family=value.get("transformation_family"),
            compatibility_profile=value.get("compatibility_profile"),
            predicted=value.get("predicted") or {},
            observed=value.get("observed") or {},
            split=value.get("split", "calibration"),
        )


@dataclass(frozen=True)
class RTLConformalCalibrationReceipt:
    """Content-addressed RTL conformal calibration result."""

    payload: dict[str, Any]
    receipt_digest: str

    @property
    def eligible(self) -> bool:
        return bool(self.payload.get("eligible") is True)

    @property
    def action_identity(self) -> dict[str, str]:
        return dict(self.payload["action"])

    def to_dict(self) -> dict[str, Any]:
        return {**self.payload, "receipt_digest": self.receipt_digest}

    def authority_payload(self) -> dict[str, Any]:
        """Return only typed metadata/coverage accepted by rule authority."""
        action = self.action_identity
        coverage = self.payload["coverage"]
        return {
            "coverage": coverage["coverage"],
            "covered": coverage["covered"],
            "total": coverage["total"],
            "method": self.payload["method"],
            "calibration_digest": self.payload["calibration_digest"],
            "calibration_receipt_digest": self.receipt_digest,
            "calibration_action_domain": action["action_domain"],
            "calibration_transformation_family": action["transformation_family"],
            "calibration_compatibility_profile": action["compatibility_profile"],
            "prediction_set_rule": self.payload["prediction_set_rule"],
            "source_lineages_digest": self.payload["source_lineages_digest"],
        }

    @classmethod
    def from_dict(cls, value: object) -> "RTLConformalCalibrationReceipt":
        if not isinstance(value, Mapping):
            raise RTLConformalError("RTL conformal receipt must be an object")
        payload = dict(value)
        digest = _digest(payload.pop("receipt_digest", None), "receipt_digest")
        actual = "sha256:" + hashlib.sha256(stable_dumps(payload).encode()).hexdigest()
        if digest != actual:
            raise RTLConformalError("RTL conformal receipt digest mismatch")
        if payload.get("version") != RTL_CONFORMAL_VERSION:
            raise RTLConformalError("RTL conformal receipt version mismatch")
        if payload.get("method") != RTL_CONFORMAL_METHOD:
            raise RTLConformalError("RTL conformal receipt method mismatch")
        if payload.get("evaluation_only") is not True or \
                payload.get("canonical_memory_mutation") != "none":
            raise RTLConformalError("RTL conformal receipt crosses production boundary")
        action = payload.get("action")
        if not isinstance(action, Mapping):
            raise RTLConformalError("RTL conformal receipt action is missing")
        identity = {
            "action_domain": action.get("action_domain"),
            "transformation_family": action.get("transformation_family"),
            "compatibility_profile": action.get("compatibility_profile"),
        }
        _action_identity({
            "domain": identity["action_domain"],
            "transformation_family": identity["transformation_family"],
            "payload": {"compatibility_profile": identity["compatibility_profile"]},
        })
        if payload.get("prediction_set_rule") != RTL_CONFORMAL_PREDICTION_RULE:
            raise RTLConformalError("RTL conformal receipt prediction rule mismatch")
        calibration_lineages = payload.get("calibration_lineages")
        training_lineages = payload.get("training_lineages")
        if (not isinstance(calibration_lineages, list) or
                any(type(item) is not str or not item.strip()
                    for item in calibration_lineages) or
                not isinstance(training_lineages, list) or
                any(type(item) is not str or not item.strip()
                    for item in training_lineages)):
            raise RTLConformalError("RTL conformal receipt lineages are malformed")
        if payload.get("source_disjoint") is not True:
            raise RTLConformalError("RTL conformal receipt is not source-disjoint")
        expected_lineage_digest = "sha256:" + hashlib.sha256(
            stable_dumps(sorted(set(calibration_lineages))).encode()).hexdigest()
        if payload.get("source_lineages_digest") != expected_lineage_digest:
            raise RTLConformalError("RTL conformal receipt lineage digest mismatch")
        coverage = payload.get("coverage")
        if not isinstance(coverage, Mapping):
            raise RTLConformalError("RTL conformal receipt coverage is malformed")
        covered, total = coverage.get("covered"), coverage.get("total")
        if (type(covered) is not int or type(total) is not int or
                total <= 0 or covered < 0 or covered > total or
                coverage.get("coverage") != round(covered / total, 6)):
            raise RTLConformalError("RTL conformal receipt coverage is malformed")
        target = _unit(payload.get("target_coverage"), "target_coverage")
        if coverage.get("required_coverage") != target:
            raise RTLConformalError("RTL conformal receipt target mismatch")
        samples = payload.get("samples")
        if (not isinstance(samples, list) or
                payload.get("sample_count") != len(set(
                    item.get("case_id") for item in samples
                    if isinstance(item, Mapping)))):
            raise RTLConformalError("RTL conformal receipt samples are malformed")
        if payload.get("sample_count") != len(calibration_lineages):
            raise RTLConformalError("RTL conformal receipt sample/lineage mismatch")
        expected_details = payload["sample_count"] * len(RTL_CONFORMAL_OBLIGATIONS)
        if len(samples) != expected_details:
            raise RTLConformalError("RTL conformal receipt obligation details are incomplete")
        return cls(payload=payload, receipt_digest=digest)


def calibrate_rtl_obligations(
        samples: Sequence[RTLConformalSample | Mapping], *,
        calibration_digest: str, training_lineages: Sequence[str] = (),
        target_coverage: float = 0.80, min_lineages: int = 3) \
        -> RTLConformalCalibrationReceipt:
    """Calibrate a same-domain RTL obligation predictor.

    ``samples`` must be an evaluation-only calibration split.  Training and
    calibration lineages are checked for disjointness, action identities are
    homogeneous, and UNKNOWN/partial oracles are rejected instead of being
    imputed.  The resulting receipt is evidence for review only.
    """
    target_coverage = _unit(target_coverage, "target_coverage")
    if target_coverage <= 0.0:
        raise RTLConformalError("target_coverage must be positive")
    min_lineages = _positive_int(min_lineages, "min_lineages")
    calibration_digest = _digest(calibration_digest, "calibration_digest")
    if isinstance(samples, (str, bytes)) or not isinstance(samples, Sequence):
        raise RTLConformalError("samples must be a non-empty sequence")
    rows = tuple(item if isinstance(item, RTLConformalSample)
                 else (RTLConformalSample.from_dict(item)
                       if isinstance(item, Mapping) and "predicted" in item
                       else RTLConformalSample.from_record(item))
                 for item in samples)
    if not rows:
        raise RTLConformalError("samples must be a non-empty sequence")
    if len({row.case_id for row in rows}) != len(rows):
        raise RTLConformalError("samples contain duplicate case IDs")
    training = {_text(value, "training_lineage") for value in training_lineages}
    lineages = {row.lineage_id for row in rows}
    overlap = sorted(training & lineages)
    if overlap:
        raise RTLConformalError(
            "calibration/training lineage overlap:" + ",".join(overlap))
    identities = {
        (row.action_domain, row.transformation_family,
         row.compatibility_profile) for row in rows
    }
    if len(identities) != 1:
        raise RTLConformalError("calibration samples mix typed action identities")
    action_domain, family, profile = next(iter(identities))
    expected_prediction = {
        obligation: "PASS" for obligation in RTL_CONFORMAL_OBLIGATIONS}
    if any(row.predicted != expected_prediction for row in rows):
        raise RTLConformalError("calibration samples do not use the typed base prediction")

    per_obligation = {}
    sample_details = []
    for obligation in RTL_CONFORMAL_OBLIGATIONS:
        scores = [int(row.observed[obligation] != row.predicted[obligation])
                  for row in rows]
        quantile = _conformal_quantile(scores, target_coverage)
        prediction_sets = {
            row.case_id: ({row.predicted[obligation]} |
                          ({"FAIL", "PASS"} if quantile >= 1 else set()))
            for row in rows
        }
        covered = sum(row.observed[obligation] in prediction_sets[row.case_id]
                      for row in rows)
        total = len(rows)
        per_obligation[obligation] = {
            "covered": covered, "total": total,
            "coverage": round(covered / total, 6),
            "nonconformity_quantile": quantile,
            "prediction_set": sorted(prediction_sets[rows[0].case_id]),
        }
        for row in rows:
            sample_details.append({
                "case_id": row.case_id,
                "lineage_id": row.lineage_id,
                "obligation": obligation,
                "observed": row.observed[obligation],
                "predicted": row.predicted[obligation],
                "nonconformity_score": int(
                    row.observed[obligation] != row.predicted[obligation]),
                "prediction_set": sorted(prediction_sets[row.case_id]),
                "covered": row.observed[obligation] in prediction_sets[row.case_id],
            })

    covered = sum(item["covered"] for item in sample_details)
    total = len(sample_details)
    lineages_digest = "sha256:" + hashlib.sha256(
        stable_dumps(sorted(lineages)).encode()).hexdigest()
    payload = {
        "version": RTL_CONFORMAL_VERSION,
        "method": RTL_CONFORMAL_METHOD,
        "prediction_set_rule": RTL_CONFORMAL_PREDICTION_RULE,
        "target_coverage": target_coverage,
        "action": {
            "action_domain": action_domain,
            "transformation_family": family,
            "compatibility_profile": profile,
        },
        "calibration_digest": calibration_digest,
        "source_lineages_digest": lineages_digest,
        "training_lineages": sorted(training),
        "calibration_lineages": sorted(lineages),
        "lineage_group_count": len(lineages),
        "source_disjoint": not overlap,
        "sample_count": len(rows),
        "coverage": {
            "covered": covered, "total": total,
            "coverage": round(covered / total, 6),
            "required_coverage": target_coverage,
        },
        "per_obligation": per_obligation,
        "samples": sample_details,
        "eligible": bool(
            len(lineages) >= min_lineages and not overlap and
            covered / total >= target_coverage),
        "min_lineages": min_lineages,
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "production_authority_changed": False,
        "promotion_attempted": False,
    }
    receipt_digest = "sha256:" + hashlib.sha256(
        stable_dumps(payload).encode()).hexdigest()
    return RTLConformalCalibrationReceipt(payload=payload,
                                          receipt_digest=receipt_digest)


__all__ = [
    "RTL_CONFORMAL_VERSION", "RTL_CONFORMAL_METHOD",
    "RTL_CONFORMAL_PREDICTION_RULE", "RTL_CONFORMAL_OBLIGATIONS",
    "RTLConformalError", "RTLConformalSample",
    "RTLConformalCalibrationReceipt", "predict_rtl_obligations",
    "observed_rtl_obligations", "calibrate_rtl_obligations",
]
