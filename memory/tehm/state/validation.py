"""Fail-closed validation shared by state relations and snapshots."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

from tehm.ids import stable_dumps


RELATION_TYPES = frozenset({
    "DERIVED_FROM", "DEPENDS_ON", "SPECIALIZES", "GENERALIZES",
    "SUPERSEDES", "INVALIDATES", "CONTRADICTS", "RETIRES",
    "REPLACED_BY", "SUPPORTED_BY", "REFUTED_BY",
})
OBJECT_TYPES = frozenset({
    "state", "transition", "episode", "rule", "causal_path", "knowledge",
    "asset", "capability", "activation", "relation",
})
RELATION_SCHEMA_FIELDS = (
    "source_type", "source_id", "relation_type", "target_type", "target_id",
    "scope", "evidence_refs", "authority_ref",
)


def _text(value, field: str, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if type(value) is not str or not value.strip():
        raise ValueError(f"state relation {field} must be a non-empty string")
    return value.strip()


def validate_object_type(value: str, field: str = "object_type") -> str:
    value = _text(value, field)
    if value not in OBJECT_TYPES:
        raise ValueError(f"unknown state relation {field}: {value!r}")
    return value


def validate_relation_type(value: str) -> str:
    value = _text(value, "relation_type")
    if value not in RELATION_TYPES:
        raise ValueError(f"unknown state relation type: {value!r}")
    return value


def normalize_scope(value: Mapping | None) -> dict:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("state relation scope must be a mapping")
    result = dict(value)
    for key in result:
        if type(key) is not str or not key.strip():
            raise ValueError("state relation scope keys must be non-empty strings")
    try:
        encoded = stable_dumps(result)
        decoded = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("state relation scope must be JSON-serializable") from exc
    if not isinstance(decoded, dict):  # pragma: no cover - stable_dumps guarantee
        raise ValueError("state relation scope must decode to an object")
    return decoded


def normalize_evidence_refs(value: Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("state relation evidence_refs must be a non-empty sequence")
    refs = tuple(value)
    if not refs:
        raise ValueError("state relation evidence_refs must be non-empty")
    if any(type(ref) is not str or not ref.strip() for ref in refs):
        raise ValueError("state relation evidence_refs must contain non-empty strings")
    if len(set(refs)) != len(refs):
        raise ValueError("state relation evidence_refs must not contain duplicates")
    return tuple(sorted(ref.strip() for ref in refs))


def relation_content(*, source_type: str, source_id: str, relation_type: str,
                     target_type: str, target_id: str, scope: Mapping,
                     evidence_refs: Sequence[str], authority_ref: str | None) -> dict:
    return {
        "source_type": validate_object_type(source_type, "source_type"),
        "source_id": _text(source_id, "source_id"),
        "relation_type": validate_relation_type(relation_type),
        "target_type": validate_object_type(target_type, "target_type"),
        "target_id": _text(target_id, "target_id"),
        "scope": normalize_scope(scope),
        "evidence_refs": list(normalize_evidence_refs(evidence_refs)),
        "authority_ref": _text(authority_ref, "authority_ref", allow_none=True),
    }


def relation_digest(content: Mapping) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(dict(content)).encode()).hexdigest()


def parse_json_object(raw, field: str) -> dict:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"state resolution {field} is malformed JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"state resolution {field} must decode to an object")
    return value


def parse_json_array(raw, field: str) -> list:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"state resolution {field} is malformed JSON") from exc
    if not isinstance(value, list):
        raise ValueError(f"state resolution {field} must decode to an array")
    return value


__all__ = [
    "OBJECT_TYPES", "RELATION_TYPES", "RELATION_SCHEMA_FIELDS",
    "normalize_scope", "normalize_evidence_refs", "parse_json_array",
    "parse_json_object", "relation_content", "relation_digest",
    "validate_object_type", "validate_relation_type",
]
