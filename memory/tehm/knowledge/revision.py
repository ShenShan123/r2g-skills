"""Shadow knowledge revisions with explicit semantic relation types.

Knowledge content is immutable. A same-claim revision keeps the stable claim
identity and advances its version; structural changes receive a new identity
and are connected with SPECIALIZES/GENERALIZES edges. Structural edges do not
suppress the parent in the current-valid-state resolver.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence

from tehm.state import record_relation

from .claims import MechanismKnowledge
from .receipts import KnowledgeRevisionReceipt, KnowledgeStructuralRevisionReceipt
from .registry import get_knowledge_by_object_id, register_knowledge


REVISION_OPERATIONS = frozenset({
    "SPECIALIZE", "GENERALIZE", "REVISE", "SPLIT", "MERGE",
})
_STRUCTURAL_RELATIONS = {"SPECIALIZE": "SPECIALIZES", "GENERALIZE": "GENERALIZES"}


def _scope(parent: MechanismKnowledge, child: MechanismKnowledge,
           target_scope: str) -> dict:
    return {key: value for key, value in {
        "target_scope": target_scope,
        "mechanism_family": child.mechanism_family or parent.mechanism_family,
        "compatibility_profile": (child.compatibility_profile
                                   or parent.compatibility_profile),
    }.items() if value is not None}


def _evidence_ids(evidence_refs: Sequence[Mapping] | None,
                  fallback: Sequence[str] = ()) -> tuple[str, ...]:
    values = []
    for evidence in evidence_refs or ():
        if not isinstance(evidence, Mapping):
            raise ValueError("knowledge revision evidence reference is malformed")
        value = evidence.get("evidence_id")
        if type(value) is str and value.strip():
            values.append(value.strip())
    if not values:
        values = [value for value in fallback if type(value) is str and value]
    if not values:
        raise ValueError("knowledge revision requires non-empty evidence refs")
    return tuple(sorted(set(values)))


def _register_child(conn: sqlite3.Connection, *, child: MechanismKnowledge,
                    target_scope: str, evidence_refs: Sequence[Mapping] | None,
                    provenance: Mapping | None) -> None:
    if child.status not in {"shadow", "candidate"}:
        raise ValueError("knowledge revision cannot grant validated/production status")
    register_knowledge(conn, child, target_scope=target_scope,
                       provenance=provenance, evidence_refs=evidence_refs,
                       commit=False)


def revise_knowledge(
    conn: sqlite3.Connection, *, parent_object_id: str,
    replacement: MechanismKnowledge, operation: str = "REVISE",
    target_scope: str = "global", authority_ref: str | None = None,
    evidence_refs: Sequence[Mapping] | None = None,
    provenance: Mapping | None = None, commit: bool = True,
) -> KnowledgeRevisionReceipt:
    """Apply one same-claim or structural revision in the shadow lane.

    REVISE is the only operation that may preserve ``knowledge_id`` and thus
    the only operation that uses SUPERSEDES.
    """
    if operation not in REVISION_OPERATIONS:
        raise ValueError(f"invalid mechanism knowledge revision operation: {operation!r}")
    if authority_ref is not None:
        raise ValueError("knowledge revisions cannot bind production authority")
    if not isinstance(replacement, MechanismKnowledge):
        raise TypeError("knowledge revision replacement must be MechanismKnowledge")
    parent = get_knowledge_by_object_id(conn, parent_object_id,
                                        target_scope=target_scope)
    if operation == "REVISE":
        if replacement.knowledge_id != parent.knowledge_id:
            raise ValueError("same-claim REVISE must preserve claim identity")
        if replacement.version != parent.version + 1:
            raise ValueError("same-claim REVISE version must increment by one")
        relation_type = "SUPERSEDES"
    elif operation in _STRUCTURAL_RELATIONS:
        if replacement.knowledge_id == parent.knowledge_id:
            raise ValueError(f"{operation} must create a new knowledge identity")
        if replacement.version != 1:
            raise ValueError(f"{operation} child version must start at one")
        relation_type = _STRUCTURAL_RELATIONS[operation]
    else:
        raise ValueError(f"{operation} requires split_knowledge or merge_knowledge API")
    had_outer_transaction = conn.in_transaction
    _register_child(conn, child=replacement, target_scope=target_scope,
                    evidence_refs=evidence_refs, provenance=provenance)
    refs = _evidence_ids(evidence_refs, replacement.causal_path_ids)
    relation = record_relation(
        conn, source_type="knowledge", source_id=replacement.object_id,
        relation_type=relation_type, target_type="knowledge",
        target_id=parent.object_id, scope=_scope(parent, replacement, target_scope),
        evidence_refs=refs, authority_ref=None, commit=False)
    if commit and not had_outer_transaction:
        conn.commit()
    return KnowledgeRevisionReceipt(
        parent_object_id=parent.object_id, child_object_id=replacement.object_id,
        operation=operation, relation_id=relation.relation_id,
        authority_ref=None, shadow_only=True, relation_ids=(relation.relation_id,),
        parent_object_ids=(parent.object_id,), child_object_ids=(replacement.object_id,))


def replace_knowledge(conn: sqlite3.Connection, *, parent_object_id: str,
                      replacement: MechanismKnowledge, **kwargs) -> KnowledgeRevisionReceipt:
    """Named API for an explicit same-claim replacement."""
    return revise_knowledge(conn, parent_object_id=parent_object_id,
                            replacement=replacement, operation="REVISE", **kwargs)


def _partition_refs(partition_evidence: Mapping[str, Sequence[str]] | None,
                    child: MechanismKnowledge,
                    fallback: Sequence[Mapping] | None) -> tuple[str, ...]:
    if partition_evidence is not None:
        raw = partition_evidence.get(child.object_id)
        if not isinstance(raw, (list, tuple)) or not raw:
            raise ValueError("split partition witness must cover every child object_id")
        if any(type(item) is not str or not item.strip() for item in raw):
            raise ValueError("split partition witness refs are invalid")
        return tuple(sorted(set(item.strip() for item in raw)))
    return _evidence_ids(fallback, child.causal_path_ids)


def split_knowledge(
    conn: sqlite3.Connection, *, parent_object_id: str,
    children: Sequence[MechanismKnowledge], target_scope: str = "global",
    partition_evidence: Mapping[str, Sequence[str]] | None = None,
    evidence_refs: Sequence[Mapping] | None = None,
    provenance: Mapping | None = None, authority_ref: str | None = None,
    commit: bool = True,
) -> KnowledgeStructuralRevisionReceipt:
    """Register multiple identity-changing SPECIALIZES children."""
    if authority_ref is not None:
        raise ValueError("knowledge revisions cannot bind production authority")
    if not isinstance(children, (list, tuple)) or len(children) < 2:
        raise ValueError("knowledge split requires at least two children")
    parent = get_knowledge_by_object_id(conn, parent_object_id,
                                        target_scope=target_scope)
    if any(not isinstance(child, MechanismKnowledge) for child in children):
        raise TypeError("knowledge split children must be MechanismKnowledge")
    child_ids = [child.object_id for child in children]
    if len(set(child_ids)) != len(child_ids) or any(
            child.knowledge_id == parent.knowledge_id or child.version != 1
            for child in children):
        raise ValueError("knowledge split children must be new identity version one")
    had_outer_transaction = conn.in_transaction
    relation_ids = []
    for child in children:
        refs = _partition_refs(partition_evidence, child, evidence_refs)
        _register_child(conn, child=child, target_scope=target_scope,
                        evidence_refs=evidence_refs, provenance=provenance)
        relation = record_relation(
            conn, source_type="knowledge", source_id=child.object_id,
            relation_type="SPECIALIZES", target_type="knowledge",
            target_id=parent.object_id, scope=_scope(parent, child, target_scope),
            evidence_refs=refs, authority_ref=None, commit=False)
        relation_ids.append(relation.relation_id)
    if commit and not had_outer_transaction:
        conn.commit()
    return KnowledgeStructuralRevisionReceipt(
        operation="SPLIT", parent_object_ids=(parent.object_id,),
        child_object_ids=tuple(child_ids), relation_ids=tuple(relation_ids),
        authority_ref=None, shadow_only=True)


def merge_knowledge(
    conn: sqlite3.Connection, *, parent_object_ids: Sequence[str],
    replacement: MechanismKnowledge, target_scope: str = "global",
    merge_witness: Mapping[str, Sequence[str]] | None = None,
    evidence_refs: Sequence[Mapping] | None = None,
    provenance: Mapping | None = None, authority_ref: str | None = None,
    commit: bool = True,
) -> KnowledgeStructuralRevisionReceipt:
    """Register one identity-changing GENERALIZES child with multi-parent proof."""
    if authority_ref is not None:
        raise ValueError("knowledge revisions cannot bind production authority")
    if not isinstance(parent_object_ids, (list, tuple)) or len(parent_object_ids) < 2:
        raise ValueError("knowledge merge requires at least two parents")
    if not isinstance(replacement, MechanismKnowledge):
        raise TypeError("knowledge merge replacement must be MechanismKnowledge")
    parents = tuple(get_knowledge_by_object_id(
        conn, object_id, target_scope=target_scope) for object_id in parent_object_ids)
    if replacement.version != 1 or any(
            replacement.knowledge_id == parent.knowledge_id for parent in parents):
        raise ValueError("knowledge merge replacement must be a new identity version one")
    if len(set(parent.object_id for parent in parents)) != len(parents):
        raise ValueError("knowledge merge parents must be distinct")
    if merge_witness is None:
        raise ValueError("knowledge merge requires a witness for every parent")
    had_outer_transaction = conn.in_transaction
    _register_child(conn, child=replacement, target_scope=target_scope,
                    evidence_refs=evidence_refs, provenance=provenance)
    relation_ids = []
    for parent in parents:
        raw = merge_witness.get(parent.object_id)
        if not isinstance(raw, (list, tuple)) or not raw or any(
                type(item) is not str or not item.strip() for item in raw):
            raise ValueError("knowledge merge witness must cover every parent")
        relation = record_relation(
            conn, source_type="knowledge", source_id=replacement.object_id,
            relation_type="GENERALIZES", target_type="knowledge",
            target_id=parent.object_id, scope=_scope(parent, replacement, target_scope),
            evidence_refs=tuple(sorted(set(item.strip() for item in raw))),
            authority_ref=None, commit=False)
        relation_ids.append(relation.relation_id)
    if commit and not had_outer_transaction:
        conn.commit()
    return KnowledgeStructuralRevisionReceipt(
        operation="MERGE", parent_object_ids=tuple(parent.object_id for parent in parents),
        child_object_ids=(replacement.object_id,), relation_ids=tuple(relation_ids),
        authority_ref=None, shadow_only=True)


__all__ = [
    "REVISION_OPERATIONS", "merge_knowledge", "replace_knowledge",
    "revise_knowledge", "split_knowledge",
]
