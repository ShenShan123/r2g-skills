"""Single source of truth for all teaching status enums, required artifacts,
and the platform lock. Mirrors TEACHING_POLICY.md §5/§6/§7.

Both ``verify_submission.py`` (autograder) and ``check_my_case.py`` (student
self-check) import from here so they can never drift from each other or from
the policy. If you change a status name in the policy, change it here too.
"""

from __future__ import annotations

PLATFORM_LOCK = "nangate45"  # 红线 3

STAGE1_STATUSES = {
    "STAGE1_SYNTHESIS_PASS",
    "FAILED_AT_DISCOVERY",
    "FAILED_AT_LINT",
    "FAILED_AT_SIMULATION",
    "FAILED_AT_SYNTHESIS",
    "BLOCKED_MISSING_DEPENDENCY",
    "BLOCKED_RTL_BUG",
    "INSUFFICIENT_EVIDENCE",
}

STAGE2_STATUSES = {
    "STAGE2_GDS_MVP_PASS",
    "FAILED_AT_BACKEND",
    "FAILED_AT_PPA_EXTRACTION",
    "FAILED_AT_TIMING_CHECK",
    "BLOCKED_BY_STAGE1",
    "BLOCKED_RTL_BUG",
    "INSUFFICIENT_EVIDENCE",
}

STAGE3_STATUSES = {
    "STAGE3_EVIDENCE_RECORDED",
    "DRC_PASS_LVS_RCX_BLOCKED",
    "DRC_FAIL_REPAIR_NEEDED",
    "POST_GDS_EXTRACTION_BLOCKED",
    "INSUFFICIENT_EVIDENCE",
}

DRC_STATUSES = {
    "DRC_PASS", "DRC_FAIL", "DRC_BLOCKED_MISSING_RULES",
    "DRC_BLOCKED_MISSING_TOOL_ENTRY", "DRC_NOT_RUN", "INSUFFICIENT_EVIDENCE",
}
LVS_STATUSES = {
    "LVS_PASS", "LVS_FAIL", "LVS_BLOCKED_MISSING_RULES",
    "LVS_BLOCKED_MISSING_TOOL_ENTRY", "LVS_NOT_RUN", "INSUFFICIENT_EVIDENCE",
}
RCX_STATUSES = {
    "RCX_PASS", "RCX_FAIL", "SPEF_PRESENT_NOT_VALIDATED",
    "RCX_BLOCKED_MISSING_RULES", "RCX_BLOCKED_MISSING_TOOL_ENTRY",
    "RCX_NOT_RUN", "INSUFFICIENT_EVIDENCE",
}

STAGE4_STATUSES = {
    "STAGE4_EXTRACTION_PASS",
    "STAGE4_EXTRACTION_PARTIAL",
    "STAGE4_LABELS_PASS_FEATURES_FAILED",
    "STAGE4_FEATURES_PASS_LABELS_FAILED",
    "FAILED_AT_LABEL_EXTRACTION",
    "FAILED_AT_FEATURE_EXTRACTION",
    "BLOCKED_BY_STAGE2_OR_STAGE3",
    "INSUFFICIENT_EVIDENCE",
}

FEATURE_SUMMARY_STATUSES = {
    "FEATURE_EXTRACTION_PASS",
    "FEATURE_EXTRACTION_PARTIAL",
    "FEATURE_EXTRACTION_FAIL",
    "FEATURE_BLOCKED_MISSING_DEF",
    "FEATURE_BLOCKED_MISSING_NANGATE_LIB",
    "FEATURE_NOT_RUN",
    "INSUFFICIENT_EVIDENCE",
}

# stage name -> the valid final-status set for that stage
STAGE_STATUS_SETS = {
    "stage1": STAGE1_STATUSES,
    "stage2": STAGE2_STATUSES,
    "stage3": STAGE3_STATUSES,
    "stage4": STAGE4_STATUSES,
}

# A *_PASS status that, if claimed, REQUIRES real artifacts to back it.
PASS_STATUSES = {
    "STAGE1_SYNTHESIS_PASS",
    "STAGE2_GDS_MVP_PASS",
    "STAGE3_EVIDENCE_RECORDED",
    "STAGE4_EXTRACTION_PASS",
    "STAGE4_LABELS_PASS_FEATURES_FAILED",
    "STAGE4_FEATURES_PASS_LABELS_FAILED",
}

# Required CSV filenames (TEACHING_POLICY §6)
REQUIRED_LABEL_CSVS = [
    "wirelength.csv",
    "cell_congestion.csv",
    "timing_features.csv",
    "ir_drop.csv",
]
REQUIRED_FEATURE_CSVS = [
    "metadata.csv",
    "nodes_gate.csv",
    "nodes_net.csv",
    "nodes_iopin.csv",
    "nodes_pin.csv",
    "edges_iopin_net.csv",
    "edges_pin_net.csv",
    "edges_gate_pin.csv",
]

ERROR_TAGS = {
    "E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "E9", "E10", "NONE",
}

# Substrings that must never appear in落盘记录 (report / CASE_STATE / status row).
# Machine-specific absolute path prefixes (TEACHING_POLICY §3).
FORBIDDEN_PATH_SUBSTRINGS = ("/home/", "/data1/", "/data2/", "/root/", "/Users/")

# The cell_type_id value meaning "UNKNOWN" in feature CSVs. A high share of this
# signals wrong platform or missing nangate45 library (policy §6 step 5).
UNKNOWN_CELL_TYPE_ID = "95"
UNKNOWN_SHARE_WARN_THRESHOLD = 0.5  # >50% UNKNOWN -> suspicious


def stage_status_valid(stage: str, status: str) -> bool:
    return status in STAGE_STATUS_SETS.get(stage, set())
