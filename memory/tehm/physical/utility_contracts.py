"""Typed, fail-closed utility contracts for physical-effect proposals.

The raw Pareto verdict remains an immutable observation of the executed
before/after pair.  A utility contract is a separately frozen engineering
policy: it names the objective and the resource budgets that make an action
worth proposing.  It never changes the raw verdict, writes TEHM, or grants
promotion authority.

``select_contract_proposal`` is deliberately a proposal/abstain boundary.  It
requires a ready, action-bound calibration policy, complete hard-oracle
evidence, a baseline PPA, and prediction intervals that fit the contract.
Missing evidence, OOD context, or an interval crossing a boundary abstains.
"""
from __future__ import annotations

import copy
import hashlib
import math
from collections.abc import Callable, Mapping

from tehm.ids import stable_dumps
from tehm.physical.effects import PHYSICAL_METRICS, extract_deltas
from tehm.physical.memory import _action_signature


UTILITY_CONTRACT_VERSION = "typed-utility-contract-v1"
TIMING_RELIEF_BUDGETED_V1_ID = "TIMING_RELIEF_BUDGETED_V1"
TIMING_RELIEF_BUDGETED_V2_50_TO_45_ID = "TIMING_RELIEF_BUDGETED_V2_50_TO_45"
DENSITY_RELIEF_NONREGRESSION_32_ID = "DENSITY_RELIEF_NONREGRESSION_32"
ROUTING_CAPACITY_RECOVERY_NONREGRESSION_005_ID = (
    "ROUTING_CAPACITY_RECOVERY_NONREGRESSION_005")

# This is a pre-registered proposal for the next prospective cohort.  It is
# intentionally not derived or rewritten from a promotion result.  Existing
# V4 observations remain raw Pareto evidence and are not contract validation.
_TIMING_RELIEF_BUDGETED_V1 = {
    "version": UTILITY_CONTRACT_VERSION,
    "contract_id": TIMING_RELIEF_BUDGETED_V1_ID,
    "status": "PRE_REGISTERED_FOR_PROSPECTIVE_COHORT",
    "action_signature": {
        "domain": "flow.CONFIG_DELTA",
        "transformation_family": "DENSITY_RELIEF",
        "config_edits": {"CORE_UTILIZATION": "40"},
        "operation_point": "50->40",
    },
    "primary_objective": {"wns_delta_ns": {"minimum": 0.01}},
    "hard_constraints": {
        "equivalence": "PASS",
        "drc": "PASS",
        "lvs": "PASS",
        "timing": "PASS",
        "tns_delta_ns": {"minimum": 0.0},
    },
    "resource_budgets": {
        "area_delta_percent": {"maximum": 2.0},
        "power_delta_percent": {"maximum": 3.0},
    },
    "runtime_policy": {
        "interval_must_fit_contract": True,
        "ood_action": "ABSTAIN",
        "missing_evidence_action": "ABSTAIN",
    },
    "authority": {
        "raw_pareto_gate_unchanged": True,
        "canonical_memory_mutation": "none",
        "promotion_eligible": False,
    },
}

# A new action signature is intentionally a separate pre-registration.  It
# inherits the engineering objective/budgets only as an explicit draft; no V1
# calibration, rule, or promotion receipt is compatible with CORE_UTILIZATION
# 50->45.
_TIMING_RELIEF_BUDGETED_V2_50_TO_45 = copy.deepcopy(
    _TIMING_RELIEF_BUDGETED_V1)
_TIMING_RELIEF_BUDGETED_V2_50_TO_45.update({
    "contract_id": TIMING_RELIEF_BUDGETED_V2_50_TO_45_ID,
    "status": "PRE_REGISTERED_NEW_ACTION_SIGNATURE",
})
_TIMING_RELIEF_BUDGETED_V2_50_TO_45["action_signature"] = {
    **_TIMING_RELIEF_BUDGETED_V2_50_TO_45["action_signature"],
    "config_edits": {"CORE_UTILIZATION": "45"},
    "operation_point": "50->45",
}

