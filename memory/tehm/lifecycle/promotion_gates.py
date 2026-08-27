"""Explicit evidence gates for candidate -> production promotion.

Promotion is an authority decision, not a model score.  Every gate in this
module is evidence-backed and fail-closed when the strict mode is requested.
The legacy ``apply_trial_verdict`` API can still be used by old deterministic
unit fixtures; production executors pass ``strict=True`` and an explicit map.
"""
from __future__ import annotations

from collections.abc import Mapping


PROMOTION_GATE_VERSION = "promotion-gates-v1"
REQUIRED_GATES = (
    "rollback_verified",
    "registry_verified",
    "obligation_coverage",
    "cross_lineage_te",
    "harmful_rate",
    "conformal_coverage",
)

CAPABILITY_GATES = tuple(f"C{index}" for index in range(1, 9))


def evaluate_promotion_gates(
        gates: Mapping | None, *, strict: bool = True,
        min_obligation_coverage: float = 1.0,
        min_cross_lineage_te: float = 1.0,
        max_harmful_rate: float = 0.0,
        min_conformal_coverage: float = 0.80) -> dict:
    """Evaluate the complete promotion gate conjunction.

    Boolean gates must literally be ``True``.  Numeric gates are checked
    against the explicit thresholds.  Missing/non-finite values never pass in
    strict mode; this prevents a report that merely omits TE or conformal
    coverage from becoming production authority.
    """
    source = dict(gates or {})
    checks = {}
    checks["rollback_verified"] = source.get("rollback_verified") is True
    checks["registry_verified"] = source.get("registry_verified") is True
    checks["obligation_coverage"] = _at_least(
        source.get("obligation_coverage"), min_obligation_coverage)
    checks["cross_lineage_te"] = _at_least(
        source.get("cross_lineage_te"), min_cross_lineage_te)
    checks["harmful_rate"] = _at_most(
        source.get("harmful_rate"), max_harmful_rate)
    checks["conformal_coverage"] = _at_least(
        source.get("conformal_coverage"), min_conformal_coverage)
    missing = sorted(name for name in REQUIRED_GATES if name not in source)
    gate_status = _gate_status(source, checks, REQUIRED_GATES)
    eligible = all(checks.values()) if strict else (not missing and all(checks.values()))
    return {
        "version": PROMOTION_GATE_VERSION,
        "strict": bool(strict),
        "eligible": bool(eligible),
        "checks": checks,
        "missing": missing,
        "gate_status": gate_status,
        "not_established": [name for name in REQUIRED_GATES
                             if gate_status[name] == "NOT_ESTABLISHED"],
        "failed": [name for name in REQUIRED_GATES
                    if gate_status[name] == "FAIL"],
        "all_gates_established": not missing,
        "thresholds": {
            "obligation_coverage": float(min_obligation_coverage),
            "cross_lineage_te": float(min_cross_lineage_te),
            "harmful_rate": float(max_harmful_rate),
            "conformal_coverage": float(min_conformal_coverage),
        },
    }


def _at_least(value, threshold: float) -> bool:
    try:
        return float(value) >= float(threshold)
    except (TypeError, ValueError):
        return False


def _at_most(value, threshold: float) -> bool:
    try:
        return float(value) <= float(threshold)
    except (TypeError, ValueError):
        return False


def evaluate_capability_promotion_gates(
        gates: Mapping | None, *, required_assets=(), strict: bool = True) -> dict:
    """Evaluate the independent C1-C8 capability authority conjunction.

    Capability promotion is deliberately separate from rule promotion.  The
    caller must provide literal booleans from an attribution receipt; scores,
    rule counts, and a single PASS are not accepted.  If the capability names
    assets, an additional independent asset-authority receipt is mandatory.
    """
    source = dict(gates or {})
    checks = {name: source.get(name) is True for name in CAPABILITY_GATES}
    required = list(CAPABILITY_GATES)
    if tuple(required_assets):
        checks["asset_authority_verified"] = (
            source.get("asset_authority_verified") is True)
        required.append("asset_authority_verified")
    missing = sorted(name for name in required if name not in source)
    gate_status = _gate_status(source, checks, required)
    eligible = all(checks[name] for name in required) if strict else (
        not missing and all(checks[name] for name in required))
    return {
        "version": "capability-promotion-gates-v1",
        "strict": bool(strict),
        "eligible": bool(eligible),
        "checks": checks,
        "missing": missing,
        "gate_status": gate_status,
        "not_established": [name for name in required
                             if gate_status[name] == "NOT_ESTABLISHED"],
        "failed": [name for name in required
                    if gate_status[name] == "FAIL"],
        "all_gates_established": not missing,
        "required": required,
    }


def _gate_status(source: Mapping, checks: Mapping,
                 names: tuple[str, ...] | list[str]) -> dict[str, str]:
    """Classify each gate without collapsing absence and a measured failure.

    ``False`` is a real, evaluated negative result (for example a harmful rate
    above threshold).  A missing key means the experiment never established
    that gate.  Keeping the distinction in the receipt prevents an empty
    ``promotion_gates`` map from being misread as six negative experiments.
    """
    return {
        name: ("NOT_ESTABLISHED" if name not in source
               else "PASS" if checks.get(name) is True else "FAIL")
        for name in names
    }
