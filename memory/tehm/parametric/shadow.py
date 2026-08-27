"""Fail-closed, read-only Parametric shadow contract.

This is intentionally *not* a Parametric View implementation.  The design
document first permits a shadow RFC only after independent held-out evidence
has passed distance, coverage, uncertainty, lineage-diversity, and replay
gates.  ``build_shadow_proposal`` therefore produces an auditable proposal
record while keeping ``parametric_view_status = NOT_IMPLEMENTED`` and
``promotion_eligible = false``.

The function accepts a :class:`~tehm.physical.memory.PhysicalEffectMemory`
instance only as a read-only predictor.  It never calls ``record``, lifecycle
status APIs, activation, or any canonical write path.  A caller may persist
the returned JSON in an external experiment log, but this package itself does
not persist it.
"""
from __future__ import annotations

import hashlib
import math
from typing import Any

from tehm.ids import stable_dumps
from tehm.views.parametric_stub import PARAMETRIC_VIEW_STATUS


PARAMETRIC_SHADOW_VERSION = "parametric-shadow-v0.1"
PARAMETRIC_SHADOW_STATUS = "SHADOW_ONLY"
SHADOW_PROPOSED = "PROPOSED"
SHADOW_ABSTAINED = "ABSTAINED"

_REQUIRED_READINESS_GATES = (
    "all_retrieval_policies_ready",
    "distance_gate_satisfied",
    "coverage_gate_satisfied",
    "uncertainty_gate_satisfied",
    "lineage_diversity_satisfied",
)


class ParametricShadowError(ValueError):
    """Malformed shadow input; evidence failures are returned as abstentions."""


