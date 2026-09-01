"""Shadow-only current-valid-state relations and resolver (P1)."""
from .relations import MemoryRelation, get_relation, load_relations, record_relation
from .resolver import (
    RESOLVER_VERSION, StateResolutionError, load_resolution_snapshot,
    resolve_current_state, resolve_state, verify_resolution_snapshot,
)
from .receipts import (
    MemoryRelationReceipt, RelationAuthorityReceipt, ResolvedMemoryState,
    StateResolutionReceipt, SuppressionReceipt,
)
from .schema import STATE_SCHEMA_VERSION, ensure_state_schema

__all__ = [
    "MemoryRelation", "MemoryRelationReceipt", "RelationAuthorityReceipt",
    "ResolvedMemoryState",
    "RESOLVER_VERSION", "STATE_SCHEMA_VERSION", "StateResolutionError",
    "StateResolutionReceipt", "SuppressionReceipt", "ensure_state_schema",
    "get_relation", "load_relations", "load_resolution_snapshot",
    "record_relation", "resolve_current_state", "resolve_state",
    "verify_resolution_snapshot",
]
