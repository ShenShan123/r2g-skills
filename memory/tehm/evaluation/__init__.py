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
]
