"""TEHM campaign evaluation and metric reporting."""

from .candidate_executor import (
    EXECUTOR_VERSION, P12_ARMS, CandidateExecutionReceipt,
    CandidateExecutorError, PairedCandidateExecutionReceipt,
    execute_candidate, execute_paired_candidates,
)
from .rtl_candidate_oracle import (
    RTL_CANDIDATE_ORACLE_VERSION, IcarusCandidateOracle,
    RtlCandidateOracleError, execute_rtl_candidate,
)
from .rtl_cohort import (
    RTL_COHORT_VERSION, RtlCohortError, RtlPairedCohortReceipt,
    execute_rtl_paired_cohort,
)
from .orfs_candidate_oracle import (
    ORFS_CANDIDATE_ORACLE_VERSION, OrfsCandidateOracle,
    OrfsCandidateOracleError, execute_orfs_candidate,
)
from .orfs_cohort import (
    ORFS_COHORT_VERSION, OrfsCohortError, OrfsPairedCohortReceipt,
    execute_orfs_paired_cohort,
)
from .paired_metrics import (
    PAIRED_METRICS_VERSION, PairedCohortMetrics, PairedMetricsError,
    summarize_paired_cohort,
)
from .no_skill_calibration import (
    NO_SKILL_CALIBRATION_VERSION, CALIBRATION_DECISIONS,
    CALIBRATION_REASONS, CALIBRATION_LABELS, CALIBRATION_STRATA,
    ORACLE_LABEL_DERIVATION_VERSION,
    NoSkillCalibrationError, NoSkillCalibrationSample,
    NoSkillCalibrationReceipt, wilson_interval,
    mcnemar_regression_test,
    build_no_skill_calibration_samples, derive_no_skill_oracle_label,
    evaluate_no_skill_calibration,
)
from .validation_freeze import (
    VALIDATION_FREEZE_VERSION, ValidationFreezeError,
    ValidationCohortFreezeReceipt, freeze_validation_cohort,
    replay_validation_freeze,
)
from .production_readiness import (
    PRODUCTION_READINESS_VERSION, READINESS_GATES, ProductionReadinessError,
    ProductionReadinessReceipt, build_production_readiness,
    replay_production_readiness,
)
from .policy_mir import (
    POLICY_MIR_VERSION, POLICY_MIR_ARMS, PolicyMIRError,
    build_routed_policy_mir, replay_routed_policy_mir,
)
from .candidate_pool_evidence import (
    CANDIDATE_POOL_EVIDENCE_VERSION, CandidatePoolEvidenceError,
    build_candidate_pool_evidence, replay_candidate_pool_evidence,
)
from .candidate_pool_aggregate import (
    CANDIDATE_POOL_AGGREGATE_VERSION, CandidatePoolAggregateError,
    build_candidate_pool_aggregate, replay_candidate_pool_aggregate,
)
from .efficacy_evidence import (
    EFFICACY_EVIDENCE_VERSION, EfficacyEvidenceError,
    build_efficacy_evidence, replay_efficacy_evidence,
)
from .mir_sample_plan import (
    MIR_SAMPLE_PLAN_VERSION, DEFAULT_MIR_SAMPLE_THRESHOLDS,
    DEFAULT_MAX_SEARCH_CASES, MIRError, MIRSamplePlanReceipt,
    build_mir_sample_plan, replay_mir_sample_plan,
)
from .mir_threshold_governance import (
    MIR_THRESHOLD_GOVERNANCE_VERSION, MIR_THRESHOLD_GOVERNANCE_REPORT_VERSION,
    DECISION as MIR_THRESHOLD_GOVERNANCE_DECISION,
    SCOPE as MIR_THRESHOLD_GOVERNANCE_SCOPE,
    MIRThresholdGovernanceError, MIRThresholdGovernanceReceipt,
    replay_mir_threshold_governance,
)
from .non_p12_challenge import (
    CAPABILITY_GAP_CHALLENGE_VERSION, REPEATED_FAILURE_CHALLENGE_VERSION,
    NON_P12_CHALLENGE_REPLAY_VERSION, NonP12ChallengeReplayError,
    replay_capability_gap_challenge, replay_repeated_failure_challenge,
)
from .production_shadow_mirror import (
    PRODUCTION_SHADOW_MIRROR_VERSION, PRODUCTION_SHADOW_MIRROR_REPORT_VERSION,
    MIRROR_STATUSES,
    ProductionShadowMirrorError, ProductionShadowMirrorReceipt,
    prepare_shadow_mirror, replay_shadow_mirror,
    build_shadow_mirror_report, replay_shadow_mirror_report,
)

