"""Parametric shadow-only experiment interfaces."""

from .shadow import (  # noqa: F401
    PARAMETRIC_SHADOW_STATUS,
    PARAMETRIC_SHADOW_VERSION,
    SHADOW_ABSTAINED,
    SHADOW_PROPOSED,
    ParametricShadowError,
    build_shadow_proposal,
    proposal_digest,
)
from .shadow_campaign import (  # noqa: F401
    AppendOnlyShadowLog,
    ShadowCampaignError,
    assert_counts_unchanged,
    build_observation_gate_audit,
    build_outcome,
    build_receipt,
    canonical_counts,
    join_receipts_and_outcomes,
    read_log,
    summarise,
)
from .calibration import (  # noqa: F401
    calibrate_lineage_grouped, calibrate_exact_groups, exact_calibration_group_key,
    materialize_shadow_policy)

__all__ = [
    "AppendOnlyShadowLog", "ParametricShadowError", "ShadowCampaignError",
    "assert_counts_unchanged", "build_observation_gate_audit", "build_outcome", "build_receipt",
    "build_shadow_proposal", "canonical_counts", "join_receipts_and_outcomes",
    "proposal_digest", "read_log", "summarise",
    "calibrate_lineage_grouped", "calibrate_exact_groups",
    "exact_calibration_group_key", "materialize_shadow_policy",
]