def build_shadow_proposal(
    memory,
    *,
    family: str,
    graph_context: dict,
    action: dict | None = None,
    calibration_policy: dict,
    readiness: dict,
    replay_evidence: dict,
    policy_scope: dict | None = None,
    effect_key: str | None = None,
    k: int = 5,
    min_unique_contexts: int = 3,
    max_distance: float = 3.0,
) -> dict:
    """Build one deterministic, read-only Parametric shadow proposal.

    ``calibration_policy`` is one family/tier policy from the frozen
    calibration report (``status == ready``).  ``readiness`` is the aggregate
    ``parametric_readiness.json`` report, and ``replay_evidence`` is the output
    of the frozen bundle verifier.  Any failed evidence gate returns an
    ``ABSTAINED`` record with the reason(s); malformed input raises
    :class:`ParametricShadowError` so a caller cannot silently reinterpret it.
    """
    _require_mapping("calibration_policy", calibration_policy)
    _require_mapping("readiness", readiness)
    _require_mapping("replay_evidence", replay_evidence)
    graph_context = _coerce_graph_context(graph_context)
    if action is not None and not isinstance(action, dict):
        raise ParametricShadowError("action must be a JSON object when supplied")
    policy_scope = _coerce_policy_scope(calibration_policy, policy_scope)
    if not isinstance(family, str) or not family.strip():
        raise ParametricShadowError("family must be a non-empty string")
    if memory is None or not callable(getattr(memory, "predict", None)):
        raise ParametricShadowError("memory must expose a read-only predict()")
    try:
        k = max(1, int(k))
        min_unique_contexts = max(2, int(min_unique_contexts))
        max_distance = float(max_distance)
    except (TypeError, ValueError) as exc:
        raise ParametricShadowError("k, min_unique_contexts, and max_distance must be numeric") from exc
    if not math.isfinite(max_distance) or max_distance <= 0:
        raise ParametricShadowError("max_distance must be finite and positive")

    policy_digest = _digest(calibration_policy)
    readiness_digest = _digest(readiness)
    replay_digest = _digest(replay_evidence)
    query_digest = _graph_context_digest(graph_context)
    base = {
        "version": PARAMETRIC_SHADOW_VERSION,
        "shadow_status": SHADOW_ABSTAINED,
        "parametric_shadow_status": PARAMETRIC_SHADOW_STATUS,
        # The materialized five-view contract must remain unchanged until a
        # separate implementation RFC and replay are accepted.
        "parametric_view_status": PARAMETRIC_VIEW_STATUS,
        "family": family,
        "action_digest": (_digest(action) if action is not None else None),
        "effect_key": effect_key,
        "query_graph_context_digest": query_digest,
        "abstained": True,
        "abstain_reasons": [],
        "prediction": None,
        "promotion_eligible": False,
        "canonical_memory_mutation": "none",
        "provenance": {
            "policy_digest": policy_digest,
            "readiness_digest": readiness_digest,
            "replay_digest": replay_digest,
            "bundle_digest": replay_evidence.get("bundle_digest"),
            "manifest_digest": replay_evidence.get("manifest_digest"),
            "heldout_lineages": sorted(_heldout_lineages(calibration_policy)),
            "readiness_status": readiness.get("status"),
            "calibration_status": calibration_policy.get("status"),
            "policy_scope": policy_scope,
            "replay_ok": replay_evidence.get("ok"),
            "roundtrip_byte_stable": replay_evidence.get("roundtrip_byte_stable"),
        },
    }

    reasons = _readiness_reasons(readiness)
    reasons.extend(_replay_reasons(replay_evidence))
    reasons.extend(_policy_reasons(calibration_policy, family, policy_scope,
                                   graph_context))
    if reasons:
        return _finish(base, reasons)

    # PhysicalEffectMemory.predict is read-only.  Keep the hard OOD ceiling at
    # or below the design's 3.0 safety bound; calibration may tighten it but
    # never relax it.
    prediction = memory.predict(
        family=family,
        effect_key=effect_key,
        graph_context=graph_context,
        action=action,
        k=k,
        min_unique_contexts=min_unique_contexts,
        max_distance=min(max_distance, 3.0),
        calibration_policy=calibration_policy,
    )
    if not isinstance(prediction, dict):
        raise ParametricShadowError("memory.predict() must return a mapping")
    if prediction.get("abstained"):
        reasons = list(prediction.get("abstain_reasons") or ["physical_prediction_abstained"])
        base["prediction"] = _prediction_audit(prediction)
        return _finish(base, reasons)

    nearest = prediction.get("nearest_distance")
    if not isinstance(nearest, (int, float)) or not math.isfinite(float(nearest)):
        base["prediction"] = _prediction_audit(prediction)
        return _finish(base, ["missing_nearest_distance"])
    if float(nearest) > min(max_distance, 3.0) + 1.0e-12:
        base["prediction"] = _prediction_audit(prediction)
        return _finish(base, ["out_of_distribution"])

    base.update({
        "shadow_status": SHADOW_PROPOSED,
        "abstained": False,
        "prediction": _prediction_audit(prediction),
    })
    base["provenance"].update({
        "nearest_distance": float(nearest),
        "uncertainty_95": prediction.get("uncertainty_95") or {},
        "unique_graph_contexts": prediction.get("unique_graph_contexts"),
        "support": prediction.get("support"),
    })
    return base


def proposal_digest(proposal: dict) -> str:
    """Return a content digest for a proposal without adding a volatile field."""
    _require_mapping("proposal", proposal)
    return _digest(proposal)


# Naming alias for callers that use the existing retrieval/activation
# vocabulary.  Both names deliberately return a proposal only; neither writes
# a canonical record.
propose_shadow = build_shadow_proposal


def _finish(base: dict, reasons: list[str]) -> dict:
    unique = []
    for reason in reasons:
        reason = str(reason)
        if reason and reason not in unique:
            unique.append(reason)
    base["abstain_reasons"] = unique
    base["provenance"]["abstain_reasons"] = list(unique)
    return base


def _readiness_reasons(readiness: dict) -> list[str]:
    reasons = []
    if readiness.get("status") != "READY_FOR_IMPLEMENTATION":
        reasons.append("parametric_readiness_not_ready")
    if readiness.get("parametric_view_status") != PARAMETRIC_VIEW_STATUS:
        reasons.append("parametric_view_status_changed")
    criteria = readiness.get("criteria")
    if not isinstance(criteria, dict):
        return reasons + ["missing_readiness_criteria"]
    for gate in _REQUIRED_READINESS_GATES:
        if criteria.get(gate) is not True:
            reasons.append(f"readiness_gate_failed:{gate}")
    minimum = criteria.get("minimum_independent_heldout_lineages")
    observed = criteria.get("observed_independent_heldout_lineages")
    if (not _valid_count(minimum) or int(minimum) < 2 or
            not _valid_count(observed) or int(observed) < int(minimum)):
        reasons.append("lineage_diversity_below_minimum")
    return reasons