# This is a new, explicit contract for the next source-disjoint density
# cohort.  It is deliberately stricter than the historical action32 shadow
# experiment: timing and resource deltas may not regress, and positive utility
# still has to be established by the independent grouped-calibration gate.
# No historical v113-v121 row is retroactively evaluated under this contract.
_DENSITY_RELIEF_NONREGRESSION_32 = {
    "version": UTILITY_CONTRACT_VERSION,
    "contract_id": DENSITY_RELIEF_NONREGRESSION_32_ID,
    "status": "PRE_REGISTERED_FOR_NEXT_SOURCE_DISJOINT_COHORT",
    "action_signature": {
        "domain": "flow.CONFIG_DELTA",
        "transformation_family": "DENSITY_RELIEF",
        "config_edits": {"CORE_UTILIZATION": "32"},
        "operation_point": "base<32->32",
    },
    "primary_objective": {"wns_delta_ns": {"minimum": 0.0}},
    "hard_constraints": {
        "equivalence": "PASS",
        "drc": "PASS",
        "lvs": "PASS",
        "timing": "PASS",
        "tns_delta_ns": {"minimum": 0.0},
    },
    "resource_budgets": {
        "area_delta_percent": {"maximum": 0.0},
        "power_delta_percent": {"maximum": 0.0},
    },
    "runtime_policy": {
        "interval_must_fit_contract": True,
        "ood_action": "ABSTAIN",
        "missing_evidence_action": "ABSTAIN",
    },
    "authority": {
        "raw_pareto_gate_unchanged": True,
        "canonical_memory_mutation": "none",
        "promotion_eligible": False,
    },
}

# Routing has the strongest current shadow evidence, but that evidence was
# collected before a typed utility contract was attached to the campaign.  A
# fresh source-disjoint cohort must therefore be run under this independently
# frozen contract; existing r5 rows are never retroactively regraded.
_ROUTING_CAPACITY_RECOVERY_NONREGRESSION_005 = {
    "version": UTILITY_CONTRACT_VERSION,
    "contract_id": ROUTING_CAPACITY_RECOVERY_NONREGRESSION_005_ID,
    "status": "PRE_REGISTERED_FOR_PROSPECTIVE_COHORT",
    "action_signature": {
        "domain": "flow.CONFIG_DELTA",
        "transformation_family": "ROUTING_CAPACITY_RECOVERY",
        "config_edits": {"ROUTING_LAYER_ADJUSTMENT": "0.05"},
        "operation_point": "default->0.05",
    },
    "primary_objective": {"wns_delta_ns": {"minimum": 0.0}},
    "hard_constraints": {
        "equivalence": "PASS",
        "drc": "PASS",
        "lvs": "PASS",
        "timing": "PASS",
        "tns_delta_ns": {"minimum": 0.0},
    },
    "resource_budgets": {
        "area_delta_percent": {"maximum": 0.0},
        "power_delta_percent": {"maximum": 0.0},
    },
    "runtime_policy": {
        "interval_must_fit_contract": True,
        "ood_action": "ABSTAIN",
        "missing_evidence_action": "ABSTAIN",
    },
    "authority": {
        "raw_pareto_gate_unchanged": True,
        "canonical_memory_mutation": "none",
        "promotion_eligible": False,
    },
}


class UtilityContractError(ValueError):
    """Malformed or internally inconsistent typed utility contract."""


def timing_relief_budgeted_v1() -> dict:
    """Return a defensive copy of the frozen contract definition."""
    return copy.deepcopy(_TIMING_RELIEF_BUDGETED_V1)


def timing_relief_budgeted_v2_50_to_45() -> dict:
    """Return the independent, not-yet-executed 50->45 contract draft."""
    return copy.deepcopy(_TIMING_RELIEF_BUDGETED_V2_50_TO_45)


def density_relief_nonregression_32() -> dict:
    """Return the pre-registered exact action32 non-regression contract."""
    return copy.deepcopy(_DENSITY_RELIEF_NONREGRESSION_32)


def routing_capacity_recovery_nonregression_005() -> dict:
    """Return the prospective default->0.05 routing contract."""
    return copy.deepcopy(_ROUTING_CAPACITY_RECOVERY_NONREGRESSION_005)


