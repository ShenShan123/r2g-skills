"""Design graph layer (design doc 22.1, 6.4, 6.5).

Semantic view (v1) is the ``RunContextGraph`` over the flow/signoff domain:
DESIGN / PLATFORM / RUN / STAGE / CHECK / VIOLATION_CLASS / CONFIG_KNOB /
TOOL / ORACLE nodes. Roles and predicates share one feature extractor:
``G_D --psi--> F``, ``RoleMap = f_role(F)``, ``PredicateSnapshot = f_pred(F)``.
"""
from tehm.graph.local_design_graph import (
    EDGE_KINDS,
    NODE_KINDS,
    LocalDesignGraph,
    build_run_context_graph,
)
from tehm.graph.feature_extractor import FeatureValue, extract_features
from tehm.graph.roles import ROLE_SCHEMA, RoleMap, RoleProjector
from tehm.graph.predicates import (
    TRUTH_VALUES,
    PredicateObservation,
    PredicateSnapshot,
    TruthValue,
    coverage,
    extract_predicates,
    support,
)

__all__ = [
    "EDGE_KINDS", "NODE_KINDS", "LocalDesignGraph", "build_run_context_graph",
    "FeatureValue", "extract_features",
    "ROLE_SCHEMA", "RoleMap", "RoleProjector",
    "TRUTH_VALUES", "PredicateObservation", "PredicateSnapshot", "TruthValue",
    "coverage", "extract_predicates", "support",
]
