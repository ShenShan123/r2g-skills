"""Lineage-grouped, shadow-only Parametric calibration gates.

The existing physical retrieval calibration is intentionally reusable, but a
Parametric proposal needs two additional checks before it can even be treated
as a useful shadow observation: calibration must be scored by independent
lineage (not by repeated rows from one design), and the safety semantics must
be explicit.  This module computes split-conformal residual intervals and a
small, auditable Pareto/harmful report from *external* observations.  It never
writes TEHM or changes lifecycle state.
"""
from __future__ import annotations

import math
import hashlib
from collections import defaultdict
from typing import Mapping

from tehm.ids import stable_dumps
from tehm.physical.effects import PHYSICAL_METRICS


VERSION = "parametric-lineage-calibration-v1"
GROUPED_VERSION = "parametric-lineage-grouped-calibration-v1"
SHADOW_POLICY_VERSION = "parametric-lineage-grouped-shadow-policy-v1"
FAVORABLE_SIGN = {
    "wns_ns": 1.0, "tns_ns": 1.0,
    "area_um2": -1.0, "power_w": -1.0,
    "congestion": -1.0, "drc_violations": -1.0,
}
DEFAULT_MAX_REGRESSION = {
    "wns_ns": -0.05, "tns_ns": -0.05,
    "area_um2": 0.05, "power_w": 0.05,
    "congestion": 0.05, "drc_violations": 0.0,
}


def exact_calibration_group_key(sample: Mapping) -> str:
    """Partition key required for PPA calibration.

    The action signature is content-addressed so a numeric knob value cannot
    borrow support from another operation point.  The four dimensions are
    intentionally visible in the serialized key for audit reports.
    """
    platform = sample.get("platform")
    family = sample.get("family")
    tier = sample.get("dataset_tier")
    signature = sample.get("action_signature")
    if signature is None:
        signature = sample.get("action")
    if any(not isinstance(value, str) or not value
           for value in (platform, family, tier)):
        raise ValueError("sample requires platform/family/dataset_tier")
    if signature is None:
        raise ValueError("sample requires action_signature")
    digest = hashlib.sha256(stable_dumps(signature).encode()).hexdigest()[:24]
    return f"{platform}|{family}|{tier}|{digest}"


def calibrate_exact_groups(samples: list[Mapping], *, training_lineages=(),
                           target_coverage: float = .8,
                           min_lineages: int = 3,
                           min_samples_per_metric: int = 3,
                           max_harmful_rate: float = 0.0,
                           max_regression: Mapping | None = None) -> dict:
    """Run lineage-grouped conformal calibration per exact PPA partition.

    Every partition is calibrated independently.  Mixed platform/family/tier/
    action populations therefore cannot average into an apparently safe
    interval.  This is an external report and remains permanently shadow-only.
    """
    grouped: dict[str, list[Mapping]] = defaultdict(list)
    invalid = []
    for index, sample in enumerate(samples or []):
        try:
            key = exact_calibration_group_key(sample)
        except ValueError as exc:
            invalid.append({"index": index, "reason": str(exc)})
            continue
        grouped[key].append(sample)
    reports = {}
    for key in sorted(grouped):
        reports[key] = calibrate_lineage_grouped(
            list(grouped[key]), training_lineages=training_lineages,
            target_coverage=target_coverage, min_lineages=min_lineages,
            min_samples_per_metric=min_samples_per_metric,
            max_harmful_rate=max_harmful_rate,
            max_regression=max_regression)
    ready = bool(reports) and not invalid and all(
        report.get("status") == "ready_for_shadow"
        for report in reports.values())
    return {
        "version": GROUPED_VERSION,
        "status": "ready_for_shadow" if ready else "shadow_calibration_failed",
        "shadow_only": True, "promotion_eligible": False,
        "canonical_memory_mutation": "none",
        "group_key_definition": "platform|family|dataset_tier|action_signature_sha256_24",
        "group_count": len(reports), "sample_count": len(samples or []),
        "invalid_samples": invalid,
        "groups": reports,
        "training_lineages": sorted({str(x) for x in training_lineages if str(x)}),
    }


