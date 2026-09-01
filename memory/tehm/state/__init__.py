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
from .shift import StateShiftError, evaluate_state_shift
from .shift_receipts import STATE_SHIFT_VERSION, SHIFT_DIMENSIONS, StateShiftReceipt
from .support_envelope import (
    SUPPORT_ENVELOPE_VERSION, SupportEnvelopeError, SupportEnvelope,
    build_support_envelope,
)

__all__ = [
    "MemoryRelation", "MemoryRelationReceipt", "RelationAuthorityReceipt",
    "ResolvedMemoryState",
    "RESOLVER_VERSION", "STATE_SCHEMA_VERSION", "StateResolutionError",
    "StateResolutionReceipt", "SuppressionReceipt", "ensure_state_schema",
    "get_relation", "load_relations", "load_resolution_snapshot",
    "record_relation", "resolve_current_state", "resolve_state",
    "verify_resolution_snapshot",
    "STATE_SHIFT_VERSION", "SHIFT_DIMENSIONS", "StateShiftReceipt",
    "StateShiftError", "evaluate_state_shift", "SUPPORT_ENVELOPE_VERSION",
    "SupportEnvelopeError", "SupportEnvelope", "build_support_envelope",
]
