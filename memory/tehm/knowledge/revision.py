"""Shadow knowledge revisions expressed as state relations."""
from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence

from tehm.state import record_relation

from .claims import MechanismKnowledge
from .receipts import KnowledgeRevisionReceipt
from .registry import get_knowledge_by_object_id, register_knowledge


REVISION_OPERATIONS = frozenset({
    "SPECIALIZE", "GENERALIZE", "REVISE", "SPLIT", "MERGE",
})


def revise_knowledge(
    conn: sqlite3.Connection, *, parent_object_id: str,
    replacement: MechanismKnowledge, operation: str = "REVISE",
    target_scope: str = "global", authority_ref: str | None = None,
    evidence_refs: Sequence[Mapping] | None = None,
    provenance: Mapping | None = None, commit: bool = True,
) -> KnowledgeRevisionReceipt:
    if operation not in REVISION_OPERATIONS:
        raise ValueError(f"invalid mechanism knowledge revision operation: {operation!r}")
    if authority_ref is not None:
        raise ValueError("knowledge revisions cannot bind production authority")
    if not isinstance(replacement, MechanismKnowledge):
        raise TypeError("knowledge revision replacement must be MechanismKnowledge")
    parent = get_knowledge_by_object_id(
        conn, parent_object_id, target_scope=target_scope)
    had_outer_transaction = conn.in_transaction
    if replacement.knowledge_id != parent.knowledge_id:
        raise ValueError("knowledge revision must preserve claim identity")
    if replacement.version != parent.version + 1:
        raise ValueError("knowledge revision version must increment by one")
    if replacement.status not in {"shadow", "candidate"}:
        raise ValueError("knowledge revision cannot grant validated/production status")
    register_knowledge(
        conn, replacement, target_scope=target_scope, provenance=provenance,
        evidence_refs=evidence_refs, commit=False)
    relation = record_relation(
        conn, source_type="knowledge", source_id=replacement.object_id,
        relation_type="SUPERSEDES", target_type="knowledge",
        target_id=parent.object_id,
        scope={key: value for key, value in {
            "target_scope": target_scope,
            "mechanism_family": replacement.mechanism_family,
            "compatibility_profile": replacement.compatibility_profile,
        }.items() if value is not None},
        evidence_refs=tuple(evidence["evidence_id"] for evidence in (
            evidence_refs or ({"evidence_id": value}
                              for value in replacement.causal_path_ids))
            if isinstance(evidence, Mapping) and evidence.get("evidence_id")),
        authority_ref=authority_ref, commit=False)
    if commit and not had_outer_transaction:
        conn.commit()
    return KnowledgeRevisionReceipt(
        parent_object_id=parent.object_id, child_object_id=replacement.object_id,
        operation=operation, relation_id=relation.relation_id,
        authority_ref=authority_ref, shadow_only=authority_ref is None)


__all__ = ["REVISION_OPERATIONS", "revise_knowledge"]