def known_utility_contracts() -> dict[str, Callable[[], dict]]:
    """Return the immutable contract catalog used by manifest validators."""
    return {
        TIMING_RELIEF_BUDGETED_V1_ID: timing_relief_budgeted_v1,
        TIMING_RELIEF_BUDGETED_V2_50_TO_45_ID: timing_relief_budgeted_v2_50_to_45,
        DENSITY_RELIEF_NONREGRESSION_32_ID: density_relief_nonregression_32,
        ROUTING_CAPACITY_RECOVERY_NONREGRESSION_005_ID:
            routing_capacity_recovery_nonregression_005,
    }


def utility_contract_digest(contract: Mapping) -> str:
    """Return a content digest suitable for a shadow receipt."""
    validate_utility_contract(contract)
    return hashlib.sha256(stable_dumps(dict(contract)).encode("utf-8")).hexdigest()


def contract_action(contract: Mapping | None = None) -> dict:
    """Build the typed action envelope bound to one utility contract."""
    contract = contract or _TIMING_RELIEF_BUDGETED_V1
    validate_utility_contract(contract)
    signature = contract["action_signature"]
    return {
        "domain": signature["domain"],
        "transformation_family": signature["transformation_family"],
        "payload": {
            "config_edits": copy.deepcopy(signature["config_edits"]),
            "utility_contract_id": contract["contract_id"],
        },
    }


def validate_utility_contract(contract: Mapping) -> None:
    """Validate the contract schema and its fail-closed numeric boundaries."""
    if not isinstance(contract, Mapping):
        raise UtilityContractError("utility contract must be a mapping")
    if contract.get("version") != UTILITY_CONTRACT_VERSION:
        raise UtilityContractError("utility contract version mismatch")
    if not isinstance(contract.get("contract_id"), str) or not contract["contract_id"]:
        raise UtilityContractError("utility contract_id is required")
    signature = contract.get("action_signature")
    if not isinstance(signature, Mapping):
        raise UtilityContractError("action_signature is required")
    for key in ("domain", "transformation_family", "operation_point"):
        if not isinstance(signature.get(key), str) or not signature[key]:
            raise UtilityContractError(f"action_signature.{key} is required")
    edits = signature.get("config_edits")
    if not isinstance(edits, Mapping) or not edits:
        raise UtilityContractError("action_signature.config_edits is required")
    primary = contract.get("primary_objective")
    hard = contract.get("hard_constraints")
    budgets = contract.get("resource_budgets")
    runtime = contract.get("runtime_policy")
    if not isinstance(primary, Mapping) or not isinstance(hard, Mapping):
        raise UtilityContractError("primary_objective and hard_constraints are required")
    if not isinstance(budgets, Mapping) or not isinstance(runtime, Mapping):
        raise UtilityContractError("resource_budgets and runtime_policy are required")
    _bound(primary, "wns_delta_ns", "minimum")
    _bound(hard, "tns_delta_ns", "minimum")
    _bound(budgets, "area_delta_percent", "maximum", nonnegative=True)
    _bound(budgets, "power_delta_percent", "maximum", nonnegative=True)
    if runtime.get("interval_must_fit_contract") is not True:
        raise UtilityContractError("interval_must_fit_contract must be true")
    if runtime.get("ood_action") != "ABSTAIN":
        raise UtilityContractError("ood_action must be ABSTAIN")
    if runtime.get("missing_evidence_action") != "ABSTAIN":
        raise UtilityContractError("missing_evidence_action must be ABSTAIN")


