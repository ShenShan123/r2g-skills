"""Evidence levels for the causal shadow graph.

The causal graph is deliberately stricter than the provenance graph.  A
transition proves that an action was executed (L1), but a stronger causal
claim requires an explicit controlled pair or independent replication.  The
helpers in this module are pure and therefore safe to use from audit code.
"""
from __future__ import annotations

from enum import Enum


class CausalEvidenceLevel(str, Enum):
    L0_ASSOCIATION = "L0_ASSOCIATION"
    L1_EXECUTED_INTERVENTION = "L1_EXECUTED_INTERVENTION"
    L2_CONTROLLED_INTERVENTION = "L2_CONTROLLED_INTERVENTION"
    L3_REPLICATED_EFFECT = "L3_REPLICATED_EFFECT"
    L4_TRANSFER_SUPPORTED_MECHANISM = "L4_TRANSFER_SUPPORTED_MECHANISM"


EVIDENCE_LEVELS = tuple(item.value for item in CausalEvidenceLevel)
_LEVEL_RANK = {value: index for index, value in enumerate(EVIDENCE_LEVELS)}


def validate_evidence_level(level: str | CausalEvidenceLevel) -> str:
    value = level.value if isinstance(level, CausalEvidenceLevel) else str(level)
    if value not in _LEVEL_RANK:
        raise ValueError(f"unknown causal evidence level: {level!r}")
    return value


def evidence_rank(level: str | CausalEvidenceLevel) -> int:
    return _LEVEL_RANK[validate_evidence_level(level)]


def at_least(level: str | CausalEvidenceLevel,
             required: str | CausalEvidenceLevel) -> bool:
    return evidence_rank(level) >= evidence_rank(required)


def transition_evidence_level(*, transition_present: bool,
                              verifier_present: bool = True) -> str:
    """Return the maximum level justified by one executed transition.

    This intentionally never returns L2+: execution without a control arm is
    observational intervention evidence, not a controlled causal estimate.
    """
    if not transition_present:
        return CausalEvidenceLevel.L0_ASSOCIATION.value
    if not verifier_present:
        return CausalEvidenceLevel.L0_ASSOCIATION.value
    return CausalEvidenceLevel.L1_EXECUTED_INTERVENTION.value


__all__ = [
    "CausalEvidenceLevel", "EVIDENCE_LEVELS", "validate_evidence_level",
    "evidence_rank", "at_least", "transition_evidence_level",
]
