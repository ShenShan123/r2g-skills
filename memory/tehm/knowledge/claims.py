"""Typed Mechanism Knowledge claims derived from causal evidence.

The claim is a derived interpretation of immutable transitions, causal paths,
and intervention receipts.  It is intentionally separate from the evidence
rows and has no implicit production authority.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from collections.abc import Mapping

from tehm.causal.evidence_level import EVIDENCE_LEVELS, validate_evidence_level
from tehm.ids import stable_dumps


KNOWLEDGE_STATUSES = frozenset({
    "shadow", "candidate", "validated", "superseded", "invalidated", "retired",
})


def _text(value: object, field_name: str, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if type(value) is not str or not value.strip():
        raise ValueError(f"mechanism knowledge {field_name} must be a non-empty string")
    return value.strip()


def _mapping(value: object, field_name: str) -> dict:
    if not isinstance(value, Mapping):
        raise ValueError(f"mechanism knowledge {field_name} must be an object")
    # Round-trip through the canonical encoder so values cannot contain a
    # non-deterministic/non-JSON object hidden behind a Mapping interface.
    try:
        decoded = json.loads(stable_dumps(dict(value)))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"mechanism knowledge {field_name} is not JSON-serializable") from exc
    if not isinstance(decoded, dict):  # pragma: no cover - encoder guarantee
        raise ValueError(f"mechanism knowledge {field_name} must be an object")
    return decoded


def _mapping_tuple(value: object, field_name: str) -> tuple[dict, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"mechanism knowledge {field_name} must be a sequence")
    result = tuple(_mapping(item, field_name) for item in value)
    encoded = tuple(stable_dumps(item) for item in result)
    if len(set(encoded)) != len(encoded):
        raise ValueError(f"mechanism knowledge {field_name} must not contain duplicates")
    return result


def _string_tuple(value: object, field_name: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"mechanism knowledge {field_name} must be a sequence")
    result = tuple(value)
    if not allow_empty and not result:
        raise ValueError(f"mechanism knowledge {field_name} must not be empty")
    if any(type(item) is not str or not item.strip() for item in result):
        raise ValueError(
            f"mechanism knowledge {field_name} must contain non-empty strings")
    result = tuple(sorted(item.strip() for item in result))
    if len(set(result)) != len(result):
        raise ValueError(f"mechanism knowledge {field_name} must not contain duplicates")
    return result


@dataclass(frozen=True)
class MechanismKnowledge:
    knowledge_id: str
    version: int
    mechanism_family: str
    compatibility_profile: str | None
    antecedent: dict
    intervention: dict
    mediated_effects: tuple[dict, ...]
    expected_outcome: dict
    positive_applicability: tuple[dict, ...]
    negative_applicability: tuple[dict, ...]
    preserved_obligations: tuple[str, ...]
    known_failure_modes: tuple[str, ...]
    causal_path_ids: tuple[str, ...]
    evidence_level: str
    support_lineages: tuple[str, ...]
    status: str = "shadow"

    def __post_init__(self) -> None:
        _text(self.knowledge_id, "knowledge_id")
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version < 1:
            raise ValueError("mechanism knowledge version must be a positive integer")
        _text(self.mechanism_family, "mechanism_family")
        _text(self.compatibility_profile, "compatibility_profile", allow_none=True)
        _mapping(self.antecedent, "antecedent")
        _mapping(self.intervention, "intervention")
        _mapping_tuple(self.mediated_effects, "mediated_effects")
        _mapping(self.expected_outcome, "expected_outcome")
        _mapping_tuple(self.positive_applicability, "positive_applicability")
        _mapping_tuple(self.negative_applicability, "negative_applicability")
        _string_tuple(self.preserved_obligations, "preserved_obligations")
        _string_tuple(self.known_failure_modes, "known_failure_modes")
        _string_tuple(self.causal_path_ids, "causal_path_ids", allow_empty=False)
        validate_evidence_level(self.evidence_level)
        _string_tuple(self.support_lineages, "support_lineages")
        if self.status not in KNOWLEDGE_STATUSES:
            raise ValueError(f"invalid mechanism knowledge status: {self.status!r}")

    def content(self) -> dict:
        """Return status-independent semantic content for hashing/replay."""
        return {
            "knowledge_id": self.knowledge_id,
            "version": self.version,
            "mechanism_family": self.mechanism_family,
            "compatibility_profile": self.compatibility_profile,
            "antecedent": _mapping(self.antecedent, "antecedent"),
            "intervention": _mapping(self.intervention, "intervention"),
            "mediated_effects": list(_mapping_tuple(
                self.mediated_effects, "mediated_effects")),
            "expected_outcome": _mapping(self.expected_outcome, "expected_outcome"),
            "positive_applicability": list(_mapping_tuple(
                self.positive_applicability, "positive_applicability")),
            "negative_applicability": list(_mapping_tuple(
                self.negative_applicability, "negative_applicability")),
            "preserved_obligations": list(_string_tuple(
                self.preserved_obligations, "preserved_obligations")),
            "known_failure_modes": list(_string_tuple(
                self.known_failure_modes, "known_failure_modes")),
            "causal_path_ids": list(_string_tuple(
                self.causal_path_ids, "causal_path_ids", allow_empty=False)),
            "evidence_level": validate_evidence_level(self.evidence_level),
            "support_lineages": list(_string_tuple(
                self.support_lineages, "support_lineages")),
        }

    @property
    def content_digest(self) -> str:
        return "sha256:" + hashlib.sha256(
            stable_dumps(self.content()).encode()).hexdigest()

    @property
    def object_id(self) -> str:
        return f"{self.knowledge_id}@{self.version}"

    def to_dict(self) -> dict:
        return {**self.content(), "status": self.status,
                "content_digest": self.content_digest}

    @classmethod
    def from_dict(cls, payload: object) -> "MechanismKnowledge":
        if not isinstance(payload, Mapping):
            raise ValueError("mechanism knowledge claim must be an object")
        required = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        if any(key not in payload for key in required):
            raise ValueError("mechanism knowledge claim is missing required fields")
        claim = cls(
            knowledge_id=payload["knowledge_id"], version=payload["version"],
            mechanism_family=payload["mechanism_family"],
            compatibility_profile=payload["compatibility_profile"],
            antecedent=payload["antecedent"], intervention=payload["intervention"],
            mediated_effects=tuple(payload["mediated_effects"]),
            expected_outcome=payload["expected_outcome"],
            positive_applicability=tuple(payload["positive_applicability"]),
            negative_applicability=tuple(payload["negative_applicability"]),
            preserved_obligations=tuple(payload["preserved_obligations"]),
            known_failure_modes=tuple(payload["known_failure_modes"]),
            causal_path_ids=tuple(payload["causal_path_ids"]),
            evidence_level=payload["evidence_level"],
            support_lineages=tuple(payload["support_lineages"]),
            status=payload["status"],
        )
        supplied = payload.get("content_digest")
        if supplied is not None and supplied != claim.content_digest:
            raise ValueError("mechanism knowledge content digest mismatch")
        return claim


def knowledge_identity(*, mechanism_family: str,
                       compatibility_profile: str | None,
                       intervention: Mapping,
                       positive_applicability: tuple[Mapping, ...] | list[Mapping]) -> str:
    """Derive a stable claim identity independent of version/status."""
    payload = {
        "mechanism_family": _text(mechanism_family, "mechanism_family"),
        "compatibility_profile": _text(
            compatibility_profile, "compatibility_profile", allow_none=True),
        "intervention": _mapping(intervention, "intervention"),
        "positive_applicability": list(_mapping_tuple(
            positive_applicability, "positive_applicability")),
    }
    digest = hashlib.sha1(stable_dumps(payload).encode()).hexdigest()[:20]
    return "mk_" + digest


__all__ = ["EVIDENCE_LEVELS", "KNOWLEDGE_STATUSES", "MechanismKnowledge",
           "knowledge_identity"]