def evaluate_observed_contract(
        *, contract: Mapping, action: Mapping | None, before_ppa: Mapping,
        after_ppa: Mapping, checks: Mapping, obligation_coverage: float = 1.0) -> dict:
    """Evaluate one completed before/after pair under a frozen contract.

    ``FAIL`` means the pair was fully observed but violated the contract;
    ``ABSTAIN`` means the pair cannot be evaluated safely.  ``raw_pareto`` is
    always reported separately and is never rewritten by this function.
    """
    validate_utility_contract(contract)
    base = _result_base(contract, action)
    if not isinstance(before_ppa, Mapping) or not isinstance(after_ppa, Mapping):
        return _abstain(base, ["missing_ppa_evidence"])
    if not isinstance(checks, Mapping):
        return _abstain(base, ["missing_hard_oracles"])
    action_reason = _action_reason(contract, action)
    if action_reason:
        return _abstain(base, [action_reason])
    missing_checks = _missing_hard_checks(contract, checks)
    if missing_checks:
        return _abstain(base, missing_checks)
    deltas = extract_deltas(dict(before_ppa), dict(after_ppa))
    base["observed_deltas"] = deltas
    base["raw_pareto"] = _raw_pareto(deltas)
    failures = _hard_check_failures(contract, checks)
    coverage = _finite(obligation_coverage)
    if coverage is None:
        return _abstain(base, ["missing_obligation_coverage"])
    if coverage < 1.0:
        failures.append("obligation_coverage_below_1")
    baseline_area = _baseline_metric(before_ppa, "area_um2")
    baseline_power = _baseline_metric(before_ppa, "power_w")
    area_delta = _finite(deltas.get("area_um2"))
    power_delta = _finite(deltas.get("power_w"))
    if baseline_area is None or baseline_power is None:
        return _abstain(base, ["missing_baseline_area_or_power"])
    area_pct = 100.0 * area_delta / baseline_area if area_delta is not None else None
    power_pct = 100.0 * power_delta / baseline_power if power_delta is not None else None
    base["observed_relative"] = {
        "area_delta_percent": area_pct,
        "power_delta_percent": power_pct,
    }
    failures.extend(_observed_bound_failures(contract, deltas, area_pct, power_pct))
    return _finish_observed(base, failures)


def select_contract_proposal(
        memory, *, graph_context: Mapping, baseline_ppa: Mapping,
        calibration_policy: Mapping, hard_checks: Mapping,
        obligation_coverage: float, action: Mapping | None = None,
        contract: Mapping | None = None, k: int = 5,
        min_unique_contexts: int = 3, max_distance: float = 3.0) -> dict:
    """Return a contract proposal or a fail-closed abstention.

    This function only calls ``memory.predict``.  It never records an effect,
    changes a rule status, opens a writable database, or returns promotion
    authority.  Prediction intervals must fit every contract boundary.
    """
    contract = contract or _TIMING_RELIEF_BUDGETED_V1
    validate_utility_contract(contract)
    action = action or contract_action(contract)
    base = _result_base(contract, action)
    if not isinstance(graph_context, Mapping):
        return _abstain(base, ["missing_graph_context"])
    if not graph_context.get("digest"):
        return _abstain(base, ["missing_graph_context_digest"])
    if not isinstance(baseline_ppa, Mapping):
        return _abstain(base, ["missing_baseline_ppa"])
    if not isinstance(calibration_policy, Mapping) or calibration_policy.get("status") != "ready":
        return _abstain(base, ["calibration_policy_not_ready"])
    action_reason = _action_reason(contract, action)
    if action_reason:
        return _abstain(base, [action_reason])
    hard_failures = _hard_check_failures(contract, hard_checks)
    if hard_failures:
        return _abstain(base, [f"hard_oracle_failed:{x}" for x in hard_failures])
    coverage = _finite(obligation_coverage)
    if coverage is None or coverage < 1.0:
        return _abstain(base, ["obligation_coverage_below_1"])
    try:
        ceiling = min(float(max_distance), 3.0)
    except (TypeError, ValueError):
        return _abstain(base, ["invalid_ood_ceiling"])
    if not math.isfinite(ceiling) or ceiling <= 0:
        return _abstain(base, ["invalid_ood_ceiling"])
    prediction = memory.predict(
        family=contract["action_signature"]["transformation_family"],
        graph_context=dict(graph_context), action=dict(action), k=max(1, int(k)),
        min_unique_contexts=max(2, int(min_unique_contexts)), max_distance=ceiling,
        calibration_policy=dict(calibration_policy))
    base["prediction"] = _prediction_audit(prediction)
    if not isinstance(prediction, Mapping):
        return _abstain(base, ["invalid_prediction"])
    if prediction.get("abstained"):
        return _abstain(base, list(prediction.get("abstain_reasons") or ["prediction_abstained"]))
    distance = _finite(prediction.get("nearest_distance"))
    if distance is None or distance > ceiling:
        return _abstain(base, ["out_of_distribution"])
    baseline_area = _baseline_metric(baseline_ppa, "area_um2")
    baseline_power = _baseline_metric(baseline_ppa, "power_w")
    if baseline_area is None or baseline_power is None:
        return _abstain(base, ["missing_baseline_area_or_power"])
    failures = _prediction_bound_failures(
        contract, prediction, baseline_area=baseline_area, baseline_power=baseline_power)
    if failures:
        return _abstain(base, failures)
    base.update({
        "status": "PROPOSED",
        "abstained": False,
        "proposal_only": True,
        "promotion_eligible": False,
        "context_predicate": {
            "platform": graph_context.get("platform"),
            "dataset_tier": graph_context.get("dataset_tier"),
            "selector": "conformal_interval_contract_v1",
            "ood_ceiling": ceiling,
        },
    })
    base["rule_proposal"] = _build_context_rule_proposal(
        contract=contract, action=action, graph_context=graph_context,
        context_predicate=base["context_predicate"], prediction=prediction)
    return base


