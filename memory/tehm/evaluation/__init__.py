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

__all__ = [
    "EXECUTOR_VERSION", "P12_ARMS", "CandidateExecutionReceipt",
    "CandidateExecutorError", "PairedCandidateExecutionReceipt",
    "execute_candidate", "execute_paired_candidates",
    "RTL_CANDIDATE_ORACLE_VERSION", "IcarusCandidateOracle",
    "RtlCandidateOracleError", "execute_rtl_candidate",
]