def _replay_reasons(evidence: dict) -> list[str]:
    reasons = []
    if evidence.get("ok") is not True:
        reasons.append("replay_not_verified")
    if evidence.get("roundtrip_byte_stable") is not True:
        reasons.append("replay_roundtrip_not_byte_stable")
    return reasons


def _policy_reasons(policy: dict, family: str, scope: dict | None,
                    graph_context: dict) -> list[str]:
    reasons = []
    if policy.get("family") not in (None, family):
        reasons.append("calibration_family_mismatch")
    if policy.get("status") != "ready":
        reasons.append("calibration_policy_not_ready")
    if scope is None:
        reasons.append("missing_calibration_policy_scope")
    else:
        if scope.get("family") not in (None, family):
            reasons.append("calibration_scope_family_mismatch")
        for key in ("platform", "dataset_tier"):
            if not scope.get(key):
                reasons.append(f"missing_calibration_scope:{key}")
            elif str(graph_context.get(key) or "") != str(scope[key]):
                reasons.append(f"calibration_scope_mismatch:{key}")
    thresholds = policy.get("thresholds")
    calibration = policy.get("calibration")
    firewall = policy.get("firewall")
    if not isinstance(firewall, dict):
        reasons.append("missing_calibration_firewall")
    else:
        if firewall.get("disjoint") is not True:
            reasons.append("calibration_lineage_firewall_failed")
        if firewall.get("overlap"):
            reasons.append("calibration_lineage_overlap")
        if not _heldout_lineages(policy):
            reasons.append("missing_heldout_lineages")
    if not isinstance(thresholds, dict):
        reasons.append("missing_calibration_thresholds")
    if not isinstance(calibration, dict):
        reasons.append("missing_calibration_summary")
    return reasons


def _heldout_lineages(policy: dict) -> set[str]:
    firewall = policy.get("firewall") or {}
    values = firewall.get("heldout_lineages") or []
    return {str(value) for value in values if str(value)}


def _coerce_policy_scope(policy: dict, explicit: dict | None) -> dict | None:
    scope = explicit if explicit is not None else policy.get("scope")
    if scope is None:
        # A wrapped policy may preserve the report key without changing the
        # calibration payload itself: platform|family|dataset_tier.
        key = policy.get("policy_key")
        if isinstance(key, str):
            parts = key.split("|")
            if len(parts) == 3:
                scope = {"platform": parts[0], "family": parts[1],
                         "dataset_tier": parts[2]}
    if scope is None:
        return None
    _require_mapping("policy_scope", scope)
    return {str(key): value for key, value in scope.items()}


def _prediction_audit(prediction: dict) -> dict:
    """Keep the proposal bounded while retaining activation-relevant evidence."""
    keys = (
        "family", "effect_key", "retrieval_mode", "memory_version",
        "action_conditioned", "action_signature",
        "gradient_claimed", "query_graph_context_digest", "platform",
        "dataset_tier", "abstained", "abstain_reasons", "support",
        "unique_graph_contexts", "neighbour_contexts", "nearest_distance",
        "max_distance", "calibration", "mean_deltas", "uncertainty_95",
        "harmful_metrics", "neighbours", "note",
    )
    return {key: prediction[key] for key in keys if key in prediction}


def _graph_context_digest(context: dict) -> str:
    identity = {key: value for key, value in context.items()
                if key not in {"digest", "source_refs"}}
    digest = hashlib.sha256(stable_dumps(identity).encode()).hexdigest()
    supplied = context.get("digest")
    if supplied is not None and str(supplied) != digest:
        raise ParametricShadowError("graph context digest does not match its content")
    return digest


def _coerce_graph_context(value: Any) -> dict:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    _require_mapping("graph_context", value)
    return dict(value)


def _digest(value: Any) -> str:
    try:
        encoded = stable_dumps(value).encode()
    except (TypeError, ValueError) as exc:
        raise ParametricShadowError("shadow evidence must be JSON serializable") from exc
    return hashlib.sha256(encoded).hexdigest()


def _require_mapping(name: str, value: Any) -> None:
    if not isinstance(value, dict):
        raise ParametricShadowError(f"{name} must be a JSON object")


def _valid_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
