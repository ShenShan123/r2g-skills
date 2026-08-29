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
