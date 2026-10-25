"""RTL extension (design doc 26 Phase 10, 22.1 RTL v2).

Adds the RTL action domains (``rtl.AST_REWRITE`` / ``rtl.GUARD_STRENGTHEN`` /
``rtl.RESET_RESTORE`` / ``rtl.WIDTH_CORRECT`` / ``rtl.PRIORITY_REORDER``), a
structural Verilog parser, the RTL semantic graph, an Icarus compile/sim oracle,
and an RTL-evidence capture adapter. Pure-Python parser + rewrites; the Icarus
oracle shells to iverilog/vvp when available (graceful otherwise).
"""
from tehm.rtl.verilog_parse import (
    AlwaysBlock,
    Assign,
    CaseItem,
    FSM,
    RTLModule,
    Signal,
    parse_verilog,
)
from tehm.rtl.rtl_graph import build_rtl_graph, design_graph_digest
from tehm.rtl.rtl_actions import (
    RTL_ACTION_DOMAINS,
    apply_rtl_action,
    apply_guard_strengthen,
    apply_reset_restore,
    apply_width_correct,
    apply_priority_reorder,
)
from tehm.rtl.rtl_oracle import IcarusOracle
from tehm.rtl.equivalence import (
    EQUIVALENCE_VERSION, YosysEquivalenceOracle, verify_profile_equivalence)
from tehm.rtl.compatibility import structural_compatibility
from tehm.rtl.rtl_evidence import capture_rtl_fix, build_rtl_execution_record
from tehm.rtl.conformal import (
    RTL_CONFORMAL_METHOD, RTL_CONFORMAL_OBLIGATIONS,
    RTL_CONFORMAL_PREDICTION_RULE, RTL_CONFORMAL_VERSION,
    RTLConformalCalibrationReceipt, RTLConformalError, RTLConformalSample,
    calibrate_rtl_obligations, observed_rtl_obligations,
    predict_rtl_obligations,
)

__all__ = [
    "AlwaysBlock", "Assign", "CaseItem", "FSM", "RTLModule", "Signal",
    "parse_verilog",
    "build_rtl_graph", "design_graph_digest",
    "RTL_ACTION_DOMAINS", "apply_rtl_action", "apply_guard_strengthen",
    "apply_reset_restore", "apply_width_correct", "apply_priority_reorder",
    "IcarusOracle",
    "EQUIVALENCE_VERSION", "YosysEquivalenceOracle",
    "verify_profile_equivalence",
    "structural_compatibility",
    "capture_rtl_fix", "build_rtl_execution_record",
    "RTL_CONFORMAL_METHOD", "RTL_CONFORMAL_OBLIGATIONS",
    "RTL_CONFORMAL_PREDICTION_RULE", "RTL_CONFORMAL_VERSION",
    "RTLConformalCalibrationReceipt", "RTLConformalError",
    "RTLConformalSample", "calibrate_rtl_obligations",
    "observed_rtl_obligations", "predict_rtl_obligations",
]
