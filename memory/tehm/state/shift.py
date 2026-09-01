"""Deterministic state-shift evaluation against a training support envelope."""
from __future__ import annotations

import math
from collections.abc import Mapping

from tehm.ids import stable_dumps

from .receipts import ResolvedMemoryState
from .shift_receipts import SHIFT_DIMENSIONS, StateShiftReceipt, _digest
from .support_envelope import SupportEnvelope


class StateShiftError(ValueError):
    """State-shift facts or envelope are malformed."""


_DIMENSION_KEYS = {
    "structural": ("structural_graph_digest", "structural_signature",
                   "structural_graph", "structure"),
    "mechanism": ("mechanism_family", "mechanism_signature", "mechanism"),
    "flow": ("flow_regime", "platform", "toolchain_digest", "orfs_root"),
    "constraint": ("constraint_regime", "constraints", "constraint_digest",
                   "timing_target", "obligation_set"),
    "oracle": ("oracle_regime", "oracle_type", "oracle_digest",
               "verification_regime", "obligations"),
    "history": ("action_history", "prior_action_digests"),
}


def _values(value: object) -> tuple:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        return (dict(value),)
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(value)
    return (value,)


def _current_facts(context: Mapping) -> dict[str, tuple]:
    result = {}
    for dimension, keys in _DIMENSION_KEYS.items():
        if dimension == "mechanism":
            signature = context.get("mechanism_signature")
            if isinstance(signature, Mapping):
                result[dimension] = (dict(signature),)
                continue
            if "mechanism_family" in context or "compatibility_profile" in context:
                result[dimension] = ({
                    "family": context.get("mechanism_family"),
                    "profile": context.get("compatibility_profile"),
                },)
                continue
        value = None
        for key in keys:
            if key in context and context[key] is not None:
                value = context[key]
                break
        result[dimension] = _values(value)
    return result


def _supported_values(envelope: SupportEnvelope, dimension: str) -> tuple:
    raw = envelope.dimensions.get(dimension) or {}
    if not isinstance(raw, Mapping):
        raise StateShiftError(f"support envelope {dimension} is malformed")
    values = raw.get("values", ())
    if not isinstance(values, (list, tuple)):
        raise StateShiftError(f"support envelope {dimension}.values is malformed")
    return tuple(values)


def _matches(current: tuple, supported: tuple) -> bool:
    if not current or not supported:
        return True
    supported_encoded = {stable_dumps(item) for item in supported}
    return any(stable_dumps(item) in supported_encoded for item in current)


def evaluate_state_shift(
        current_context: Mapping,
        resolved_state: ResolvedMemoryState | Mapping,
        knowledge,
        envelope: SupportEnvelope,
        *, shift_threshold: float = 0.15,
        evidence_refs=(),
) -> StateShiftReceipt:
    """Compare typed current facts with training-only support facts.

    Missing dimensions are treated as unknown (zero shift), not as evidence of
    transfer.  This is intentionally conservative at the router boundary:
    unresolved state remains ``ABSTAIN`` rather than becoming STATE_SHIFT.
    """
    if not isinstance(current_context, Mapping):
        raise StateShiftError("current state context must be an object")
    if not isinstance(resolved_state, (ResolvedMemoryState, Mapping)):
        raise StateShiftError("resolved_state must be a resolved state")
    if not isinstance(knowledge, object) or not getattr(knowledge, "object_id", None):
        raise StateShiftError("state shift requires knowledge object")
    if not isinstance(envelope, SupportEnvelope):
        raise StateShiftError("state shift requires SupportEnvelope")
    if envelope.knowledge_object_id != knowledge.object_id:
        raise StateShiftError("state shift knowledge/envelope mismatch")
    if isinstance(shift_threshold, bool) or not isinstance(shift_threshold, (int, float)):
        raise StateShiftError("state shift threshold must be numeric")
    shift_threshold = float(shift_threshold)
    if not math.isfinite(shift_threshold) or not 0.0 <= shift_threshold <= 1.0:
        raise StateShiftError("state shift threshold must be in [0,1]")
    facts = _current_facts(dict(current_context))
    names = ("structural", "mechanism", "flow", "constraint", "oracle", "history")
    scores = {}
    for name in names:
        supported = _supported_values(envelope, name)
        # A dimension with no current or support fact is unknown.  It cannot
        # independently create a state shift.
        scores[name] = 0.0 if _matches(facts[name], supported) else 1.0
    aggregate = round(sum(scores.values()) / len(names), 6)
    shifted = tuple(f"{name}_shift" for name in names if scores[name] > 0.0)
    transferable = aggregate <= shift_threshold
    reason = "NO_SHIFT" if transferable else "STATE_SHIFT"
    resolution_id = (resolved_state.resolution_id if isinstance(
        resolved_state, ResolvedMemoryState) else resolved_state.get("resolution_id"))
    if type(resolution_id) is not str or not resolution_id:
        raise StateShiftError("state shift current resolution ID is required")
    refs = tuple(sorted({str(item) for item in evidence_refs if str(item)}))
    payload = {
        "version": "state-shift-v0.1", "current_resolution_id": resolution_id,
        "knowledge_object_id": knowledge.object_id,
        "support_envelope_digest": envelope.envelope_digest,
        "structural_shift": scores["structural"],
        "mechanism_shift": scores["mechanism"], "flow_shift": scores["flow"],
        "constraint_shift": scores["constraint"], "oracle_shift": scores["oracle"],
        "history_shift": scores["history"], "aggregate_shift": aggregate,
        "shifted_dimensions": tuple(shifted), "transferable": transferable,
        "reason": reason, "evidence_refs": refs,
    }
    return StateShiftReceipt(**payload, replay_digest=_digest(payload))


__all__ = ["StateShiftError", "evaluate_state_shift"]
