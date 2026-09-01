"""Training-only support envelopes for reason-aware state-shift decisions.

An envelope is a compact, deterministic description of the typed regimes in
which a knowledge claim was actually supported.  It is not an applicability
or authority grant, and calibration/held-out rows are deliberately rejected.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tehm.ids import stable_dumps


SUPPORT_ENVELOPE_VERSION = "support-envelope-v0.1"
_DIMENSIONS = ("structural", "mechanism", "flow", "constraint", "oracle", "history")


class SupportEnvelopeError(ValueError):
    """Malformed or non-training support evidence."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _sequence(value: object, name: str) -> tuple:
    if value is None:
        return ()
    if isinstance(value, Mapping) or isinstance(value, (str, bytes)):
        raise SupportEnvelopeError(f"support envelope {name} must be a sequence")
    if not isinstance(value, Sequence):
        raise SupportEnvelopeError(f"support envelope {name} must be a sequence")
    return tuple(value)


def _training_guard(item: Mapping, name: str) -> None:
    split = item.get("split", item.get("dataset_split"))
    if split != "training":
        raise SupportEnvelopeError(
            f"support envelope {name} requires training split")
    eligible = item.get("learner_eligible", item.get("dataset_learner_eligible"))
    if type(eligible) is not bool or not eligible:
        raise SupportEnvelopeError(
            f"support envelope {name} requires learner-eligible evidence")
    verification = item.get("verification")
    if isinstance(verification, Mapping):
        verdict = verification.get("verdict")
        oracle_complete = verification.get("oracle_complete")
    elif verification is None:
        verdict = item.get("verification_verdict", item.get("verdict"))
        oracle_complete = item.get("oracle_complete")
    else:
        raise SupportEnvelopeError(
            f"support envelope {name} verification must be an object")
    if verdict != "PASS":
        raise SupportEnvelopeError(
            f"support envelope {name} requires PASS oracle evidence")
    if type(oracle_complete) is not bool or not oracle_complete:
        raise SupportEnvelopeError(
            f"support envelope {name} requires complete oracle evidence")


def _pick(item: Mapping, *keys: str):
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
    return None


def _dimension_facts(item: Mapping) -> dict[str, object]:
    mechanism = _pick(item, "mechanism_family", "mechanism_signature")
    profile = _pick(item, "compatibility_profile")
    flow = _pick(item, "flow_regime", "platform", "toolchain_digest", "orfs_root")
    constraint = _pick(item, "constraint_regime", "constraints", "constraint_digest",
                       "timing_target", "obligation_set")
    oracle = _pick(item, "oracle_regime", "oracle_type", "oracle_digest",
                   "verification_regime", "obligations")
    history = _pick(item, "action_history", "prior_action_digests")
    structural = _pick(item, "structural_graph_digest", "structural_signature",
                       "structural_graph", "structure")
    return {
        "structural": structural,
        "mechanism": {"family": mechanism, "profile": profile}
        if mechanism is not None or profile is not None else None,
        "flow": flow,
        "constraint": constraint,
        "oracle": oracle,
        "history": history,
    }


def _claim_facts(knowledge) -> dict[str, list]:
    facts = {dimension: [] for dimension in _DIMENSIONS}
    family = getattr(knowledge, "mechanism_family", None)
    profile = getattr(knowledge, "compatibility_profile", None)
    if family is not None or profile is not None:
        facts["mechanism"].append({"family": family, "profile": profile})
    for entry in getattr(knowledge, "positive_applicability", ()):
        if not isinstance(entry, Mapping):
            continue
        structural = {key: entry[key] for key in entry
                      if key in {"structural_graph_digest", "structural_signature",
                                 "structure", "reset_style", "priority_overlap",
                                 "exit_count"}}
        if structural:
            facts["structural"].append(structural)
        for dimension, keys in {
                "flow": ("flow_regime", "platform", "toolchain_digest", "orfs_root"),
                "constraint": ("constraint_regime", "constraints", "constraint_digest",
                               "timing_target", "obligation_set"),
                "oracle": ("oracle_regime", "oracle_type", "oracle_digest",
                           "verification_regime", "obligations"),
                "history": ("action_history", "prior_action_digests"),
        }.items():
            value = _pick(entry, *keys)
            if value is not None:
                facts[dimension].append(value)
    return facts


def _source_ref(item: Mapping, fallback: str) -> str:
    value = _pick(item, "transition_id", "state_id", "evidence_id", "record_id")
    return str(value) if value is not None else fallback


