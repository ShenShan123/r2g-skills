"""Typed memory views (design doc 3.2, 19.3, 22).

The five views (semantic / diagnostic / episodic / procedural / parametric) are
first-class materialized objects in ``tehm_views`` — never buried fields inside
a big JSON. Extractor versions are stamped so a view can be rebuilt and compared
across extractor upgrades.
"""
from tehm.views.base import (
    OWNER_TYPES,
    VIEW_TYPES,
    ViewRecord,
    payload_digest,
    upsert_view,
)
from tehm.views.semantic import (
    SEMANTIC_EXTRACTOR_VERSION,
    build_semantic_view,
    materialize_semantic,
)
from tehm.views.diagnostic import (
    DIAGNOSTIC_EXTRACTOR_VERSION,
    build_diagnostic_view,
    extract_diagnostic_signature,
    materialize_diagnostic,
)
from tehm.views.episodic import (
    EPISODIC_EXTRACTOR_VERSION,
    build_episodic_view,
    materialize_episodic,
)
from tehm.views.procedural import (
    PROCEDURAL_EXTRACTOR_VERSION,
    build_procedural_view,
    materialize_procedural,
)
from tehm.views.parametric_stub import PARAMETRIC_VIEW_STATUS

__all__ = [
    "OWNER_TYPES", "VIEW_TYPES", "ViewRecord", "payload_digest", "upsert_view",
    "SEMANTIC_EXTRACTOR_VERSION", "build_semantic_view", "materialize_semantic",
    "DIAGNOSTIC_EXTRACTOR_VERSION", "build_diagnostic_view",
    "extract_diagnostic_signature", "materialize_diagnostic",
    "EPISODIC_EXTRACTOR_VERSION", "build_episodic_view", "materialize_episodic",
    "PROCEDURAL_EXTRACTOR_VERSION", "build_procedural_view", "materialize_procedural",
    "PARAMETRIC_VIEW_STATUS",
]