def materialize_shadow_policy(
        report: Mapping, *, scope: Mapping, action_signature: Mapping,
        max_distance: float, min_unique_contexts: int = 3) -> dict:
    """Convert one passing grouped report into a shadow-read policy.

    ``calibrate_exact_groups`` deliberately returns an external report with
    ``status=ready_for_shadow``.  The predictor's legacy policy adapter uses
    ``status=ready`` as its read-only admission token, so this function is the
    sole explicit bridge between the two representations.  It is intentionally
    strict: exactly one exact group must be selected, its scope and action
    signature must match, every new safety check must pass, and the hard OOD
    ceiling can never exceed ``3.0``.  The resulting ``ready`` value means
    *shadow-predictor compatible* only; the policy remains permanently
    shadow-only and cannot authorize a canonical or production write.
    """
    _require_mapping("calibration report", report)
    _require_mapping("policy scope", scope)
    _require_mapping("action signature", action_signature)
    if report.get("version") != GROUPED_VERSION:
        raise ValueError("calibration report version is unsupported")
    if report.get("status") != "ready_for_shadow":
        raise ValueError("calibration report is not ready_for_shadow")
    if report.get("shadow_only") is not True:
        raise ValueError("calibration report must be shadow_only")
    if report.get("promotion_eligible") is not False:
        raise ValueError("calibration report promotion flag is not false")
    if report.get("canonical_memory_mutation") != "none":
        raise ValueError("calibration report records a canonical mutation")
    groups = report.get("groups")
    if not isinstance(groups, Mapping) or len(groups) != 1:
        raise ValueError("exactly one calibration group is required")
    group_key, group = next(iter(groups.items()))
    if not isinstance(group_key, str) or not isinstance(group, Mapping):
        raise ValueError("calibration group is malformed")
    if group.get("version") != VERSION:
        raise ValueError("calibration group version is unsupported")
    if group.get("status") != "ready_for_shadow":
        raise ValueError("calibration group is not ready_for_shadow")
    if report.get("invalid_samples") or group.get("invalid_samples"):
        raise ValueError("calibration report contains invalid samples")
    scope = {str(key): value for key, value in scope.items()}
    for key in ("platform", "family", "dataset_tier"):
        if not isinstance(scope.get(key), str) or not scope[key]:
            raise ValueError(f"policy scope.{key} is required")
    expected_key = exact_calibration_group_key({
        **scope, "action_signature": dict(action_signature)})
    if expected_key != group_key:
        raise ValueError("calibration scope/action signature does not match group")
    checks = group.get("checks")
    required_checks = {
        "lineage_firewall", "minimum_lineage_groups", "per_metric_support",
        "conformal_lineage_coverage", "harmful_rate", "positive_utility",
        "pareto_definition_validated",
    }
    if (not isinstance(checks, Mapping) or
            not required_checks <= set(checks) or
            any(checks[key] is not True for key in required_checks)):
        raise ValueError("calibration group has a failed safety or support gate")
    safety = group.get("safety")
    harmful_rate = (_finite(safety.get("harmful_rate"))
                    if isinstance(safety, Mapping) else None)
    if harmful_rate is None or harmful_rate > 0.0:
        raise ValueError("calibration group has harmful utility")
    positive_rate = (_finite(safety.get("positive_utility_rate"))
                     if isinstance(safety, Mapping) else None)
    if positive_rate is None or positive_rate <= 0:
        raise ValueError("calibration group has no positive utility")
    try:
        max_distance = float(max_distance)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_distance must be numeric") from exc
    if not math.isfinite(max_distance) or not 0.0 < max_distance <= 3.0:
        raise ValueError("max_distance must be finite, positive, and <= 3.0")
    if isinstance(min_unique_contexts, bool) or not isinstance(min_unique_contexts, int):
        raise ValueError("min_unique_contexts must be an integer")
    min_unique_contexts = max(2, min_unique_contexts)

    thresholds = group.get("thresholds")
    conformal = group.get("conformal")
    per_metric = (conformal or {}).get("per_metric") if isinstance(conformal, Mapping) else None
    radii = (conformal or {}).get("radii") if isinstance(conformal, Mapping) else None
    if (not isinstance(thresholds, Mapping) or
            not isinstance(conformal, Mapping) or
            conformal.get("method") != "split_conformal_residual_lineage_grouped_v1" or
            not isinstance(per_metric, Mapping)):
        raise ValueError("calibration group lacks conformal thresholds")
    if not isinstance(radii, Mapping) or not radii:
        raise ValueError("calibration group lacks conformal radii")
    target_coverage = _finite(thresholds.get("target_coverage"))
    if target_coverage is None or not 0.0 <= target_coverage <= 1.0:
        raise ValueError("calibration target coverage is malformed")

    required_metrics = []
    conformal_quantiles = {}
    max_widths = {}
    evaluated = covered = 0
    for metric, raw_radius in sorted(radii.items()):
        metric = str(metric)
        if metric not in PHYSICAL_METRICS:
            raise ValueError(f"calibration metric is unsupported: {metric}")
        radius = _finite(raw_radius)
        detail = per_metric.get(metric)
        if radius is None or not isinstance(detail, Mapping):
            raise ValueError(f"calibration radius is malformed: {metric}")
        count = detail.get("evaluated")
        hits = detail.get("covered")
        if (isinstance(count, bool) or not isinstance(count, int) or count < 1 or
                isinstance(hits, bool) or not isinstance(hits, int) or
                hits < 0 or hits > count):
            raise ValueError(f"calibration metric counts are malformed: {metric}")
        required_metrics.append(metric)
        conformal_quantiles[metric] = round(radius, 6)
        # The predictor replaces its normal-approximation interval with the
        # frozen conformal interval, so this is an exact derived width bound.
        max_widths[metric] = round(2.0 * radius, 6)
        evaluated += count
        covered += hits
    empirical_coverage = covered / evaluated if evaluated else None
    if empirical_coverage is None or empirical_coverage < target_coverage:
        raise ValueError("calibration empirical coverage is below target")

    firewall = group.get("firewall")
    if not isinstance(firewall, Mapping) or firewall.get("disjoint") is not True:
        raise ValueError("calibration lineage firewall is not disjoint")
    return {
        "version": SHADOW_POLICY_VERSION,
        "family": scope["family"],
        "status": "ready",
        "policy_kind": "lineage_grouped_shadow",
        "source_calibration_status": "ready_for_shadow",
        "source_group_key": group_key,
        "scope": scope,
        "action_signature": dict(action_signature),
        "interval_method": "split_conformal_residual_v1",
        "thresholds": {
            "min_unique_contexts": min_unique_contexts,
            "max_distance": max_distance,
            "required_coverage": target_coverage,
            "max_uncertainty_widths": max_widths,
            "conformal_quantiles": conformal_quantiles,
        },
        "calibration": {
            "sample_count": group.get("sample_count"),
            "usable_predictions": group.get("sample_count"),
            "in_distribution_predictions": group.get("sample_count"),
            "metric_comparisons": evaluated,
            "covered_comparisons": covered,
            "empirical_coverage": empirical_coverage,
            "required_coverage": target_coverage,
            "required_metrics": required_metrics,
            "conformal_quantiles": conformal_quantiles,
            "positive_utility_rate": positive_rate,
        },
        "firewall": dict(firewall),
        "safety": {
            "harmful_rate": safety.get("harmful_rate"),
            "positive_utility_rate": safety.get("positive_utility_rate"),
            "positive_utility_lineages": list(
                safety.get("positive_utility_lineages") or []),
            "checks": dict(checks),
        },
        "shadow_only": True,
        "promotion_eligible": False,
        "canonical_memory_mutation": "none",
    }


