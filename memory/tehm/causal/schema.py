"""Typed constants shared by the causal shadow store."""
from __future__ import annotations

from .evidence_level import EVIDENCE_LEVELS

CAUSAL_NODE_TYPES = frozenset({
    "STATE_CONDITION", "ACTION", "INTERMEDIATE_EFFECT", "FAILURE_MECHANISM",
    "ORACLE_OUTCOME", "OBLIGATION", "REGRESSION", "PHYSICAL_EFFECT",
    "ASSET", "CAPABILITY",
})
CAUSAL_RELATIONS = frozenset({
    "ENABLES", "BLOCKS", "INTERVENES_ON", "CHANGES", "MEDIATES", "REMOVES",
    "CREATES", "PRESERVES", "CONTRADICTS", "SUPPORTS", "SPECIALIZES",
    "GENERALIZES",
})
CAUSAL_PATH_STATUSES = frozenset({"shadow", "candidate", "validated", "retired"})


def validate_node_type(node_type: str) -> str:
    value = str(node_type)
    if value not in CAUSAL_NODE_TYPES:
        raise ValueError(f"unknown causal node type: {node_type!r}")
    return value


def validate_relation(relation_type: str) -> str:
    value = str(relation_type)
    if value not in CAUSAL_RELATIONS:
        raise ValueError(f"unknown causal relation: {relation_type!r}")
    return value


def validate_path_status(status: str) -> str:
    value = str(status)
    if value not in CAUSAL_PATH_STATUSES:
        raise ValueError(f"unknown causal path status: {status!r}")
    return value


__all__ = [
    "CAUSAL_NODE_TYPES", "CAUSAL_RELATIONS", "CAUSAL_PATH_STATUSES",
    "EVIDENCE_LEVELS", "validate_node_type", "validate_relation",
    "validate_path_status",
]