def _build_context_rule_proposal(*, contract: Mapping, action: Mapping,
                                 graph_context: Mapping,
                                 context_predicate: Mapping,
                                 prediction: Mapping) -> dict:
    """Build a non-persistent candidate rule with a non-empty context scope.

    This is deliberately a proposal envelope, not a ``tehm_rules`` insert.
    The exact graph digest anchors the observed context while platform/tier and
    the OOD ceiling describe the bounded generalization predicate.  Runtime
    retrieval cannot see this object until an independent lifecycle authority
    materializes and promotes it.
    """
    context_predicates = {
        "platform": graph_context.get("platform"),
        "dataset_tier": graph_context.get("dataset_tier"),
        "graph_context_digest": graph_context.get("digest"),
        "selector": context_predicate.get("selector"),
        "ood_ceiling": context_predicate.get("ood_ceiling"),
        "utility_contract_id": contract["contract_id"],
    }
    context_predicates = {
        key: value for key, value in context_predicates.items()
        if value is not None and value != ""
    }
    body = {
        "domain": contract["action_signature"]["domain"],
        "transformation_family": contract["action_signature"]["transformation_family"],
        "action": copy.deepcopy(dict(action)),
        "context_predicates": context_predicates,
        "prediction_digest": hashlib.sha256(
            stable_dumps(dict(prediction)).encode("utf-8")).hexdigest(),
    }
    rule_id = "parametric-shadow-rule:" + hashlib.sha256(
        stable_dumps(body).encode("utf-8")).hexdigest()[:24]
    return {
        "rule_id": rule_id,
        "status": "candidate",
        "domain": body["domain"],
        "transformation_family": body["transformation_family"],
        "action": body["action"],
        "hard_preconditions": [
            "equivalence=PASS", "drc=PASS", "lvs=PASS", "timing=PASS",
            "obligation_coverage=1.0",
        ],
        "context_predicates": context_predicates,
        "utility_contract_id": contract["contract_id"],
        "prediction_digest": body["prediction_digest"],
        "runtime_eligible": False,
        "canonical_memory_mutation": "none",
        "promotion_eligible": False,
    }


def _result_base(contract: Mapping, action: Mapping | None) -> dict:
    return {
        "version": UTILITY_CONTRACT_VERSION,
        "contract_id": contract.get("contract_id"),
        "contract_digest": utility_contract_digest(contract),
        "action": copy.deepcopy(dict(action)) if isinstance(action, Mapping) else None,
        "status": "ABSTAINED",
        "abstained": True,
        "abstain_reasons": [],
        "promotion_eligible": False,
        "canonical_memory_mutation": "none",
    }


def _finish_observed(base: dict, failures: list[str]) -> dict:
    failures = _unique(failures)
    if failures:
        base.update({"status": "FAIL", "contract_eligible": False,
                     "failures": failures, "contract_harmful": True})
    else:
        base.update({"status": "PASS", "contract_eligible": True,
                     "failures": [], "contract_harmful": False})
    base["abstained"] = False
    return base