def calibrate_lineage_grouped(
        samples: list[Mapping], *, training_lineages=(), target_coverage: float = .8,
        min_lineages: int = 3, min_samples_per_metric: int = 3,
        max_harmful_rate: float = 0.0, max_regression: Mapping | None = None,
        epsilon: float = 1e-12) -> dict:
    """Return a conformal calibration report, remaining shadow-only.

    A sample must contain ``lineage_id``, ``predicted`` and ``observed_deltas``
    mappings.  All coverage and harmful rates are first computed per lineage
    and then aggregated, so one lineage with many repeats cannot dominate the
    gate.  ``predicted`` is an externally supplied point prediction; this
    function does not train or invent one.
    """
    if not isinstance(samples, list) or not samples:
        return _failed("insufficient_support", "no_samples")
    target_coverage = _probability(target_coverage, "target_coverage")
    max_harmful_rate = _probability(max_harmful_rate, "max_harmful_rate")
    min_lineages = max(1, int(min_lineages))
    min_samples_per_metric = max(1, int(min_samples_per_metric))
    training = {str(x) for x in training_lineages if str(x)}
    groups: dict[str, list[dict]] = defaultdict(list)
    invalid = []
    for index, sample in enumerate(samples):
        lineage = str(sample.get("lineage_id") or "")
        predicted = sample.get("predicted")
        observed = sample.get("observed_deltas")
        if not lineage or not isinstance(predicted, Mapping) or not isinstance(observed, Mapping):
            invalid.append({"index": index, "reason": "missing_lineage_or_metrics"})
            continue
        groups[lineage].append(dict(sample))
    heldout = set(groups)
    overlap = sorted(training & heldout)
    if invalid:
        return _failed("firewall_failed", "invalid_sample", firewall=_firewall(
            training, heldout, overlap), invalid=invalid)
    if overlap:
        return _failed("firewall_failed", "training_heldout_lineage_overlap",
                       firewall=_firewall(training, heldout, overlap))
    if len(groups) < min_lineages:
        return _failed("insufficient_support", "insufficient_lineage_groups",
                       firewall=_firewall(training, heldout, overlap),
                       lineage_count=len(groups), min_lineages=min_lineages)

    regression = dict(DEFAULT_MAX_REGRESSION)
    if max_regression is not None:
        for key, value in max_regression.items():
            parsed = _finite(value)
            if parsed is None:
                raise ValueError("max_regression must contain finite numbers")
            regression[str(key)] = parsed
    residuals = defaultdict(list)
    for rows in groups.values():
        for sample in rows:
            for metric in PHYSICAL_METRICS:
                point = _finite(sample["predicted"].get(metric))
                actual = _finite(sample["observed_deltas"].get(metric))
                if point is not None and actual is not None:
                    residuals[metric].append(abs(actual - point))
    radii = {metric: _conformal_radius(values, target_coverage)
             for metric, values in residuals.items() if values}
    per_metric = {}
    per_lineage = {}
    for metric, values in residuals.items():
        radius = radii.get(metric)
        if radius is None:
            continue
        hits_by_lineage = {}
        for lineage, rows in groups.items():
            hits = total = 0
            for sample in rows:
                point = _finite(sample["predicted"].get(metric))
                actual = _finite(sample["observed_deltas"].get(metric))
                if point is None or actual is None:
                    continue
                total += 1
                hits += int(point - radius <= actual <= point + radius)
            if total:
                hits_by_lineage[lineage] = {"covered": hits, "evaluated": total,
                                            "coverage": hits / total}
        groups_with_metric = len(hits_by_lineage)
        covered_groups = sum(item["coverage"] >= target_coverage - epsilon
                             for item in hits_by_lineage.values())
        comparisons = sum(item["evaluated"] for item in hits_by_lineage.values())
        covered = sum(item["covered"] for item in hits_by_lineage.values())
        per_metric[metric] = {
            "conformal_radius": round(radius, 6),
            "evaluated": comparisons, "covered": covered,
            "coverage": covered / comparisons if comparisons else None,
            "lineage_groups": groups_with_metric,
            "lineage_group_coverage": covered_groups / groups_with_metric
            if groups_with_metric else None,
        }
        for lineage, detail in hits_by_lineage.items():
            per_lineage.setdefault(lineage, {})[metric] = detail

    safety_rows = []
    for lineage, rows in groups.items():
        harmful = 0
        pareto_safe = 0
        for sample in rows:
            deltas = {metric: _finite((sample.get("observed_deltas") or {}).get(metric))
                      for metric in PHYSICAL_METRICS}
            violating = [metric for metric, delta in deltas.items()
                         if delta is not None and delta > regression.get(metric, math.inf)
                         and FAVORABLE_SIGN.get(metric, 0.0) < 0]
            # For higher-is-better metrics, a negative delta beyond the allowed
            # regression is harmful; for lower-is-better metrics, a positive
            # delta is harmful.
            violating += [metric for metric, delta in deltas.items()
                          if delta is not None and
                          FAVORABLE_SIGN.get(metric, 0.0) > 0 and
                          delta < regression.get(metric, -math.inf)]
            improved = any(delta is not None and
                           FAVORABLE_SIGN.get(metric, 0.0) * delta > epsilon
                           for metric, delta in deltas.items())
            is_harmful = bool(violating)
            harmful += int(is_harmful)
            pareto_safe += int(not is_harmful and improved)
            safety_rows.append({"lineage_id": lineage,
                                "harmful": is_harmful,
                                "pareto_safe": bool(not is_harmful and improved),
                                "violating_metrics": sorted(set(violating)),
                                "improved": improved})

    lineage_rates = []
    for lineage in sorted(groups):
        rows = [row for row in safety_rows if row["lineage_id"] == lineage]
        lineage_rates.append({"lineage_id": lineage,
                              "samples": len(rows),
                              "harmful_rate": sum(r["harmful"] for r in rows) / len(rows),
                              "pareto_safe_rate": sum(r["pareto_safe"] for r in rows) / len(rows)})
    harmful_rate = (sum(row["harmful_rate"] for row in lineage_rates) /
                    len(lineage_rates) if lineage_rates else None)
    positive_rows = [row for row in safety_rows if row["pareto_safe"]]
    positive_lineages = sorted({row["lineage_id"] for row in positive_rows})
    positive_utility_rate = (len(positive_rows) / len(safety_rows)
                             if safety_rows else None)
    checks = {
        "lineage_firewall": not overlap,
        "minimum_lineage_groups": len(groups) >= min_lineages,
        "per_metric_support": all(item["evaluated"] >= min_samples_per_metric
                                   for item in per_metric.values()),
        "conformal_lineage_coverage": bool(per_metric) and all(
            item["lineage_group_coverage"] >= target_coverage - epsilon
            for item in per_metric.values()),
        "harmful_rate": harmful_rate is not None and harmful_rate <= max_harmful_rate,
        # A completed but neutral intervention is not useful calibration
        # support.  Require at least one independently observed Pareto-safe
        # row before a policy can be consumed by the shadow lane.  This is a
        # safety/utility gate only; it does not make the policy promotable.
        "positive_utility": bool(positive_rows),
        "pareto_definition_validated": bool(safety_rows),
    }
    status = "ready_for_shadow" if all(checks.values()) else "shadow_calibration_failed"
    return {
        "version": VERSION, "status": status,
        "shadow_only": True, "promotion_eligible": False,
        "canonical_memory_mutation": "none",
        "firewall": _firewall(training, heldout, overlap),
        "thresholds": {"target_coverage": target_coverage,
                       "min_lineages": min_lineages,
                       "min_samples_per_metric": min_samples_per_metric,
                       "max_harmful_rate": max_harmful_rate,
                       "max_regression": regression},
        "conformal": {"method": "split_conformal_residual_lineage_grouped_v1",
                       "radii": {k: round(v, 6) for k, v in radii.items()},
                       "per_metric": per_metric},
        "safety": {"definition": {
            "harmful": "constraint violation under max_regression",
            "pareto_safe": "no harmful metric and at least one favorable delta",
            "favorable_sign": FAVORABLE_SIGN},
            "harmful_rate": harmful_rate,
            "positive_utility_rate": positive_utility_rate,
            "positive_utility_lineages": positive_lineages,
            "lineage_rates": lineage_rates,
            "rows": safety_rows},
        "checks": checks, "lineage_group_count": len(groups),
        "sample_count": len(samples), "invalid_samples": invalid,
        "per_lineage_coverage": per_lineage,
    }


def _firewall(training, heldout, overlap):
    return {"training_lineages": sorted(training),
            "heldout_lineages": sorted(heldout), "overlap": sorted(overlap),
            "disjoint": not overlap}


def _require_mapping(name: str, value) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")


def _failed(status, reason, **extra):
    return {"version": VERSION, "status": status, "reason": reason,
            "shadow_only": True, "promotion_eligible": False,
            "canonical_memory_mutation": "none", **extra}


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _conformal_radius(values, coverage):
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    rank = max(1, math.ceil((len(ordered) + 1) * coverage))
    return ordered[min(rank, len(ordered)) - 1]


def _probability(value, name):
    value = _finite(value)
    if value is None or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be in [0,1]")
    return value
