"""Training-only support envelopes for reason-aware state-shift decisions.

An envelope is a compact, deterministic description of the typed regimes in
which a knowledge claim was actually supported.  It is not an applicability
or authority grant, and calibration/held-out rows are deliberately rejected.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
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


def build_support_envelope_from_transitions(
        conn: sqlite3.Connection, knowledge, transition_ids, *,
        campaign_id: str) -> SupportEnvelope:
    """Derive support facts from canonical learner evidence and raw scopes.

    This database-bound entry point is required for real challenge cohorts.
    The legacy mapping entry point remains useful for fixtures, but caller
    booleans are not authority here.
    """
    from pathlib import Path
    from tehm.adapters.r2g_evidence import parse_config_mk
    from tehm.causal.mechanism import load_transition_facts
    from tehm.dataset import validate_membership_row
    from tehm.verified_execution import require_verified_transition

    if type(campaign_id) is not str or not campaign_id.strip():
        raise SupportEnvelopeError("support envelope campaign_id is required")
    ids = _sequence(transition_ids, "transition_ids")
    if not ids or any(type(item) is not str or not item for item in ids) or len(set(ids)) != len(ids):
        raise SupportEnvelopeError("support envelope transition_ids are invalid")
    path_sources: set[str] = set()
    for path_id in getattr(knowledge, "causal_path_ids", ()):
        row = conn.execute("SELECT source_transitions_json FROM tehm_causal_paths WHERE path_id=?",
                           (path_id,)).fetchone()
        if row is None:
            raise SupportEnvelopeError("support envelope knowledge path is missing")
        try:
            values = json.loads(row[0])
        except (TypeError, json.JSONDecodeError) as exc:
            raise SupportEnvelopeError("support envelope knowledge path sources are malformed") from exc
        if not isinstance(values, list):
            raise SupportEnvelopeError("support envelope knowledge path sources are malformed")
        path_sources.update(values)
    if not set(ids) <= path_sources:
        raise SupportEnvelopeError("support envelope transition is not a Knowledge source")

    records = []
    for transition_id in ids:
        membership = conn.execute(
            "SELECT split, learner_eligible FROM tehm_dataset_membership "
            "WHERE transition_id=? AND campaign_id=?", (transition_id, campaign_id)).fetchone()
        if membership is None or validate_membership_row(membership) != (True, "training"):
            raise SupportEnvelopeError("support envelope requires exact training membership")
        try:
            require_verified_transition(conn, transition_id)
            facts = load_transition_facts(conn, transition_id)
        except ValueError as exc:
            raise SupportEnvelopeError("support envelope transition is not independently verified") from exc
        if facts.action.get("domain") == "flow.BASELINE_CONTROL" or facts.verifier.get("verdict") != "PASS":
            raise SupportEnvelopeError("support envelope requires successful intervention transitions")
        item = {
            "transition_id": transition_id, "split": "training", "learner_eligible": True,
            "verification": facts.verifier,
            "mechanism_family": facts.mechanism_family,
            "compatibility_profile": facts.compatibility_profile,
            "structural_graph_digest": facts.source_state.get("context_graph_digest"),
            # One transition contributes one observed prior action. A nested
            # list would not replay against shift._values(), which treats a
            # current action-history sequence as a set of individual values.
            "action_history": facts.action_digest,
        }
        scoped = facts.verifier.get("scoped_execution")
        if scoped is not None:
            if not isinstance(scoped, Mapping) or scoped.get("role") != "after":
                raise SupportEnvelopeError("support envelope scoped source is not a treatment")
            try:
                # The envelope describes the pre-action state where the
                # historical intervention was supported, not its successful
                # target state after applying the action.
                project = Path(scoped["before_project"]).resolve()
                config = parse_config_mk((project / "constraints/config.mk").read_text())
                rtl_hashes = tuple(hashlib.sha256(Path(value).read_bytes()).hexdigest()
                                   for value in config["VERILOG_FILES"].split())
                sdc_hash = hashlib.sha256(Path(config["SDC_FILE"]).read_bytes()).hexdigest()
                local_sdc_hash = hashlib.sha256(
                    (project / "constraints/constraint.sdc").read_bytes()).hexdigest()
                pair = scoped["pair_receipt"]
                contract_digest = pair["contract_digest"]
                measurement = knowledge.intervention["measurement_contract"]
            except (KeyError, OSError, TypeError, AttributeError) as exc:
                raise SupportEnvelopeError("support envelope scoped source is malformed") from exc
            if (measurement.get("contract_digest") != contract_digest
                    or measurement.get("scope") != facts.verifier.get("scope")):
                raise SupportEnvelopeError("support envelope scoped contract conflicts with Knowledge")
            item.update(
                structural_signature={"design_name": config.get("DESIGN_NAME"),
                                      "rtl_sha256": rtl_hashes},
                flow_regime={"platform": config.get("PLATFORM"),
                             "toolchain_manifest_digest": scoped.get("expected_manifest_digest")},
                constraint_regime={"core_utilization": config.get("CORE_UTILIZATION"),
                                   "sdc_sha256": sdc_hash,
                                   "wrapper_sdc_sha256": local_sdc_hash},
                oracle_regime={"scope": measurement["scope"],
                               "contract_digest": contract_digest})
            item.pop("structural_graph_digest", None)
        records.append(item)
    return build_support_envelope(knowledge, (), records)


__all__ = [
    "SUPPORT_ENVELOPE_VERSION", "SupportEnvelopeError", "SupportEnvelope",
    "build_support_envelope", "build_support_envelope_from_transitions",
]
