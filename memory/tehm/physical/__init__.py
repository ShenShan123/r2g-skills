"""Cross-stage Physical Effect Memory (design doc 26 Phase 11).

Connects the flow/RTL stage to the physical signoff stage:

    (RTL/flow context, action) -> (ΔWNS, ΔTNS, ΔArea, ΔPower, ΔCongestion, ΔDRC)

Phase 1 records the empirical physical deltas of executed actions and aggregates
them per action (transformation family / effect group) — a Physical Effect
Memory that predicts expected deltas from the observed distribution. It does NOT
claim a differentiable gradient (design doc Phase 11).
"""
from tehm.physical.effects import (
    PHYSICAL_METRICS,
    PhysicalEffect,
    extract_deltas,
)
from tehm.physical.memory import PhysicalEffectMemory, typed_action_signature
from tehm.physical.calibration import (
    CALIBRATION_VERSION,
    calibrate_retrieval,
)
from tehm.physical.graph_context import (
    GRAPH_CONTEXT_VERSION,
    PhysicalGraphContext,
    load_defgraph_context,
)
from tehm.physical.orfs_ppa import (
    ORFS_PPA_VERSION, build_orfs_pair, calibration_group_key, extract_orfs_ppa)
from tehm.physical.orfs_preflight import (
    PREFLIGHT_VERSION, inspect_routing_layer_adjustment, parse_orfs_config,
    preflight_digest, validate_persisted_execution_preflight)
from tehm.physical.utility_contracts import (
    TIMING_RELIEF_BUDGETED_V1_ID,
    TIMING_RELIEF_BUDGETED_V2_50_TO_45_ID,
    contract_action,
    evaluate_observed_contract,
    select_contract_proposal,
    timing_relief_budgeted_v1,
    timing_relief_budgeted_v2_50_to_45,
    utility_contract_digest,
    validate_utility_contract,
)

__all__ = ["PHYSICAL_METRICS", "PhysicalEffect", "extract_deltas",
           "PhysicalEffectMemory", "CALIBRATION_VERSION", "calibrate_retrieval",
           "typed_action_signature",
           "ORFS_PPA_VERSION", "extract_orfs_ppa", "build_orfs_pair",
           "calibration_group_key",
           "PREFLIGHT_VERSION", "inspect_routing_layer_adjustment",
           "parse_orfs_config", "preflight_digest",
           "validate_persisted_execution_preflight",
           "GRAPH_CONTEXT_VERSION",
           "PhysicalGraphContext", "load_defgraph_context",
           "TIMING_RELIEF_BUDGETED_V1_ID",
           "TIMING_RELIEF_BUDGETED_V2_50_TO_45_ID",
           "contract_action", "evaluate_observed_contract",
           "select_contract_proposal", "timing_relief_budgeted_v1",
           "timing_relief_budgeted_v2_50_to_45", "utility_contract_digest",
           "validate_utility_contract"]
from .utility_contracts import (  # noqa: F401
    TIMING_RELIEF_BUDGETED_V1_ID,
    UTILITY_CONTRACT_VERSION,
    UtilityContractError,
    contract_action,
    evaluate_observed_contract,
    select_contract_proposal,
    timing_relief_budgeted_v1,
    utility_contract_digest,
    validate_utility_contract,
)

__all__ = [
    "TIMING_RELIEF_BUDGETED_V1_ID",
    "UTILITY_CONTRACT_VERSION",
    "UtilityContractError",
    "contract_action",
    "evaluate_observed_contract",
    "select_contract_proposal",
    "timing_relief_budgeted_v1",
    "utility_contract_digest",
    "validate_utility_contract",
]