@dataclass(frozen=True)
class SupportEnvelope:
    knowledge_object_id: str
    dimensions: dict
    evidence_refs: tuple[str, ...]
    source_transition_ids: tuple[str, ...]
    training_only: bool = True
    version: str = SUPPORT_ENVELOPE_VERSION

    def __post_init__(self) -> None:
        if type(self.knowledge_object_id) is not str or not self.knowledge_object_id:
            raise SupportEnvelopeError("support envelope knowledge_object_id is required")
        if not isinstance(self.dimensions, dict) or set(self.dimensions) != set(_DIMENSIONS):
            raise SupportEnvelopeError("support envelope dimensions are incomplete")
        if not isinstance(self.evidence_refs, tuple) or any(
                type(item) is not str or not item for item in self.evidence_refs):
            raise SupportEnvelopeError("support envelope evidence_refs are invalid")
        if not isinstance(self.source_transition_ids, tuple) or any(
                type(item) is not str or not item for item in self.source_transition_ids):
            raise SupportEnvelopeError("support envelope source transition IDs are invalid")
        if not self.source_transition_ids:
            raise SupportEnvelopeError(
                "support envelope requires verified training transitions")
        if self.training_only is not True:
            raise SupportEnvelopeError("support envelope must be training-only")

    def _payload(self) -> dict:
        return {
            "version": self.version, "knowledge_object_id": self.knowledge_object_id,
            "dimensions": self.dimensions, "evidence_refs": list(self.evidence_refs),
            "source_transition_ids": list(self.source_transition_ids),
            "training_only": self.training_only,
        }

    @property
    def envelope_digest(self) -> str:
        return _digest(self._payload())

    def to_dict(self) -> dict:
        return {**self._payload(), "envelope_digest": self.envelope_digest}

    @classmethod
    def from_dict(cls, payload: object) -> "SupportEnvelope":
        if not isinstance(payload, Mapping):
            raise SupportEnvelopeError("support envelope must be an object")
        envelope = cls(
            knowledge_object_id=payload.get("knowledge_object_id"),
            dimensions=dict(payload.get("dimensions") or {}),
            evidence_refs=tuple(payload.get("evidence_refs") or ()),
            source_transition_ids=tuple(payload.get("source_transition_ids") or ()),
            training_only=payload.get("training_only", True),
            version=payload.get("version", SUPPORT_ENVELOPE_VERSION),
        )
        supplied = payload.get("envelope_digest")
        if supplied is not None and supplied != envelope.envelope_digest:
            raise SupportEnvelopeError("support envelope digest mismatch")
        return envelope


def build_support_envelope(knowledge, source_states=(), source_transitions=()) -> SupportEnvelope:
    """Build an envelope from verified training facts only.

    The source collections may contain state/transition dictionaries or typed
    objects exposing ``to_dict``.  Non-training evidence is rejected instead
    of silently widening the transfer domain.
    """
    object_id = getattr(knowledge, "object_id", None)
    if type(object_id) is not str or not object_id:
        raise SupportEnvelopeError("support envelope requires knowledge.object_id")
    facts = _claim_facts(knowledge)
    refs: set[str] = set()
    transition_ids: set[str] = set()
    for collection, name in ((source_states, "source_states"),
                             (source_transitions, "source_transitions")):
        for index, raw in enumerate(_sequence(collection, name)):
            item = raw.to_dict() if hasattr(raw, "to_dict") else raw
            if not isinstance(item, Mapping):
                raise SupportEnvelopeError(f"support envelope {name} item is not an object")
            item = dict(item)
            _training_guard(item, name)
            if name == "source_transitions" and _pick(
                    item, "transition_id", "evidence_id", "record_id") is None:
                raise SupportEnvelopeError(
                    "support envelope source_transitions requires transition_id")
            ref = _source_ref(item, f"{name}:{index}")
            refs.add(ref)
            if name == "source_transitions":
                transition_ids.add(ref)
            item_facts = _dimension_facts(item)
            for dimension, value in item_facts.items():
                if value is not None:
                    facts[dimension].append(value)
    if not transition_ids:
        raise SupportEnvelopeError(
            "support envelope requires verified training transitions")
    dimensions = {}
    for dimension in _DIMENSIONS:
        values = []
        for value in facts[dimension]:
            if value is None:
                continue
            encoded = stable_dumps(value)
            if encoded not in {stable_dumps(item) for item in values}:
                values.append(value)
        dimensions[dimension] = {"values": values}
    return SupportEnvelope(
        knowledge_object_id=object_id, dimensions=dimensions,
        evidence_refs=tuple(sorted(refs)),
        source_transition_ids=tuple(sorted(transition_ids)),
    )


__all__ = [
    "SUPPORT_ENVELOPE_VERSION", "SupportEnvelopeError", "SupportEnvelope",
    "build_support_envelope",
]
