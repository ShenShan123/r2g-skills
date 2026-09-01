"""Counterfactual evidence for retrieval-failure attribution.

Retrieval failure is not inferred from a failed memory action.  It requires a
typed eligible-candidate set, the actual candidate-pool receipt, a failed
selected memory execution, and an explicitly marked counterfactual execution
of an eligible candidate that the pool omitted.  The module is evaluation /
shadow-only and never changes retrieval, canonical memory, or authority.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tehm.canonical.transition import HARMFUL_OUTCOMES, POSITIVE_OUTCOMES
from tehm.ids import stable_dumps
from tehm.retrieval.candidate_pool import CandidatePoolReceipt
from contracts import MemoryRoutingDecision

from ..evaluation.candidate_executor import CandidateExecutionReceipt


RETRIEVAL_ATTRIBUTION_VERSION = "retrieval-attribution-v0.1"


class RetrievalAttributionError(ValueError):
    """Retrieval attribution evidence is malformed or incomplete."""


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise RetrievalAttributionError(f"retrieval attribution {name} is invalid")
    return value.strip()


def _strings(value: object, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple, set, frozenset)):
        raise RetrievalAttributionError(
            f"retrieval attribution {name} must be a sequence")
    values = tuple(sorted(_text(item, name) for item in value))
    if len(values) != len(set(values)):
        raise RetrievalAttributionError(
            f"retrieval attribution {name} must not contain duplicates")
    return values


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


@dataclass(frozen=True)
class RetrievalAttributionReceipt:
    """Replayable proof (or explicit negative result) for retrieval failure."""

    case_id: str
    routing_receipt_id: str
    candidate_pool_receipt_digest: str
    eligible_candidate_ids: tuple[str, ...]
    selected_candidate_ids: tuple[str, ...]
    missed_candidate_ids: tuple[str, ...]
    selected_execution_digest: str | None
    counterfactual_execution_digests: tuple[str, ...]
    retrieval_failure: bool
    oracle_success: bool
    reason: str
    evaluation_only: bool = True

    def __post_init__(self) -> None:
        _text(self.case_id, "case_id")
        _text(self.routing_receipt_id, "routing_receipt_id")
        _text(self.candidate_pool_receipt_digest, "candidate_pool_receipt_digest")
        eligible = _strings(self.eligible_candidate_ids, "eligible_candidate_ids")
        selected = _strings(self.selected_candidate_ids, "selected_candidate_ids")
        missed = _strings(self.missed_candidate_ids, "missed_candidate_ids")
        if not set(selected) <= set(eligible):
            raise RetrievalAttributionError(
                "retrieval attribution selected candidates are not eligible")
        if set(missed) != set(eligible) - set(selected):
            raise RetrievalAttributionError(
                "retrieval attribution missed candidates do not match eligible set")
        if self.selected_execution_digest is not None:
            _text(self.selected_execution_digest, "selected_execution_digest")
        digests = _strings(self.counterfactual_execution_digests,
                           "counterfactual_execution_digests")
        if type(self.retrieval_failure) is not bool or type(self.oracle_success) is not bool:
            raise RetrievalAttributionError("retrieval attribution verdicts must be boolean")
        if self.evaluation_only is not True:
            raise RetrievalAttributionError("retrieval attribution must be evaluation-only")
        _text(self.reason, "reason")
        if self.retrieval_failure and not self.oracle_success:
            raise RetrievalAttributionError(
                "retrieval failure requires counterfactual oracle success")
        object.__setattr__(self, "eligible_candidate_ids", eligible)
        object.__setattr__(self, "selected_candidate_ids", selected)
        object.__setattr__(self, "missed_candidate_ids", missed)
        object.__setattr__(self, "counterfactual_execution_digests", digests)

    @property
    def receipt_digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def receipt_id(self) -> str:
        return "retrieval_" + self.receipt_digest.split(":", 1)[1][:24]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": RETRIEVAL_ATTRIBUTION_VERSION,
            "case_id": self.case_id,
            "routing_receipt_id": self.routing_receipt_id,
            "candidate_pool_receipt_digest": self.candidate_pool_receipt_digest,
            "eligible_candidate_ids": list(self.eligible_candidate_ids),
            "selected_candidate_ids": list(self.selected_candidate_ids),
            "missed_candidate_ids": list(self.missed_candidate_ids),
            "selected_execution_digest": self.selected_execution_digest,
            "counterfactual_execution_digests": list(self.counterfactual_execution_digests),
            "retrieval_failure": self.retrieval_failure,
            "oracle_success": self.oracle_success,
            "reason": self.reason,
            "evaluation_only": self.evaluation_only,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "RetrievalAttributionReceipt":
        if not isinstance(payload, Mapping):
            raise RetrievalAttributionError("retrieval attribution receipt must be an object")
        required = {
            "case_id", "routing_receipt_id", "candidate_pool_receipt_digest",
            "eligible_candidate_ids", "selected_candidate_ids", "missed_candidate_ids",
            "selected_execution_digest", "counterfactual_execution_digests",
            "retrieval_failure", "oracle_success", "reason", "evaluation_only",
        }
        if not required <= set(payload):
            raise RetrievalAttributionError("retrieval attribution receipt is missing fields")
        receipt = cls(
            case_id=payload["case_id"], routing_receipt_id=payload["routing_receipt_id"],
            candidate_pool_receipt_digest=payload["candidate_pool_receipt_digest"],
            eligible_candidate_ids=tuple(payload["eligible_candidate_ids"]),
            selected_candidate_ids=tuple(payload["selected_candidate_ids"]),
            missed_candidate_ids=tuple(payload["missed_candidate_ids"]),
            selected_execution_digest=payload["selected_execution_digest"],
            counterfactual_execution_digests=tuple(payload["counterfactual_execution_digests"]),
            retrieval_failure=payload["retrieval_failure"],
            oracle_success=payload["oracle_success"], reason=payload["reason"],
            evaluation_only=payload["evaluation_only"])
        supplied = payload.get("receipt_digest")
        if supplied is not None and supplied != receipt.receipt_digest:
            raise RetrievalAttributionError("retrieval attribution receipt digest mismatch")
        return receipt


def _execution_map(value: object) -> dict[str, CandidateExecutionReceipt]:
    if not isinstance(value, Mapping):
        raise RetrievalAttributionError(
            "retrieval attribution counterfactual executions must be a mapping")
    result: dict[str, CandidateExecutionReceipt] = {}
    for candidate_id, receipt in value.items():
        key = _text(candidate_id, "counterfactual candidate ID")
        if not isinstance(receipt, CandidateExecutionReceipt):
            raise RetrievalAttributionError(
                "retrieval attribution counterfactual execution is invalid")
        if receipt.candidate_id != key:
            raise RetrievalAttributionError(
                "retrieval attribution execution candidate ID mismatch")
        if receipt.metadata.get("counterfactual") is not True:
            raise RetrievalAttributionError(
                "retrieval attribution requires an explicit counterfactual marker")
        result[key] = receipt
    return result


def attribute_retrieval_failure(
        routing: MemoryRoutingDecision,
        candidate_pool: CandidatePoolReceipt,
        eligible_candidate_ids: Sequence[str],
        *, selected_execution: CandidateExecutionReceipt | None = None,
        counterfactual_executions: Mapping[str, CandidateExecutionReceipt] | None = None,
) -> RetrievalAttributionReceipt:
    """Build retrieval attribution from explicit pool and oracle witnesses."""
    if not isinstance(routing, MemoryRoutingDecision):
        raise TypeError("retrieval attribution routing must be MemoryRoutingDecision")
    if not isinstance(candidate_pool, CandidatePoolReceipt):
        raise TypeError("retrieval attribution pool must be CandidatePoolReceipt")
    if (candidate_pool.routing_receipt_id != routing.routing_receipt_id or
            candidate_pool.routing_decision != routing.decision):
        raise RetrievalAttributionError("retrieval attribution routing receipt mismatch")
    eligible = _strings(eligible_candidate_ids, "eligible_candidate_ids")
    selected = tuple(candidate_pool.memory_candidate_ids)
    if routing.decision in {"ABSTAIN", "INAPPLICABLE", "NO_SKILL"} and selected:
        raise RetrievalAttributionError(
            "retrieval attribution cannot select memory under a no-memory route")
    if selected and selected_execution is None:
        selected_execution_digest = None
    elif selected_execution is not None:
        if (selected_execution.case_id != candidate_pool.case_id or
                selected_execution.candidate_id not in selected or
                selected_execution.source != "structured_memory"):
            raise RetrievalAttributionError(
                "retrieval attribution selected execution does not match pool")
        selected_execution_digest = selected_execution.execution_digest
    else:
        selected_execution_digest = None
    missed = tuple(sorted(set(eligible) - set(selected)))
    cf = _execution_map(counterfactual_executions or {})
    if any(candidate_id not in missed for candidate_id in cf):
        raise RetrievalAttributionError(
            "retrieval attribution counterfactual is not a missed candidate")
    if any(receipt.case_id != candidate_pool.case_id for receipt in cf.values()):
        raise RetrievalAttributionError(
            "retrieval attribution counterfactual case ID mismatch")
    oracle_success = any(
        receipt.outcome in POSITIVE_OUTCOMES for receipt in cf.values())
    selected_failed = (selected_execution is not None and
                       selected_execution.outcome in HARMFUL_OUTCOMES)
    if not selected:
        reason = "no_memory_candidate_selected"
    elif selected_execution is None or selected_execution.outcome == "UNKNOWN":
        reason = "selected_execution_missing_or_unknown"
    elif not selected_failed:
        reason = "selected_candidate_not_failed"
    elif not missed:
        reason = "no_eligible_candidate_missed"
    elif not oracle_success:
        reason = "missed_candidate_not_verified"
    else:
        reason = "eligible_candidate_missed_and_selected_failed"
    return RetrievalAttributionReceipt(
        case_id=candidate_pool.case_id,
        routing_receipt_id=routing.routing_receipt_id,
        candidate_pool_receipt_digest=candidate_pool.receipt_digest,
        eligible_candidate_ids=eligible, selected_candidate_ids=selected,
        missed_candidate_ids=missed,
        selected_execution_digest=selected_execution_digest,
        counterfactual_execution_digests=tuple(
            receipt.execution_digest for _, receipt in sorted(cf.items())),
        retrieval_failure=(reason == "eligible_candidate_missed_and_selected_failed"),
        oracle_success=oracle_success, reason=reason)


__all__ = [
    "RETRIEVAL_ATTRIBUTION_VERSION", "RetrievalAttributionError",
    "RetrievalAttributionReceipt", "attribute_retrieval_failure",
]