def _abstain(base: dict, reasons: list[str]) -> dict:
    base["status"] = "ABSTAINED"
    base["abstained"] = True
    base["abstain_reasons"] = _unique(reasons)
    base["contract_eligible"] = False
    return base


def _action_reason(contract: Mapping, action: Mapping | None) -> str | None:
    return action_contract_binding_reason(action, contract)


def action_contract_binding_reason(action: Mapping | None,
                                   contract: Mapping) -> str | None:
    """Return a stable reason when an action is not bound to ``contract``."""
    validate_utility_contract(contract)
    if not isinstance(action, Mapping):
        return "missing_action"
    expected = contract["action_signature"]
    actual = _action_signature(dict(action))
    if actual is None:
        return "invalid_action_signature"
    expected_signature = {
        "domain": expected["domain"],
        "transformation_family": expected["transformation_family"],
        "config_edit_keys": sorted(str(k) for k in expected["config_edits"]),
        "config_edit_values": {str(k): expected["config_edits"][k]
                                for k in sorted(expected["config_edits"], key=str)},
        "typed_action": None,
    }
    if actual != expected_signature:
        return "action_signature_mismatch"
    payload = action.get("payload") if isinstance(action.get("payload"), Mapping) else action
    if payload.get("utility_contract_id") != contract["contract_id"]:
        return "utility_contract_binding_mismatch"
    return None


def _hard_check_failures(contract: Mapping, checks: Mapping | None) -> list[str]:
    if not isinstance(checks, Mapping):
        return ["missing_hard_oracles"]
    failures = []
    for name, required in contract["hard_constraints"].items():
        if name.endswith("_delta_ns"):
            continue
        value = checks.get(name)
        if not _pass_status(value, required):
            failures.append(name)
    return failures


def _missing_hard_checks(contract: Mapping, checks: Mapping) -> list[str]:
    return [f"missing_hard_oracle:{name}"
            for name, required in contract["hard_constraints"].items()
            if not name.endswith("_delta_ns") and name not in checks]


def _pass_status(value, required) -> bool:
    if required == "PASS":
        return value is True or (isinstance(value, str) and value.upper() == "PASS")
    return value == required


def _observed_bound_failures(contract, deltas, area_pct, power_pct) -> list[str]:
    failures = []
    wns = _finite(deltas.get("wns_ns"))
    tns = _finite(deltas.get("tns_ns"))
    if wns is None:
        failures.append("missing_wns_delta_ns")
    elif wns < float(contract["primary_objective"]["wns_delta_ns"]["minimum"]):
        failures.append("wns_delta_below_objective")
    if tns is None:
        failures.append("missing_tns_delta_ns")
    elif tns < float(contract["hard_constraints"]["tns_delta_ns"]["minimum"]):
        failures.append("tns_delta_below_minimum")
    area_max = float(contract["resource_budgets"]["area_delta_percent"]["maximum"])
    power_max = float(contract["resource_budgets"]["power_delta_percent"]["maximum"])
    if area_pct is None:
        failures.append("missing_area_delta_percent")
    elif area_pct > area_max:
        failures.append("area_budget_exceeded")
    if power_pct is None:
        failures.append("missing_power_delta_percent")
    elif power_pct > power_max:
        failures.append("power_budget_exceeded")
    return failures


def _prediction_bound_failures(contract, prediction, *, baseline_area, baseline_power) -> list[str]:
    intervals = prediction.get("uncertainty_95") or {}
    failures = []
    wns_lower = _interval_bound(intervals, "wns_ns", "lower_95", failures)
    tns_lower = _interval_bound(intervals, "tns_ns", "lower_95", failures)
    area_upper = _interval_bound(intervals, "area_um2", "upper_95", failures)
    power_upper = _interval_bound(intervals, "power_w", "upper_95", failures)
    if wns_lower is not None and wns_lower < float(contract["primary_objective"]["wns_delta_ns"]["minimum"]):
        failures.append("wns_interval_below_objective")
    if tns_lower is not None and tns_lower < float(contract["hard_constraints"]["tns_delta_ns"]["minimum"]):
        failures.append("tns_interval_below_minimum")
    if area_upper is not None and 100.0 * area_upper / baseline_area > float(contract["resource_budgets"]["area_delta_percent"]["maximum"]):
        failures.append("area_interval_exceeds_budget")
    if power_upper is not None and 100.0 * power_upper / baseline_power > float(contract["resource_budgets"]["power_delta_percent"]["maximum"]):
        failures.append("power_interval_exceeds_budget")
    return failures


