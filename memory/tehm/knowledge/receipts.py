"""Typed receipts for Mechanism Knowledge derivation and authority."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MechanismKnowledgeReceipt:
    knowledge_id: str
    version: int
    object_id: str
    content_digest: str
    evidence_level: str
    status: str
    target_scope: str

    def to_dict(self) -> dict:
        return {
            "knowledge_id": self.knowledge_id, "version": self.version,
            "object_id": self.object_id, "content_digest": self.content_digest,
            "evidence_level": self.evidence_level, "status": self.status,
            "target_scope": self.target_scope,
        }


@dataclass(frozen=True)
class KnowledgeApplicabilityReceipt:
    object_id: str
    eligible: bool
    positive_matches: tuple[str, ...] = field(default_factory=tuple)
    negative_matches: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "object_id": self.object_id, "eligible": self.eligible,
            "positive_matches": list(self.positive_matches),
            "negative_matches": list(self.negative_matches), "reason": self.reason,
        }


@dataclass(frozen=True)
class KnowledgeResolutionReceipt:
    resolution_id: str
    scope: dict
    active_knowledge: tuple[str, ...]
    suppressed: tuple[str, ...]
    unresolved_conflicts: tuple[str, ...]
    mode: str = "shadow"

    def to_dict(self) -> dict:
        return {
            "resolution_id": self.resolution_id, "scope": self.scope,
            "active_knowledge": list(self.active_knowledge),
            "suppressed": list(self.suppressed),
            "unresolved_conflicts": list(self.unresolved_conflicts),
            "mode": self.mode,
        }


@dataclass(frozen=True)
class KnowledgeAuthorityReceipt:
    object_id: str
    eligible: bool
    evidence_level: str
    required_evidence_level: str
    support_lineages: tuple[str, ...]
    gates: dict
    reason: str
    # The pure evaluator predates the database-bound authority seam.  These
    # fields are optional for compatibility, but a receipt consumed by
    # lifecycle must carry all of them and be present in the authority ledger.
    authority_version: str = "knowledge-authority-v1"
    knowledge_content_digest: str = ""
    target_scope: str = "global"
    status_version: int | None = None
    min_support_lineages: int = 2
    evidence_refs: tuple[dict, ...] = field(default_factory=tuple)
    authority_receipt_id: str = ""
    receipt_digest: str = ""

    def to_dict(self) -> dict:
        return {
            "object_id": self.object_id, "eligible": self.eligible,
            "evidence_level": self.evidence_level,
            "required_evidence_level": self.required_evidence_level,
            "support_lineages": list(self.support_lineages),
            "gates": dict(self.gates), "reason": self.reason,
            "authority_version": self.authority_version,
            "knowledge_content_digest": self.knowledge_content_digest,
            "target_scope": self.target_scope,
            "status_version": self.status_version,
            "min_support_lineages": self.min_support_lineages,
            "evidence_refs": [dict(item) for item in self.evidence_refs],
            "authority_receipt_id": self.authority_receipt_id,
            "receipt_digest": self.receipt_digest,
        }


@dataclass(frozen=True)
class KnowledgeRevisionReceipt:
    parent_object_id: str
    child_object_id: str
    operation: str
    relation_id: str
    authority_ref: str | None
    shadow_only: bool
    relation_ids: tuple[str, ...] = field(default_factory=tuple)
    parent_object_ids: tuple[str, ...] = field(default_factory=tuple)
    child_object_ids: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "parent_object_id": self.parent_object_id,
            "child_object_id": self.child_object_id,
            "operation": self.operation, "relation_id": self.relation_id,
            "authority_ref": self.authority_ref, "shadow_only": self.shadow_only,
            "relation_ids": list(self.relation_ids or (self.relation_id,)),
            "parent_object_ids": list(self.parent_object_ids or (self.parent_object_id,)),
            "child_object_ids": list(self.child_object_ids or (self.child_object_id,)),
        }


@dataclass(frozen=True)
class KnowledgeStructuralRevisionReceipt:
    """Receipt for a split or merge with multiple relation edges."""

    operation: str
    parent_object_ids: tuple[str, ...]
    child_object_ids: tuple[str, ...]
    relation_ids: tuple[str, ...]
    authority_ref: str | None
    shadow_only: bool

    def to_dict(self) -> dict:
        return {
            "operation": self.operation,
            "parent_object_ids": list(self.parent_object_ids),
            "child_object_ids": list(self.child_object_ids),
            "relation_ids": list(self.relation_ids),
            "authority_ref": self.authority_ref,
            "shadow_only": self.shadow_only,
        }


__all__ = [
    "KnowledgeApplicabilityReceipt", "KnowledgeAuthorityReceipt",
    "KnowledgeResolutionReceipt", "KnowledgeRevisionReceipt",
    "KnowledgeStructuralRevisionReceipt",
    "MechanismKnowledgeReceipt",
]
