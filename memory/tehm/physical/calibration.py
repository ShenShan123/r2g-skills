"""Held-out calibration for similar-graph Physical Effect Memory.

Calibration examples are deliberately passed as external observations.  This
module never records them in TEHM, so evaluating a held-out lineage cannot
silently increase retrieval support or affect a later example in the same
frozen evaluation set.
"""
from __future__ import annotations

import math
from collections import defaultdict

from tehm.physical.effects import PHYSICAL_METRICS
from tehm.physical.memory import _action_signature


CALIBRATION_VERSION = "physical-retrieval-calibration-v0.3"


def calibrate_retrieval(memory, *, family: str, heldout_samples: list[dict],
                        training_lineages=(), k: int = 5,
                        min_unique_contexts: int = 3,
                        min_samples: int = 3,
                        target_coverage: float = 0.80,
                        per_metric_target_coverage: float | None = None,
                        distance_ceiling: float = 3.0,
                        distance_quantile: float = 0.95,
                        uncertainty_quantile: float = 0.95,
                        interval_method: str = "normal_weighted_mean_v1") -> dict:
    """Fit conservative retrieval gates on frozen, independent observations.

    Each sample needs ``lineage_id``, ``graph_context`` and
    ``observed_deltas``.  ``family`` may be supplied per sample as an extra
    consistency check.  The output is a serializable policy accepted by
    :meth:`PhysicalEffectMemory.predict`.
    """
    min_samples = max(1, int(min_samples))
    if interval_method not in {
            "normal_weighted_mean_v1", "split_conformal_residual_v1"}:
        raise ValueError("unsupported interval_method")
    target_coverage = _probability(target_coverage, "target_coverage")
    if per_metric_target_coverage is None:
        per_metric_target_coverage = target_coverage
    else:
        per_metric_target_coverage = _probability(
            per_metric_target_coverage, "per_metric_target_coverage")
    distance_ceiling = float(distance_ceiling)
    if not math.isfinite(distance_ceiling) or distance_ceiling <= 0:
        raise ValueError("distance_ceiling must be finite and positive")
    distance_quantile = _probability(distance_quantile, "distance_quantile")
    uncertainty_quantile = _probability(
        uncertainty_quantile, "uncertainty_quantile")
    training = {str(x) for x in training_lineages if str(x)}
    sample_lineages = {str(x.get("lineage_id") or "")
                       for x in heldout_samples}
    missing_lineage = "" in sample_lineages
    sample_lineages.discard("")
    overlap = sorted(training & sample_lineages)

    # A retrieval policy is valid only for the same typed action population it
    # was calibrated against.  In particular, CORE_UTILIZATION=22 and
    # CORE_UTILIZATION=40 are not interchangeable support: their empirical
    # effects may differ even when the transformation family is identical.
    # Keep the legacy unbound mode for cohorts that intentionally omit action
    # provenance, but reject partially bound or heterogeneous cohorts.
    action_present = [sample.get("action") is not None
                      for sample in heldout_samples]
    action_signature = None
    action_binding_reason = None
    if any(action_present):
        if not all(action_present):
            action_binding_reason = "action_signature_mismatch"
        else:
            signatures = [_action_signature(sample.get("action"))
                          for sample in heldout_samples]
            if any(signature is None for signature in signatures):
                action_binding_reason = "invalid_action_signature"
            elif any(signature != signatures[0] for signature in signatures[1:]):
                action_binding_reason = "mixed_action_signatures"
            else:
                action_signature = signatures[0]

    evaluations, distances, in_distribution_distances = [], [], []
    selective_rows = []
    widths: dict[str, list[float]] = defaultdict(list)
    metric_hits: dict[str, int] = defaultdict(int)
    metric_total: dict[str, int] = defaultdict(int)
    usable_predictions = 0
    metric_observations: dict[str, list[dict]] = defaultdict(list)
    for index, sample in enumerate(heldout_samples):
        lineage = str(sample.get("lineage_id") or "")
        sample_family = str(sample.get("family") or family)
        if not lineage or sample_family != family:
            evaluations.append({
                "index": index, "lineage_id": lineage,
                "status": "invalid_sample",
                "reason": "missing_lineage" if not lineage else "family_mismatch",
            })
            continue
        result = memory.predict(
            family=family, graph_context=sample.get("graph_context"), k=k,
            min_unique_contexts=min_unique_contexts,
            max_distance=1.0e12, action=sample.get("action"))
        evaluation = {
            "index": index, "lineage_id": lineage,
            "query_graph_context_digest": result.get("query_graph_context_digest"),
            "abstained": bool(result.get("abstained")),
            "abstain_reasons": list(result.get("abstain_reasons") or []),
            "nearest_distance": result.get("nearest_distance"),
            "metrics": {},
        }
        if result.get("abstained"):
            evaluations.append(evaluation)
            continue
        distance = result.get("nearest_distance")
        if isinstance(distance, (int, float)) and math.isfinite(float(distance)):
            distances.append(float(distance))
            if float(distance) <= distance_ceiling:
                in_distribution_distances.append(float(distance))
        observed = sample.get("observed_deltas") or {}
        compared = 0
        for metric in PHYSICAL_METRICS:
            actual = observed.get(metric)
            point = (result.get("mean_deltas") or {}).get(metric)
            interval = (result.get("uncertainty_95") or {}).get(metric) or {}
            lower, upper = interval.get("lower_95"), interval.get("upper_95")
            if not all(isinstance(x, (int, float)) and math.isfinite(float(x))
                       for x in (actual, point)):
                continue
            actual, point = float(actual), float(point)
            if interval_method == "split_conformal_residual_v1":
                metric_observations[metric].append({
                    "index": index, "observed": actual, "point": point,
                    "normal_lower": lower, "normal_upper": upper,
                })
                compared += 1
                continue
            if not all(isinstance(x, (int, float)) and math.isfinite(float(x))
                       for x in (lower, upper)):
                continue
            lower, upper = float(lower), float(upper)
            hit = _interval_contains(actual, lower, upper)
            width = max(0.0, upper - lower)
            metric_total[metric] += 1
            metric_hits[metric] += int(hit)
            widths[metric].append(width)
            compared += 1
            evaluation["metrics"][metric] = {
                "observed": actual, "lower_95": lower, "upper_95": upper,
                "covered": hit, "interval_width": width,
                "interval_method": interval_method,
            }
        if compared:
            usable_predictions += 1
            selective_rows.append({
                "distance": float(distance) if isinstance(distance, (int, float))
                and math.isfinite(float(distance)) else None,
                "comparisons": compared,
                "covered": sum(int(detail["covered"])
                               for detail in evaluation["metrics"].values()),
            })
        evaluation["status"] = "evaluated" if compared else "no_comparable_metrics"
        evaluations.append(evaluation)

    conformal_quantiles = {}
    if interval_method == "split_conformal_residual_v1":
        by_index = {item["index"]: item for item in evaluations}
        for metric, observations in metric_observations.items():
            residuals = [abs(item["observed"] - item["point"])
                         for item in observations]
            radius = _conformal_quantile(residuals, target_coverage)
            if radius is None:
                continue
            conformal_quantiles[metric] = radius
            for item in observations:
                lower = item["point"] - radius
                upper = item["point"] + radius
                hit = _interval_contains(item["observed"], lower, upper)
                metric_total[metric] += 1
                metric_hits[metric] += int(hit)
                widths[metric].append(max(0.0, upper - lower))
                evaluation = by_index[item["index"]]
                evaluation["metrics"][metric] = {
                    "observed": item["observed"],
                    "lower_95": lower,
                    "upper_95": upper,
                    "covered": hit,
                    "interval_width": max(0.0, upper - lower),
                    "conformal_radius": radius,
                    "interval_method": interval_method,
                }
        # Recompute the selective curve after replacing normal intervals.
        selective_rows = []
        for evaluation in evaluations:
            metrics = evaluation.get("metrics") or {}
            if not metrics:
                continue
            selective_rows.append({
                "distance": (float(evaluation["nearest_distance"])
                              if isinstance(evaluation.get("nearest_distance"),
                                            (int, float)) else None),
                "comparisons": len(metrics),
                "covered": sum(int(detail["covered"])
                               for detail in metrics.values()),
            })

    comparisons = sum(metric_total.values())
    hits = sum(metric_hits.values())
    empirical_coverage = hits / comparisons if comparisons else None
    per_metric = {
        metric: {
            "comparisons": metric_total[metric],
            "covered": metric_hits[metric],
            "coverage": (metric_hits[metric] / metric_total[metric]
                         if metric_total[metric] else None),
            "max_interval_width": _quantile(widths[metric], uncertainty_quantile),
        }
        for metric in PHYSICAL_METRICS if metric_total[metric]
    }
    selective_risk_coverage = _selective_risk_coverage(selective_rows)

    metric_coverage_failures = sorted(
        metric for metric, detail in per_metric.items()
        if detail["comparisons"] >= min_samples and
        detail["coverage"] < per_metric_target_coverage)

    if action_binding_reason:
        status, reason = "firewall_failed", action_binding_reason
    elif missing_lineage:
        status, reason = "firewall_failed", "missing_heldout_lineage"
    elif overlap:
        status, reason = "firewall_failed", "training_heldout_lineage_overlap"
    elif (usable_predictions < min_samples or
          len(in_distribution_distances) < min_samples):
        status, reason = (
            "insufficient_support", "insufficient_in_distribution_heldout_predictions")
    elif comparisons < min_samples:
        status, reason = "insufficient_support", "insufficient_metric_comparisons"
    elif empirical_coverage is None or empirical_coverage < target_coverage:
        status, reason = "coverage_failed", "empirical_coverage_below_target"
    elif metric_coverage_failures:
        status, reason = "coverage_failed", "per_metric_coverage_below_target"
    else:
        status, reason = "ready", "calibrated"

    required_metrics = sorted(
        metric for metric, detail in per_metric.items()
        if detail["comparisons"] >= min_samples)
    if status == "ready" and not required_metrics:
        status, reason = "insufficient_support", "no_metric_reaches_minimum_support"

    return {
        "version": CALIBRATION_VERSION,
        "family": family,
        "status": status,
        "reason": reason,
        "firewall": {
            "training_lineages": sorted(training),
            "heldout_lineages": sorted(sample_lineages),
            "disjoint": not missing_lineage and not overlap,
            "overlap": overlap,
            "action_signature_bound": action_signature is not None,
        },
        "action_signature": action_signature,
        "interval_method": interval_method,
        "thresholds": {
            "min_unique_contexts": max(2, int(min_unique_contexts)),
            "distance_ceiling": distance_ceiling,
            "max_distance": _quantile(in_distribution_distances, distance_quantile),
            "required_coverage": target_coverage,
            "required_metric_coverage": per_metric_target_coverage,
            "max_uncertainty_widths": {
                metric: per_metric[metric]["max_interval_width"]
                for metric in required_metrics},
            "conformal_quantiles": {
                metric: round(float(value), 6)
                for metric, value in sorted(conformal_quantiles.items())},
        },
        "calibration": {
            "sample_count": len(heldout_samples),
            "usable_predictions": usable_predictions,
            "in_distribution_predictions": len(in_distribution_distances),
            "observed_distance_range": ([min(distances), max(distances)]
                                        if distances else None),
            "metric_comparisons": comparisons,
            "covered_comparisons": hits,
            "empirical_coverage": empirical_coverage,
            "distance_quantile": distance_quantile,
            "uncertainty_quantile": uncertainty_quantile,
            "required_metrics": required_metrics,
            "per_metric_coverage_failures": metric_coverage_failures,
            "per_metric": per_metric,
            "selective_risk_coverage": selective_risk_coverage,
            "interval_method": interval_method,
            "conformal_quantiles": {
                metric: round(float(value), 6)
                for metric, value in sorted(conformal_quantiles.items())},
        },
        "evaluations": evaluations,
        "mutation": "none; held-out samples were not recorded in TEHM",
    }