__all__ = [
    "EXECUTOR_VERSION", "P12_ARMS", "CandidateExecutionReceipt",
    "CandidateExecutorError", "PairedCandidateExecutionReceipt",
    "execute_candidate", "execute_paired_candidates",
    "RTL_CANDIDATE_ORACLE_VERSION", "IcarusCandidateOracle",
    "RtlCandidateOracleError", "execute_rtl_candidate",
    "RTL_COHORT_VERSION", "RtlCohortError", "RtlPairedCohortReceipt",
    "execute_rtl_paired_cohort",
    "ORFS_CANDIDATE_ORACLE_VERSION", "OrfsCandidateOracle",
    "OrfsCandidateOracleError", "execute_orfs_candidate",
    "ORFS_COHORT_VERSION", "OrfsCohortError", "OrfsPairedCohortReceipt",
    "execute_orfs_paired_cohort",
    "PAIRED_METRICS_VERSION", "PairedCohortMetrics", "PairedMetricsError",
    "summarize_paired_cohort",
    "NO_SKILL_CALIBRATION_VERSION", "CALIBRATION_DECISIONS",
    "CALIBRATION_REASONS", "CALIBRATION_LABELS", "CALIBRATION_STRATA",
    "ORACLE_LABEL_DERIVATION_VERSION",
    "NoSkillCalibrationError", "NoSkillCalibrationSample",
    "NoSkillCalibrationReceipt", "wilson_interval",
    "mcnemar_regression_test",
    "build_no_skill_calibration_samples", "derive_no_skill_oracle_label",
    "evaluate_no_skill_calibration",
    "VALIDATION_FREEZE_VERSION", "ValidationFreezeError",
    "ValidationCohortFreezeReceipt", "freeze_validation_cohort",
    "replay_validation_freeze",
    "PRODUCTION_READINESS_VERSION", "READINESS_GATES", "ProductionReadinessError",
    "ProductionReadinessReceipt", "build_production_readiness",
    "replay_production_readiness",
    "POLICY_MIR_VERSION", "POLICY_MIR_ARMS", "PolicyMIRError",
    "build_routed_policy_mir", "replay_routed_policy_mir",
    "CANDIDATE_POOL_EVIDENCE_VERSION", "CandidatePoolEvidenceError",
    "build_candidate_pool_evidence", "replay_candidate_pool_evidence",
    "CANDIDATE_POOL_AGGREGATE_VERSION", "CandidatePoolAggregateError",
    "build_candidate_pool_aggregate", "replay_candidate_pool_aggregate",
    "EFFICACY_EVIDENCE_VERSION", "EfficacyEvidenceError",
    "build_efficacy_evidence", "replay_efficacy_evidence",
    "MIR_SAMPLE_PLAN_VERSION", "DEFAULT_MIR_SAMPLE_THRESHOLDS",
    "DEFAULT_MAX_SEARCH_CASES", "MIRError", "MIRSamplePlanReceipt",
    "build_mir_sample_plan", "replay_mir_sample_plan",
    "MIR_THRESHOLD_GOVERNANCE_VERSION", "MIR_THRESHOLD_GOVERNANCE_REPORT_VERSION",
    "MIR_THRESHOLD_GOVERNANCE_DECISION", "MIR_THRESHOLD_GOVERNANCE_SCOPE",
    "MIRThresholdGovernanceError", "MIRThresholdGovernanceReceipt",
    "replay_mir_threshold_governance",
    "CAPABILITY_GAP_CHALLENGE_VERSION", "REPEATED_FAILURE_CHALLENGE_VERSION",
    "NON_P12_CHALLENGE_REPLAY_VERSION", "NonP12ChallengeReplayError",
    "replay_capability_gap_challenge", "replay_repeated_failure_challenge",
    "PRODUCTION_SHADOW_MIRROR_VERSION", "PRODUCTION_SHADOW_MIRROR_REPORT_VERSION",
    "MIRROR_STATUSES",
    "ProductionShadowMirrorError", "ProductionShadowMirrorReceipt",
    "prepare_shadow_mirror", "replay_shadow_mirror",
    "build_shadow_mirror_report", "replay_shadow_mirror_report",
]