def _interval_bound(intervals, metric, key, failures):
    row = intervals.get(metric)
    value = _finite(row.get(key)) if isinstance(row, Mapping) else None
    if value is None:
        failures.append(f"missing_{metric}_{key}")
    return value


def _raw_pareto(deltas: Mapping) -> dict:
    harmful = []
    improved = []
    for metric, value in deltas.items():
        value = _finite(value)
        if value is None:
            continue
        if metric in {"area_um2", "power_w", "congestion", "drc_violations", "tns_ns"}:
            (harmful if value > 0 else improved if value < 0 else []).append(metric)
        elif metric == "wns_ns":
            (harmful if value < 0 else improved if value > 0 else []).append(metric)
    verdict = "HARMFUL" if harmful else "PARETO_SAFE" if improved else "NEUTRAL"
    return {"verdict": verdict, "harmful_metrics": sorted(harmful),
            "improved_metrics": sorted(improved)}


def _baseline_metric(ppa: Mapping, metric: str) -> float | None:
    if metric == "area_um2":
        # ORFS extract_ppa emits area in either the legacy summary payload or
        # the geometry payload used by the physical campaign runner.  Both are
        # source-bound evidence; refusing the geometry form would turn a real
        # PPA baseline into a false ``ABSTAINED`` contract observation.
        paths = (("summary", "area", "design_area_um2"),
                 ("ppa_metrics", "area_um2"),
                 ("geometry", "die_area_um2"),
                 ("geometry", "core_area_um2"))
    elif metric == "power_w":
        paths = (("summary", "power", "total_power_w"), ("ppa_metrics", "power_w"))
    else:
        paths = (("ppa_metrics", metric),)
    for path in paths:
        value = ppa
        for key in path:
            value = value.get(key) if isinstance(value, Mapping) else None
        value = _finite(value)
        if value is not None and value > 0:
            return value
    return None


def _prediction_audit(prediction) -> dict:
    if not isinstance(prediction, Mapping):
        return {"type": type(prediction).__name__}
    keep = ("abstained", "abstain_reasons", "nearest_distance", "support",
            "unique_graph_contexts", "query_graph_context_digest",
            "action_signature", "mean_deltas", "uncertainty_95")
    return {key: copy.deepcopy(prediction[key]) for key in keep if key in prediction}


def _bound(container, key, bound, *, nonnegative=False):
    value = container.get(key)
    if not isinstance(value, Mapping):
        raise UtilityContractError(f"{key} must define {bound}")
    number = _finite(value.get(bound))
    if number is None or (nonnegative and number < 0):
        raise UtilityContractError(f"{key}.{bound} must be finite")


def _finite(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _unique(values) -> list[str]:
    result = []
    for value in values:
        value = str(value)
        if value and value not in result:
            result.append(value)
    return result


__all__ = [
    "DENSITY_RELIEF_NONREGRESSION_32_ID",
    "ROUTING_CAPACITY_RECOVERY_NONREGRESSION_005_ID",
    "TIMING_RELIEF_BUDGETED_V1_ID", "TIMING_RELIEF_BUDGETED_V2_50_TO_45_ID",
    "UTILITY_CONTRACT_VERSION",
    "UtilityContractError", "contract_action", "evaluate_observed_contract",
    "action_contract_binding_reason", "known_utility_contracts",
    "select_contract_proposal",
    "density_relief_nonregression_32", "timing_relief_budgeted_v1",
    "routing_capacity_recovery_nonregression_005",
    "timing_relief_budgeted_v2_50_to_45",
    "utility_contract_digest", "validate_utility_contract",
]
