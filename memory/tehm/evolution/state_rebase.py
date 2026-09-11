"""Typed rebasing of an admitted plan onto a deterministic source replay.

Historical state-resolution IDs include the exact SQLite materialization.
Rebuilding the same semantic Memory from frozen evidence can therefore yield a
different resolution ID.  This module never treats that mismatch as equality:
it emits an explicit, content-addressed receipt and derives a new shadow-only
plan whose before-state is the replayed source state.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from tehm.ids import stable_dumps
from tehm.state.receipts import ResolvedMemoryState

from .local_revision import LocalizedUpdatePlan


STATE_RESOLUTION_REBASE_VERSION = "state-resolution-rebase-v0.1"
STATE_RESOLUTION_REBASE_REASON = "DETERMINISTIC_SOURCE_REPLAY"


class StateResolutionRebaseError(ValueError):
    """An admitted plan cannot be bound to the reconstructed source state."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _sha(value: object, name: str) -> str:
    if type(value) is not str or not value.startswith("sha256:") or len(value) != 71:
        raise StateResolutionRebaseError(f"state rebase {name} must be sha256")
    return value


def _strings(value: Sequence[str], name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise StateResolutionRebaseError(
            f"state rebase {name} must be a sequence")
    result = tuple(sorted(value))
    if (not result or len(set(result)) != len(result) or
            any(type(item) is not str or not item for item in result)):
        raise StateResolutionRebaseError(
            f"state rebase {name} must contain unique non-empty strings")
    return result


def state_semantic_payload(state: ResolvedMemoryState) -> dict:
    """Return state semantics without materialization-dependent identities."""
    if not isinstance(state, ResolvedMemoryState):
        raise TypeError("state rebase requires ResolvedMemoryState")
    return {
        "scope": dict(state.scope),
        "active_rules": list(state.active_rules),
        "active_causal_paths": list(state.active_causal_paths),
        "active_knowledge_claims": list(state.active_knowledge_claims),
        "active_assets": list(state.active_assets),
        "active_capabilities": list(state.active_capabilities),
        "suppressed": [item.to_dict() for item in state.suppressed],
        "unresolved_conflicts": list(state.unresolved_conflicts),
        "relation_ids": list(state.relation_ids),
        "shadow_relation_ids": list(state.shadow_relation_ids),
        "resolver_version": state.resolver_version,
    }


def state_semantic_digest(state: ResolvedMemoryState) -> str:
    return _digest(state_semantic_payload(state))


@dataclass(frozen=True)
class StateResolutionRebaseReceipt:
    admitted_plan_digest: str
    original_resolution_id: str
    source_resolution_id: str
    source_semantic_digest: str
    source_database_digest: str
    scope: dict
    evidence_refs: tuple[str, ...]
    reason: str = STATE_RESOLUTION_REBASE_REASON
    evaluation_only: bool = True
    canonical_memory_mutation: str = "none"
    production_runtime_imported: bool = False
    version: str = STATE_RESOLUTION_REBASE_VERSION

    def __post_init__(self) -> None:
        _sha(self.admitted_plan_digest, "admitted_plan_digest")
        _sha(self.source_semantic_digest, "source_semantic_digest")
        _sha(self.source_database_digest, "source_database_digest")
        for value, name in (
                (self.original_resolution_id, "original_resolution_id"),
                (self.source_resolution_id, "source_resolution_id")):
            if type(value) is not str or not value:
                raise StateResolutionRebaseError(
                    f"state rebase {name} is required")
        if self.original_resolution_id == self.source_resolution_id:
            raise StateResolutionRebaseError(
                "state rebase requires distinct resolution identities")
        if not isinstance(self.scope, dict):
            raise StateResolutionRebaseError("state rebase scope must be an object")
        refs = _strings(self.evidence_refs, "evidence_refs")
        if self.admitted_plan_digest not in refs:
            raise StateResolutionRebaseError(
                "state rebase evidence must bind the admitted plan")
        if self.reason != STATE_RESOLUTION_REBASE_REASON:
            raise StateResolutionRebaseError("state rebase reason is invalid")
        if (self.evaluation_only is not True or
                self.canonical_memory_mutation != "none" or
                self.production_runtime_imported is not False or
                self.version != STATE_RESOLUTION_REBASE_VERSION):
            raise StateResolutionRebaseError(
                "state rebase crossed a shadow authority boundary")
        object.__setattr__(self, "evidence_refs", refs)

    def _payload(self) -> dict:
        return {
            "version": self.version,
            "admitted_plan_digest": self.admitted_plan_digest,
            "original_resolution_id": self.original_resolution_id,
            "source_resolution_id": self.source_resolution_id,
            "source_semantic_digest": self.source_semantic_digest,
            "source_database_digest": self.source_database_digest,
            "scope": dict(self.scope),
            "evidence_refs": list(self.evidence_refs),
            "reason": self.reason,
            "evaluation_only": self.evaluation_only,
            "canonical_memory_mutation": self.canonical_memory_mutation,
            "production_runtime_imported": self.production_runtime_imported,
        }

    @property
    def receipt_digest(self) -> str:
        return _digest(self._payload())

    @property
    def receipt_id(self) -> str:
        return "state_resolution_rebase_" + self.receipt_digest.split(":", 1)[1][:24]

    def to_dict(self) -> dict:
        return {
            **self._payload(),
            "receipt_id": self.receipt_id,
            "receipt_digest": self.receipt_digest,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "StateResolutionRebaseReceipt":
        if not isinstance(payload, Mapping):
            raise StateResolutionRebaseError(
                "state rebase receipt must be an object")
        required = {
            "admitted_plan_digest", "original_resolution_id",
            "source_resolution_id", "source_semantic_digest",
            "source_database_digest", "scope", "evidence_refs", "reason",
            "evaluation_only", "canonical_memory_mutation",
            "production_runtime_imported",
        }
        if not required <= set(payload):
            raise StateResolutionRebaseError(
                "state rebase receipt is missing fields")
        scope = payload["scope"]
        if not isinstance(scope, Mapping):
            raise StateResolutionRebaseError("state rebase scope must be an object")
        receipt = cls(
            admitted_plan_digest=payload["admitted_plan_digest"],
            original_resolution_id=payload["original_resolution_id"],
            source_resolution_id=payload["source_resolution_id"],
            source_semantic_digest=payload["source_semantic_digest"],
            source_database_digest=payload["source_database_digest"],
            scope=dict(scope), evidence_refs=tuple(payload["evidence_refs"]),
            reason=payload["reason"],
            evaluation_only=payload["evaluation_only"],
            canonical_memory_mutation=payload["canonical_memory_mutation"],
            production_runtime_imported=payload["production_runtime_imported"],
            version=payload.get("version", STATE_RESOLUTION_REBASE_VERSION),
        )
        if payload.get("receipt_id") not in {None, receipt.receipt_id}:
            raise StateResolutionRebaseError("state rebase receipt ID mismatch")
        if payload.get("receipt_digest") not in {None, receipt.receipt_digest}:
            raise StateResolutionRebaseError("state rebase receipt digest mismatch")
        return receipt


def rebase_localized_update_plan(
    plan: LocalizedUpdatePlan,
    source_state: ResolvedMemoryState,
    *,
    source_database_digest: str,
    evidence_refs: Sequence[str],
) -> tuple[StateResolutionRebaseReceipt, LocalizedUpdatePlan]:
    """Bind an admitted plan to a semantically checked source replay."""
    if not isinstance(plan, LocalizedUpdatePlan):
        raise TypeError("state rebase requires LocalizedUpdatePlan")
    if plan.state_resolution_id is None:
        raise StateResolutionRebaseError(
            "state rebase requires an admitted plan resolution")
    if plan.state_resolution_id == source_state.resolution_id:
        raise StateResolutionRebaseError(
            "state rebase is unnecessary for an exact resolution match")
    if source_state.unresolved_conflicts:
        raise StateResolutionRebaseError(
            "state rebase source has unresolved conflicts")
    if plan.knowledge_refs and not set(plan.knowledge_refs) <= set(
            source_state.active_knowledge_claims):
        raise StateResolutionRebaseError(
            "state rebase source lacks planned Knowledge parent")
    refs = set(_strings(evidence_refs, "evidence_refs"))
    refs.add(plan.plan_digest)
    receipt = StateResolutionRebaseReceipt(
        admitted_plan_digest=plan.plan_digest,
        original_resolution_id=plan.state_resolution_id,
        source_resolution_id=source_state.resolution_id,
        source_semantic_digest=state_semantic_digest(source_state),
        source_database_digest=_sha(
            source_database_digest, "source_database_digest"),
        scope=dict(source_state.scope),
        evidence_refs=tuple(sorted(refs)),
    )
    rebased = replace(
        plan,
        state_resolution_id=source_state.resolution_id,
        evidence_refs=tuple(sorted({
            *plan.evidence_refs,
            plan.plan_digest,
            receipt.receipt_digest,
            *refs,
        })),
    )
    return receipt, rebased


__all__ = [
    "STATE_RESOLUTION_REBASE_VERSION", "STATE_RESOLUTION_REBASE_REASON",
    "StateResolutionRebaseError", "StateResolutionRebaseReceipt",
    "state_semantic_payload", "state_semantic_digest",
    "rebase_localized_update_plan",
]
