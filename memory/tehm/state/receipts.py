"""Typed receipts emitted by the state-relation/resolution shadow lane."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tehm.ids import stable_dumps


RELATION_AUTHORITY_VERSION = "relation-authority-v1"
_APPROVED_EFFECTS = frozenset({
    "suppress_target", "retire_source", "replace_source",
})


def _relation_authority_digest(payload: Mapping) -> str:
    return "sha256:" + hashlib.sha256(
        stable_dumps(dict(payload)).encode()).hexdigest()


@dataclass(frozen=True)
class RelationAuthorityReceipt:
    """Replayable authority decision for one state-affecting relation.

    The relation table remains an immutable evidence edge.  This receipt is a
    separate typed boundary documenting whether an authority decision permits
    the edge to affect a requested scope; it never mutates lifecycle rows.
    """

    relation_id: str
    authority_type: str
    eligible: bool
    evidence_refs: tuple[str, ...]
    replay_digest: str
    scope: dict
    approved_effect: str | None = None
    version: str = RELATION_AUTHORITY_VERSION

    def __post_init__(self) -> None:
        if type(self.relation_id) is not str or not self.relation_id:
            raise ValueError("relation authority relation_id is required")
        if type(self.authority_type) is not str or not self.authority_type:
            raise ValueError("relation authority authority_type is required")
        if type(self.eligible) is not bool:
            raise ValueError("relation authority eligible must be boolean")
        if not isinstance(self.evidence_refs, tuple) or any(
                type(item) is not str or not item for item in self.evidence_refs):
            raise ValueError("relation authority evidence_refs are invalid")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("relation authority evidence_refs contain duplicates")
        object.__setattr__(self, "evidence_refs", tuple(sorted(self.evidence_refs)))
        if type(self.replay_digest) is not str or not self.replay_digest.startswith("sha256:"):
            raise ValueError("relation authority replay_digest must be a sha256 digest")
        if not isinstance(self.scope, dict):
            raise ValueError("relation authority scope must be an object")
        try:
            encoded = stable_dumps(self.scope)
            if not isinstance(json.loads(encoded), dict):
                raise ValueError
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("relation authority scope must be JSON-serializable") from exc
        if type(self.version) is not str or not self.version:
            raise ValueError("relation authority version is required")
        if self.approved_effect is not None and self.approved_effect not in _APPROVED_EFFECTS:
            raise ValueError("relation authority approved_effect is invalid")
        if self.eligible and self.approved_effect is None:
            raise ValueError(
                "eligible relation authority requires an approved_effect")
        if self.replay_digest != _relation_authority_digest(self._payload()):
            raise ValueError("relation authority replay digest mismatch")

    def _payload(self) -> dict:
        return {
            "version": self.version,
            "relation_id": self.relation_id,
            "authority_type": self.authority_type,
            "eligible": self.eligible,
            "evidence_refs": list(self.evidence_refs),
            "scope": dict(self.scope),
            "approved_effect": self.approved_effect,
        }

    @property
    def receipt_digest(self) -> str:
        """Alias the replay digest for receipt APIs that use that vocabulary."""
        return self.replay_digest

    @property
    def receipt_id(self) -> str:
        return "relation_authority_" + self.replay_digest.split(":", 1)[1][:24]

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._payload(),
            "replay_digest": self.replay_digest,
            "receipt_digest": self.receipt_digest,
            "receipt_id": self.receipt_id,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "RelationAuthorityReceipt":
        if not isinstance(payload, Mapping):
            raise ValueError("relation authority receipt must be an object")
        required = {
            "relation_id", "authority_type", "eligible", "evidence_refs",
            "replay_digest", "scope", "approved_effect",
        }
        if not required <= set(payload):
            raise ValueError("relation authority receipt is missing fields")
        refs = payload["evidence_refs"]
        if not isinstance(refs, (list, tuple)) or isinstance(refs, (str, bytes)):
            raise ValueError("relation authority evidence_refs must be a sequence")
        scope = payload["scope"]
        if not isinstance(scope, Mapping):
            raise ValueError("relation authority scope must be an object")
        receipt = cls(
            relation_id=payload["relation_id"],
            authority_type=payload["authority_type"],
            eligible=payload["eligible"], evidence_refs=tuple(refs),
            replay_digest=payload["replay_digest"], scope=dict(scope),
            approved_effect=payload["approved_effect"],
            version=payload.get("version", RELATION_AUTHORITY_VERSION),
        )
        supplied_digest = payload.get("receipt_digest")
        if supplied_digest is not None and supplied_digest != receipt.receipt_digest:
            raise ValueError("relation authority receipt digest mismatch")
        supplied_id = payload.get("receipt_id")
        if supplied_id is not None and supplied_id != receipt.receipt_id:
            raise ValueError("relation authority receipt ID mismatch")
        return receipt


@dataclass(frozen=True)
class MemoryRelationReceipt:
    relation_id: str
    relation_type: str
    source_type: str
    source_id: str
    target_type: str
    target_id: str
    relation_digest: str
    shadow_only: bool

    def to_dict(self) -> dict:
        return {
            "relation_id": self.relation_id,
            "relation_type": self.relation_type,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "relation_digest": self.relation_digest,
            "shadow_only": self.shadow_only,
        }


@dataclass(frozen=True)
class SuppressionReceipt:
    object_type: str
    object_id: str
    reason: str
    relation_id: str | None
    replacement_id: str | None

    def to_dict(self) -> dict:
        return {
            "object_type": self.object_type,
            "object_id": self.object_id,
            "reason": self.reason,
            "relation_id": self.relation_id,
            "replacement_id": self.replacement_id,
        }


@dataclass(frozen=True)
class ResolvedMemoryState:
    resolution_id: str
    input_memory_digest: str
    scope: dict
    active_rules: tuple[str, ...]
    active_causal_paths: tuple[str, ...]
    active_knowledge_claims: tuple[str, ...]
    active_assets: tuple[str, ...]
    active_capabilities: tuple[str, ...]
    suppressed: tuple[SuppressionReceipt, ...]
    unresolved_conflicts: tuple[str, ...]
    relation_ids: tuple[str, ...]
    shadow_relation_ids: tuple[str, ...]
    resolution_digest: str
    resolver_version: str

    def to_dict(self) -> dict:
        return {
            "resolution_id": self.resolution_id,
            "input_memory_digest": self.input_memory_digest,
            "scope": self.scope,
            "active_rules": list(self.active_rules),
            "active_causal_paths": list(self.active_causal_paths),
            "active_knowledge_claims": list(self.active_knowledge_claims),
            "active_assets": list(self.active_assets),
            "active_capabilities": list(self.active_capabilities),
            "suppressed": [item.to_dict() for item in self.suppressed],
            "unresolved_conflicts": list(self.unresolved_conflicts),
            "relation_ids": list(self.relation_ids),
            "shadow_relation_ids": list(self.shadow_relation_ids),
            "resolution_digest": self.resolution_digest,
            "resolver_version": self.resolver_version,
        }


@dataclass(frozen=True)
class StateResolutionReceipt:
    resolution_id: str
    input_memory_digest: str
    resolution_digest: str
    relation_count: int
    unresolved_conflicts: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolution_id": self.resolution_id,
            "input_memory_digest": self.input_memory_digest,
            "resolution_digest": self.resolution_digest,
            "relation_count": self.relation_count,
            "unresolved_conflicts": list(self.unresolved_conflicts),
        }


__all__ = [
    "RELATION_AUTHORITY_VERSION", "MemoryRelationReceipt",
    "RelationAuthorityReceipt", "ResolvedMemoryState",
    "StateResolutionReceipt",
    "SuppressionReceipt",
]
