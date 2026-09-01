"""Deterministic failure attribution for the online shadow lane.

Attribution explains which derived layer should be inspected after a failed
memory-guided action.  It is an auditable hypothesis, not a lifecycle update;
canonical transitions and activation rows are read-only inputs.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass, field
from collections.abc import Mapping

from tehm.canonical.transition import HARMFUL_OUTCOMES
from tehm.causal.mechanism import load_transition_facts
from tehm.ids import stable_dumps

from .retrieval_attribution import RetrievalAttributionReceipt


FAILURE_TYPES = frozenset({
    "NO_FAILURE", "STATE_RESOLUTION_FAILURE", "RETRIEVAL_FAILURE",
    "APPLICABILITY_FAILURE", "CAUSAL_MODEL_FAILURE", "BINDING_FAILURE",
    "ASSET_EXECUTION_FAILURE", "VERIFICATION_FAILURE", "AUTHORITY_FAILURE",
    "MEMORY_INTERFERENCE", "CAPABILITY_REGRESSION",
})

UPDATE_TARGETS = frozenset({
    "UPDATE_NONE", "UPDATE_STATE_RELATION", "UPDATE_CAUSAL_KNOWLEDGE",
    "UPDATE_RULE", "UPDATE_ASSET", "UPDATE_CAPABILITY",
})


def _unit(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"failure attribution {field_name} must be numeric")
    value = float(value)
    if not math.isfinite(value) or value < 0.0 or value > 1.0:
        raise ValueError(f"failure attribution {field_name} must be in [0, 1]")
    return value


def _strings(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"failure attribution {field_name} must be a sequence")
    values = tuple(value)
    if any(type(item) is not str or not item.strip() for item in values):
        raise ValueError(
            f"failure attribution {field_name} must contain non-empty strings")
    values = tuple(sorted(item.strip() for item in values))
    if len(set(values)) != len(values):
        raise ValueError(f"failure attribution {field_name} must not contain duplicates")
    return values


def _ordered_strings(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"failure attribution {field_name} must be a sequence")
    values = tuple(item.strip() if isinstance(item, str) else item for item in value)
    if any(type(item) is not str or not item for item in values):
        raise ValueError(
            f"failure attribution {field_name} must contain non-empty strings")
    if len(set(values)) != len(values):
        raise ValueError(f"failure attribution {field_name} must not contain duplicates")
    return values


@dataclass(frozen=True)
class MemoryFailureAttributionReceipt:
    activation_id: str | None
    transition_id: str | None
    failure_type: str
    blamed_objects: tuple[str, ...] = field(default_factory=tuple)
    excluded_causes: tuple[str, ...] = field(default_factory=tuple)
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 0.0
    recommended_update_layers: tuple[str, ...] = field(default_factory=lambda: ("UPDATE_NONE",))

    def __post_init__(self) -> None:
        for value, field_name in ((self.activation_id, "activation_id"),
                                  (self.transition_id, "transition_id")):
            if value is not None and (type(value) is not str or not value.strip()):
                raise ValueError(f"failure attribution {field_name} is invalid")
        if self.failure_type not in FAILURE_TYPES:
            raise ValueError(f"invalid failure attribution type: {self.failure_type!r}")
        _strings(self.blamed_objects, "blamed_objects")
        _strings(self.excluded_causes, "excluded_causes")
        _strings(self.evidence_refs, "evidence_refs")
        confidence = _unit(self.confidence, "confidence")
        layers = _ordered_strings(
            self.recommended_update_layers, "recommended_update_layers")
        if any(layer not in UPDATE_TARGETS for layer in layers):
            raise ValueError("failure attribution has an unknown update target")
        if not layers:
            raise ValueError("failure attribution requires an update target")
        if "UPDATE_NONE" in layers and len(layers) != 1:
            raise ValueError("UPDATE_NONE cannot accompany another update target")
        if self.failure_type == "NO_FAILURE" and layers != ("UPDATE_NONE",):
            raise ValueError("NO_FAILURE must select UPDATE_NONE")
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "blamed_objects", _strings(self.blamed_objects, "blamed_objects"))
        object.__setattr__(self, "excluded_causes", _strings(self.excluded_causes, "excluded_causes"))
        object.__setattr__(self, "evidence_refs", _strings(self.evidence_refs, "evidence_refs"))
        object.__setattr__(self, "recommended_update_layers", layers)

    def to_dict(self) -> dict:
        return {
            "activation_id": self.activation_id,
            "transition_id": self.transition_id,
            "failure_type": self.failure_type,
            "blamed_objects": list(self.blamed_objects),
            "excluded_causes": list(self.excluded_causes),
            "evidence_refs": list(self.evidence_refs),
            "confidence": self.confidence,
            "recommended_update_layers": list(self.recommended_update_layers),
        }

    @classmethod
    def from_dict(cls, payload: object) -> "MemoryFailureAttributionReceipt":
        if not isinstance(payload, Mapping):
            raise ValueError("failure attribution receipt must be an object")
        required = {
            "activation_id", "transition_id", "failure_type", "blamed_objects",
            "excluded_causes", "evidence_refs", "confidence",
            "recommended_update_layers",
        }
        if any(key not in payload for key in required):
            raise ValueError("failure attribution receipt is missing required fields")
        return cls(
            activation_id=payload["activation_id"],
            transition_id=payload["transition_id"],
            failure_type=payload["failure_type"],
            blamed_objects=tuple(payload["blamed_objects"]),
            excluded_causes=tuple(payload["excluded_causes"]),
            evidence_refs=tuple(payload["evidence_refs"]),
            confidence=payload["confidence"],
            recommended_update_layers=tuple(payload["recommended_update_layers"]),
        )


def failure_attribution_digest(receipt: MemoryFailureAttributionReceipt) -> str:
    if not isinstance(receipt, MemoryFailureAttributionReceipt):
        raise TypeError("failure attribution digest requires a receipt")
    return "sha256:" + hashlib.sha256(
        stable_dumps(receipt.to_dict()).encode()).hexdigest()


def _json_object(raw: object, field_name: str) -> dict:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"failure attribution {field_name} is malformed JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"failure attribution {field_name} must be an object")
    return value


def _activation(conn: sqlite3.Connection, activation_id: str) -> sqlite3.Row:
    if type(activation_id) is not str or not activation_id.strip():
        raise ValueError("failure attribution activation_id is invalid")
    if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            ("tehm_activations",)).fetchone() is None:
        raise ValueError("failure attribution activation table is unavailable")
    row = conn.execute(
        "SELECT * FROM tehm_activations WHERE activation_id=?", (activation_id,)
    ).fetchone()
    if row is None:
        raise ValueError("failure attribution activation is not found")
    return row


def attribute_failure(
    conn: sqlite3.Connection, *, transition_id: str | None = None,
    activation_id: str | None = None, value_receipt=None,
    state_resolution=None, conflict=None,
    retrieval_receipt: RetrievalAttributionReceipt | None = None,
) -> MemoryFailureAttributionReceipt:
    """Attribute one observed outcome without mutating any memory layer."""
    activation = _activation(conn, activation_id) if activation_id is not None else None
    if value_receipt is not None and not hasattr(value_receipt, "transition_id"):
        raise TypeError("failure attribution value receipt is invalid")
    if retrieval_receipt is not None and not isinstance(
            retrieval_receipt, RetrievalAttributionReceipt):
        raise TypeError("failure attribution retrieval receipt is invalid")
    if transition_id is None and activation is not None:
        transition_id = activation["produced_transition_id"]
    if transition_id is not None and (type(transition_id) is not str or not transition_id.strip()):
        raise ValueError("failure attribution transition_id is invalid")
    facts = load_transition_facts(conn, transition_id) if transition_id else None
    refs = [value for value in (transition_id, activation_id) if value]
    blamed: list[str] = []
    excluded: list[str] = []
    failure_type = "NO_FAILURE"
    confidence = 0.0

    unresolved = tuple(getattr(state_resolution, "unresolved_conflicts", ()) or ())
    if unresolved:
        failure_type = "STATE_RESOLUTION_FAILURE"
        blamed.extend(f"state:{value}" for value in unresolved)
        excluded.extend(("retrieval", "causal_model"))
        confidence = 1.0

    if value_receipt is not None:
        if (facts is not None and value_receipt.transition_id != facts.transition_id):
            raise ValueError("failure attribution value transition does not match")
        if getattr(value_receipt, "memory_interference", 0.0) >= 1.0:
            failure_type = "MEMORY_INTERFERENCE"
            blamed.extend(f"memory:{value}" for value in value_receipt.reasons
                          if value in {"MEMORY_INTERFERENCE", "PROMOTED_MEMORY_COUNTEREXAMPLE"})
            excluded.extend(("state_resolution", "verification"))
            confidence = 1.0

    if (retrieval_receipt is not None and retrieval_receipt.retrieval_failure and
            failure_type == "NO_FAILURE"):
        failure_type = "RETRIEVAL_FAILURE"
        blamed.extend(
            f"candidate:{value}" for value in retrieval_receipt.missed_candidate_ids)
        excluded.extend(("applicability", "causal_model", "binding"))
        refs.append(retrieval_receipt.receipt_digest)
        confidence = 1.0

    if activation is not None and failure_type == "NO_FAILURE":
        rule_id = str(activation["rule_id"] or "")
        if rule_id:
            blamed.append(f"rule:{rule_id}")
        applicability = str(activation["applicability_status"] or "")
        binding = str(activation["binding_status"] or "")
        executable = str(activation["executability_status"] or "")
        verifier_status = str(activation["verification_status"] or "")
        if applicability != "APPLICABLE":
            failure_type = "APPLICABILITY_FAILURE"
            excluded.extend(("binding", "asset_execution"))
            confidence = 0.9
        elif binding != "BOUND":
            failure_type = "BINDING_FAILURE"
            excluded.append("applicability")
            confidence = 0.9
        elif executable != "EXECUTABLE":
            failure_type = "ASSET_EXECUTION_FAILURE"
            confidence = 0.8
        else:
            verifier = _json_object(activation["verifier_json"], "activation verifier")
            authority = verifier.get("authority") or verifier.get("authority_status")
            if authority in {"DENY", "DENIED", "UNRESOLVED"}:
                failure_type = "AUTHORITY_FAILURE"
                confidence = 0.9
            elif verifier_status not in {"PASS", "VERIFIED"}:
                failure_type = "VERIFICATION_FAILURE"
                confidence = 1.0 if verifier_status in {"FAIL", "UNKNOWN"} else 0.8
            elif facts is not None and facts.outcome in HARMFUL_OUTCOMES:
                failure_type = "CAUSAL_MODEL_FAILURE"
                excluded.extend(("applicability", "binding", "verification"))
                confidence = 0.8

    if facts is not None and failure_type == "NO_FAILURE":
        verifier = facts.verifier
        complete = verifier.get("oracle_complete") is True
        verdict = str(verifier.get("verdict") or "UNKNOWN")
        if not complete or verdict in {"UNKNOWN", ""}:
            failure_type = "VERIFICATION_FAILURE"
            excluded.append("causal_model")
            confidence = 1.0
        elif facts.outcome in HARMFUL_OUTCOMES or verdict == "FAIL":
            if facts.delta.get("capability_regression") is True:
                failure_type = "CAPABILITY_REGRESSION"
            else:
                failure_type = "CAUSAL_MODEL_FAILURE"
            confidence = 0.8

    layer_map = {
        "NO_FAILURE": ("UPDATE_NONE",),
        "STATE_RESOLUTION_FAILURE": ("UPDATE_STATE_RELATION",),
        "RETRIEVAL_FAILURE": ("UPDATE_NONE",),
        "APPLICABILITY_FAILURE": ("UPDATE_CAUSAL_KNOWLEDGE",),
        "CAUSAL_MODEL_FAILURE": ("UPDATE_CAUSAL_KNOWLEDGE",),
        "BINDING_FAILURE": ("UPDATE_ASSET",),
        "ASSET_EXECUTION_FAILURE": ("UPDATE_ASSET",),
        "VERIFICATION_FAILURE": ("UPDATE_NONE",),
        "AUTHORITY_FAILURE": ("UPDATE_NONE",),
        "MEMORY_INTERFERENCE": ("UPDATE_STATE_RELATION", "UPDATE_CAUSAL_KNOWLEDGE", "UPDATE_ASSET"),
        "CAPABILITY_REGRESSION": ("UPDATE_CAPABILITY",),
    }
    return MemoryFailureAttributionReceipt(
        activation_id=activation_id, transition_id=transition_id,
        failure_type=failure_type,
        blamed_objects=tuple(blamed), excluded_causes=tuple(excluded),
        evidence_refs=tuple(refs), confidence=confidence,
        recommended_update_layers=layer_map[failure_type])


__all__ = [
    "FAILURE_TYPES", "UPDATE_TARGETS", "MemoryFailureAttributionReceipt",
    "attribute_failure", "failure_attribution_digest",
]