def _probability(value: float, name: str) -> float:
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be in [0, 1]")
    return value


def _interval_contains(value: float, lower: float, upper: float) -> bool:
    """Closed-interval membership with a scale-aware numeric tolerance.

    PPA values are often serialized at decimal precision and reconstructed as
    binary floats.  Treating an observation equal to a serialized endpoint as
    outside solely because of a one-ulp rounding error would create a false
    coverage failure.  The interval remains closed; the tolerance only covers
    representation noise and is far below the reported metric precision.
    """
    scale = max(1.0, abs(float(value)), abs(float(lower)), abs(float(upper)))
    tol = 1.0e-12 * scale
    return float(lower) - tol <= float(value) <= float(upper) + tol


def _quantile(values: list[float], q: float):
    if not values:
        return None
    ordered = sorted(float(x) for x in values)
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _conformal_quantile(values: list[float], coverage: float):
    """Finite-sample conservative split-conformal residual radius.

    The order-statistic rank uses ``ceil((n+1)*coverage)`` and is clamped to
    the available residuals.  This intentionally chooses the largest residual
    for tiny cohorts instead of interpolating an optimistic radius.
    """
    if not values:
        return None
    ordered = sorted(float(value) for value in values
                     if math.isfinite(float(value)))
    if not ordered:
        return None
    rank = max(1, math.ceil((len(ordered) + 1) * float(coverage)))
    return ordered[min(rank, len(ordered)) - 1]


def _selective_risk_coverage(rows: list[dict]) -> list[dict]:
    """Return deterministic distance-threshold risk/coverage diagnostics.

    A row is retained when its nearest context distance is no greater than the
    threshold.  Risk is the interval miss rate over all comparable metrics;
    missing metrics are excluded rather than treated as misses.  This report
    is descriptive only and never widens the hard OOD ceiling or changes the
    calibration decision gate.
    """
    usable = [row for row in rows
              if isinstance(row.get("distance"), (int, float)) and
              math.isfinite(float(row["distance"])) and
              int(row.get("comparisons") or 0) > 0]
    if not usable:
        return []
    total = len(usable)
    result = []
    for threshold in sorted({float(row["distance"]) for row in usable}):
        selected = [row for row in usable if float(row["distance"]) <= threshold]
        comparisons = sum(int(row["comparisons"]) for row in selected)
        covered = sum(int(row["covered"]) for row in selected)
        interval_coverage = covered / comparisons if comparisons else None
        result.append({
            "max_distance": threshold,
            "sample_coverage": len(selected) / total,
            "metric_comparisons": comparisons,
            "interval_coverage": interval_coverage,
            "risk": (1.0 - interval_coverage
                     if interval_coverage is not None else None),
        })
    return result
