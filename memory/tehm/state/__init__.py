"""Shadow-only current-valid-state relations and resolver (P1)."""
from .authority import record_relation_authority, verify_relation_authority
from .relations import MemoryRelation, get_relation, load_relations, record_relation
from .resolver import (
    RESOLVER_VERSION, StateResolutionError, load_resolution_snapshot,
    resolve_current_state, resolve_state, verify_resolution_snapshot,
)
from .receipts import (
    RELATION_AUTHORITY_VERSION, MemoryRelationReceipt, RelationAuthorityReceipt,
    ResolvedMemoryState,
    StateResolutionReceipt, SuppressionReceipt,
)
from .schema import STATE_SCHEMA_VERSION, ensure_state_schema
from .shift import StateShiftError, evaluate_state_shift
from .shift_receipts import STATE_SHIFT_VERSION, SHIFT_DIMENSIONS, StateShiftReceipt
from .risk_receipts import RISK_RECEIPT_VERSION, RiskReceipt
from .support_envelope import (
    SUPPORT_ENVELOPE_VERSION, SupportEnvelopeError, SupportEnvelope,
    build_support_envelope,
)

__all__ = [
    "MemoryRelation", "MemoryRelationReceipt", "RelationAuthorityReceipt",
    "RELATION_AUTHORITY_VERSION",
    "ResolvedMemoryState",
    "RESOLVER_VERSION", "STATE_SCHEMA_VERSION", "StateResolutionError",
    "StateResolutionReceipt", "SuppressionReceipt", "ensure_state_schema",
    "get_relation", "load_relations", "load_resolution_snapshot",
    "record_relation", "resolve_current_state", "resolve_state",
    "verify_resolution_snapshot",
    "record_relation_authority", "verify_relation_authority",
    "STATE_SHIFT_VERSION", "SHIFT_DIMENSIONS", "StateShiftReceipt",
    "StateShiftError", "evaluate_state_shift", "SUPPORT_ENVELOPE_VERSION",
    "SupportEnvelopeError", "SupportEnvelope", "build_support_envelope",
    "RISK_RECEIPT_VERSION", "RiskReceipt",
]
