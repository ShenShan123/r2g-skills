"""Canonical verified experience substrate (design doc 19, 21.2).

The canonical store is the ONLY source of truth: verified states, transitions
and episodes as they actually happened. Views (tehm/views) are derived, typed
projections of this substrate and never overwrite it (19.3).
"""
from tehm.canonical.verifier import (
    CONFIDENCE_TIERS,
    ORACLE_TYPES,
    VerifierSnapshot,
    toolchain_snapshot,
)
from tehm.canonical.state import CanonicalState, source_digest
from tehm.canonical.transition import (
    OUTCOMES,
    Action,
    CanonicalTransition,
    ObservationDelta,
    classify_outcome,
    primary_effect_key,
)
from tehm.canonical.episode import (
    TERMINAL_STATUSES,
    CanonicalEpisode,
    trajectory_summary,
)

__all__ = [
    "CONFIDENCE_TIERS", "ORACLE_TYPES", "VerifierSnapshot", "toolchain_snapshot",
    "CanonicalState", "source_digest",
    "OUTCOMES", "Action", "CanonicalTransition", "ObservationDelta",
    "classify_outcome", "primary_effect_key",
    "TERMINAL_STATUSES", "CanonicalEpisode", "trajectory_summary",
]
