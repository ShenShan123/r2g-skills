"""Evaluation-only execution adapter for structured candidates (P12).

The adapter records an oracle-facing receipt but owns no toolchain, database,
canonical evidence, lifecycle, or promotion state. A real R2G executor/oracle
is injected by the campaign harness; absent one, the result is explicitly
UNKNOWN rather than inferred from the candidate or caller-provided flags.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from tehm.canonical.transition import OUTCOMES
from contracts import NO_SKILL_REASONS
from tehm.ids import stable_dumps
from tehm.retrieval.structured_candidate import StructuredRepairCandidate


EXECUTOR_VERSION = "candidate-executor-v0.1"
_VERDICTS = frozenset({"PASS", "FAIL", "UNKNOWN"})
_GOLD_KEYS = frozenset({"fix", "gold_patch", "repaired_rtl", "heldout_answer"})
P12_ARMS = ("NO_MEMORY", "ALWAYS_MEMORY", "APPLICABILITY_GATED", "CAUSAL_NO_SKILL")


class CandidateExecutorError(ValueError):
    """A candidate execution request or oracle receipt is malformed."""


def _text(value: object, field_name: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str or (not allow_empty and not value.strip()):
        raise CandidateExecutorError(f"candidate execution {field_name} is invalid")
    return value.strip()


def _contains_gold(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(key in _GOLD_KEYS or _contains_gold(item)
                   for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_gold(item) for item in value)
    return False


def _case_payload(frozen_case: object) -> dict:
    if not isinstance(frozen_case, Mapping):
        raise CandidateExecutorError("frozen_case must be an object")
    if _contains_gold(frozen_case):
        raise CandidateExecutorError("frozen_case contains gold-answer fields")
    try:
        payload = __import__("json").loads(stable_dumps(dict(frozen_case)))
    except (TypeError, ValueError, __import__("json").JSONDecodeError) as exc:
        raise CandidateExecutorError("frozen_case is not JSON-serializable") from exc
    if not isinstance(payload, dict):  # pragma: no cover
        raise CandidateExecutorError("frozen_case must be an object")
    return payload


def _budget(value: int | Mapping) -> tuple[int, dict]:
    if isinstance(value, Mapping):
        raw = value.get("candidate_budget", value.get("total_budget", 3))
        if type(raw) is not int or raw < 1:
            raise CandidateExecutorError("candidate execution budget is invalid")
        if raw > 3:
            raise CandidateExecutorError("P12 candidate budget must be at most three")
        details = dict(value)
        details["candidate_budget"] = raw
        return raw, details
    if type(value) is not int or value < 1:
        raise CandidateExecutorError("P12 candidate budget must be a positive integer")
    if value > 3:
        raise CandidateExecutorError("P12 candidate budget must be at most three")
    return value, {"candidate_budget": value}


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _call_oracle(oracle: object, candidate: StructuredRepairCandidate,
                 frozen_case: Mapping, budget: dict) -> dict:
    if oracle is None:
        return {}
    try:
        if callable(oracle):
            result = oracle(candidate, frozen_case, budget)
        elif hasattr(oracle, "execute_candidate"):
            result = oracle.execute_candidate(candidate, frozen_case, budget)
        elif hasattr(oracle, "run"):
            result = oracle.run(candidate, frozen_case, budget)
        else:
            raise CandidateExecutorError(
                "oracle must be callable or expose execute_candidate/run")
    except CandidateExecutorError:
        raise
    except Exception as exc:  # an oracle crash is an UNKNOWN result, not PASS
        return {"outcome": "UNKNOWN", "oracle_error": type(exc).__name__}
    if result is None:
        return {}
    if not isinstance(result, Mapping):
        raise CandidateExecutorError("oracle result must be an object")
    if _contains_gold(result):
        raise CandidateExecutorError("oracle result contains gold-answer fields")
    return dict(result)


def _verdict(value: object, field_name: str) -> str:
    if value is None:
        return "UNKNOWN"
    value = _text(value, field_name).upper()
    if value not in _VERDICTS:
        raise CandidateExecutorError(
            f"candidate execution {field_name} must be PASS/FAIL/UNKNOWN")
    return value


def _outcome(result: Mapping, compile_result: str,
             functional_result: str, signoff_result: str) -> str:
    explicit = result.get("outcome")
    if explicit is not None:
        explicit = _text(explicit, "outcome").upper()
        if explicit not in OUTCOMES:
            raise CandidateExecutorError("candidate execution outcome is invalid")
        return explicit
    if "FAIL" in {compile_result, functional_result, signoff_result}:
        return "FAIL"
    if compile_result == functional_result == "PASS" and signoff_result in {"PASS", "UNKNOWN"}:
        return "PASS" if signoff_result == "PASS" else "PARTIAL"
    return "UNKNOWN"


def _oracle_metadata(result: Mapping) -> dict:
    """Retain oracle evidence without allowing it to replace executor fields."""
    raw = result.get("metadata")
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise CandidateExecutorError("candidate execution metadata must be an object")
    return dict(raw)


@dataclass(frozen=True)
class CandidateExecutionReceipt:
    case_id: str
    candidate_id: str
    source: str
    action_digest: str
    compile_result: str
    functional_result: str
    signoff_result: str | None
    outcome: str
    created_regressions: tuple[str, ...]
    obligations: dict
    toolchain_digest: str
    oracle_digest: str
    produced_transition_id: str | None
    candidate_digest: str
    budget: int
    execution_version: str = EXECUTOR_VERSION
    evaluation_only: bool = True
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("case_id", "candidate_id", "source", "action_digest",
                     "candidate_digest", "toolchain_digest", "oracle_digest",
                     "execution_version"):
            _text(getattr(self, name), name)
        if self.source not in {"structured_memory", "no_memory"}:
            raise CandidateExecutorError("candidate execution source is invalid")
        for name in ("compile_result", "functional_result"):
            if getattr(self, name) not in _VERDICTS:
                raise CandidateExecutorError(f"candidate execution {name} is invalid")
        if self.signoff_result is not None and self.signoff_result not in _VERDICTS:
            raise CandidateExecutorError("candidate execution signoff_result is invalid")
        if self.outcome not in OUTCOMES:
            raise CandidateExecutorError("candidate execution outcome is invalid")
        if not isinstance(self.created_regressions, tuple) or any(
                type(item) is not str or not item for item in self.created_regressions):
            raise CandidateExecutorError("candidate execution regressions are invalid")
        if not isinstance(self.obligations, dict) or not isinstance(self.metadata, dict):
            raise CandidateExecutorError("candidate execution objects are invalid")
        if type(self.budget) is not int or not 1 <= self.budget <= 3:
            raise CandidateExecutorError("candidate execution budget is invalid")
        if self.evaluation_only is not True:
            raise CandidateExecutorError("candidate execution must be evaluation-only")

    @property
    def execution_digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.execution_version,
            "case_id": self.case_id, "candidate_id": self.candidate_id,
            "source": self.source, "action_digest": self.action_digest,
            "candidate_digest": self.candidate_digest,
            "compile_result": self.compile_result,
            "functional_result": self.functional_result,
            "signoff_result": self.signoff_result,
            "outcome": self.outcome,
            "created_regressions": list(self.created_regressions),
            "obligations": self.obligations,
            "toolchain_digest": self.toolchain_digest,
            "oracle_digest": self.oracle_digest,
            "produced_transition_id": self.produced_transition_id,
            "budget": self.budget, "evaluation_only": self.evaluation_only,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "CandidateExecutionReceipt":
        if not isinstance(payload, Mapping):
            raise CandidateExecutorError("candidate execution receipt must be an object")
        required = {
            "case_id", "candidate_id", "source", "action_digest", "candidate_digest",
            "compile_result", "functional_result", "signoff_result", "outcome",
            "created_regressions", "obligations", "toolchain_digest", "oracle_digest",
            "produced_transition_id", "budget", "evaluation_only", "metadata",
        }
        if not required <= set(payload):
            raise CandidateExecutorError("candidate execution receipt is missing fields")
        receipt = cls(
            case_id=payload["case_id"], candidate_id=payload["candidate_id"],
            source=payload["source"], action_digest=payload["action_digest"],
            compile_result=payload["compile_result"], functional_result=payload["functional_result"],
            signoff_result=payload["signoff_result"], outcome=payload["outcome"],
            created_regressions=tuple(payload["created_regressions"]),
            obligations=dict(payload["obligations"]), toolchain_digest=payload["toolchain_digest"],
            oracle_digest=payload["oracle_digest"],
            produced_transition_id=payload["produced_transition_id"],
            candidate_digest=payload["candidate_digest"], budget=payload["budget"],
            execution_version=payload.get("version", EXECUTOR_VERSION),
            evaluation_only=payload["evaluation_only"], metadata=dict(payload["metadata"]))
        supplied = payload.get("execution_digest")
        if supplied is not None and supplied != receipt.execution_digest:
            raise CandidateExecutorError("candidate execution receipt digest mismatch")
        return receipt


@dataclass(frozen=True)
class PairedCandidateExecutionReceipt:
    """Four-arm same-case execution bundle for P12 attribution."""

    case_id: str
    arm_receipts: dict[str, CandidateExecutionReceipt]
    candidate_budget: int
    case_digest: str
    toolchain_digest: str
    oracle_digest: str
    paired: bool = True
    evaluation_only: bool = True
    reasons: tuple[str, ...] = ()
    no_skill_reason: str | None = None
    state_shift_receipt_id: str | None = None
    risk_receipt_id: str | None = None
    lineage_id: str | None = None

    def __post_init__(self) -> None:
        _text(self.case_id, "case_id")
        if not isinstance(self.arm_receipts, dict):
            raise CandidateExecutorError("paired arm_receipts must be an object")
        if set(self.arm_receipts) != set(P12_ARMS):
            raise CandidateExecutorError("paired execution requires exactly four P12 arms")
        if any(not isinstance(value, CandidateExecutionReceipt)
               for value in self.arm_receipts.values()):
            raise CandidateExecutorError("paired arm receipt is invalid")
        if type(self.candidate_budget) is not int or not 1 <= self.candidate_budget <= 3:
            raise CandidateExecutorError("paired candidate budget is invalid")
        _text(self.case_digest, "case_digest")
        _text(self.toolchain_digest, "toolchain_digest")
        _text(self.oracle_digest, "oracle_digest")
        if self.paired is not True or self.evaluation_only is not True:
            raise CandidateExecutorError("paired execution must be evaluation-only")
        if any(value.case_id != self.case_id or
               value.budget != self.candidate_budget
               for value in self.arm_receipts.values()):
            raise CandidateExecutorError("paired execution case or budget mismatch")
        if self.arm_receipts["NO_MEMORY"].source != "no_memory":
            raise CandidateExecutorError("NO_MEMORY arm must not contain a memory candidate")
        if any(self.arm_receipts[arm].source != "structured_memory"
               for arm in P12_ARMS[1:]):
            raise CandidateExecutorError("memory arms must contain structured candidates")
        if any(value.toolchain_digest != self.toolchain_digest or
               value.oracle_digest != self.oracle_digest
               for value in self.arm_receipts.values()):
            raise CandidateExecutorError("paired execution toolchain/oracle digest mismatch")
        if not isinstance(self.reasons, tuple) or any(
                type(item) is not str or not item for item in self.reasons):
            raise CandidateExecutorError("paired execution reasons are invalid")
        if self.no_skill_reason is not None and self.no_skill_reason not in NO_SKILL_REASONS:
            raise CandidateExecutorError("paired execution no_skill_reason is invalid")
        for name in ("state_shift_receipt_id", "risk_receipt_id"):
            value = getattr(self, name)
            if value is not None and (type(value) is not str or not value):
                raise CandidateExecutorError(f"paired execution {name} is invalid")
        if self.lineage_id is not None and (
                type(self.lineage_id) is not str or
                not self.lineage_id or self.lineage_id != self.lineage_id.strip()):
            raise CandidateExecutorError("paired execution lineage_id is invalid")
        if self.state_shift_receipt_id is not None and self.no_skill_reason != "STATE_SHIFT":
            raise CandidateExecutorError("paired state shift receipt requires STATE_SHIFT")
        if self.risk_receipt_id is not None and self.no_skill_reason != "RISK":
            raise CandidateExecutorError("paired risk receipt requires RISK")

    @property
    def receipt_digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def legacy_receipt_digest(self) -> str:
        """Digest accepted for paired receipts written before reason metadata."""
        payload = {
            "version": EXECUTOR_VERSION, "case_id": self.case_id,
            "arm_receipts": {arm: receipt.to_dict()
                             for arm, receipt in sorted(self.arm_receipts.items())},
            "candidate_budget": self.candidate_budget,
            "case_digest": self.case_digest,
            "toolchain_digest": self.toolchain_digest,
            "oracle_digest": self.oracle_digest,
            "paired": self.paired, "evaluation_only": self.evaluation_only,
            "reasons": list(self.reasons),
        }
        # Legacy arm receipts did not include reason metadata because that
        # metadata belongs to the paired routing decision, not each arm.
        for arm, receipt in sorted(self.arm_receipts.items()):
            payload["arm_receipts"][arm] = receipt.to_dict()
        # ``lineage_id`` was introduced together with reason metadata.
        payload.pop("lineage_id", None)
        return _digest(payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": EXECUTOR_VERSION,
            "case_id": self.case_id,
            "arm_receipts": {arm: receipt.to_dict()
                             for arm, receipt in sorted(self.arm_receipts.items())},
            "candidate_budget": self.candidate_budget,
            "case_digest": self.case_digest,
            "toolchain_digest": self.toolchain_digest,
            "oracle_digest": self.oracle_digest,
            "paired": self.paired, "evaluation_only": self.evaluation_only,
            "reasons": list(self.reasons),
            "no_skill_reason": self.no_skill_reason,
            "state_shift_receipt_id": self.state_shift_receipt_id,
            "risk_receipt_id": self.risk_receipt_id,
            "lineage_id": self.lineage_id,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "PairedCandidateExecutionReceipt":
        if not isinstance(payload, Mapping):
            raise CandidateExecutorError("paired execution receipt must be an object")
        arms = payload.get("arm_receipts")
        if not isinstance(arms, Mapping):
            raise CandidateExecutorError("paired arm receipts are missing")
        receipt = cls(
            case_id=payload.get("case_id"),
            arm_receipts={str(arm): CandidateExecutionReceipt.from_dict(value)
                          for arm, value in arms.items()},
            candidate_budget=payload.get("candidate_budget"),
            case_digest=payload.get("case_digest"),
            toolchain_digest=payload.get("toolchain_digest"),
            oracle_digest=payload.get("oracle_digest"),
            paired=payload.get("paired"), evaluation_only=payload.get("evaluation_only"),
            reasons=tuple(payload.get("reasons", ())),
            no_skill_reason=payload.get("no_skill_reason"),
            state_shift_receipt_id=payload.get("state_shift_receipt_id"),
            risk_receipt_id=payload.get("risk_receipt_id"),
            lineage_id=payload.get("lineage_id"))
        supplied = payload.get("receipt_digest")
        if supplied is not None and supplied not in {
                receipt.receipt_digest, receipt.legacy_receipt_digest}:
            raise CandidateExecutorError("paired execution receipt digest mismatch")
        return receipt


def execute_candidate(
    candidate: StructuredRepairCandidate,
    frozen_case: Mapping,
    oracle: object = None,
    budget: int | Mapping = 3,
) -> CandidateExecutionReceipt:
    """Execute one candidate through an injected evaluation oracle.

    No result is inferred from the candidate action. If no oracle is wired, all
    verdicts remain UNKNOWN and no canonical transition is claimed.
    """
    if not isinstance(candidate, StructuredRepairCandidate):
        raise TypeError("execute_candidate requires StructuredRepairCandidate")
    case = _case_payload(frozen_case)
    case_id = _text(case.get("case_id"), "case_id")
    budget_value, budget_payload = _budget(budget)
    result = _call_oracle(oracle, candidate, case, budget_payload)
    compile_result = _verdict(result.get("compile_result"), "compile_result")
    functional_result = _verdict(result.get("functional_result"), "functional_result")
    raw_signoff = result.get("signoff_result")
    signoff_result = None if raw_signoff is None else _verdict(raw_signoff, "signoff_result")
    outcome = _outcome(result, compile_result, functional_result,
                       signoff_result or "UNKNOWN")
    regressions = result.get("created_regressions") or ()
    if not isinstance(regressions, (list, tuple)) or any(
            type(item) is not str or not item for item in regressions):
        raise CandidateExecutorError("candidate execution created_regressions is invalid")
    obligations = result.get("obligations")
    if obligations is None:
        obligations = {name: "UNKNOWN" for name in candidate.obligations}
    if not isinstance(obligations, Mapping):
        raise CandidateExecutorError("candidate execution obligations must be an object")
    toolchain_digest = result.get("toolchain_digest") or case.get("toolchain_digest") or "UNAVAILABLE"
    oracle_digest = result.get("oracle_digest") or case.get("oracle_digest") or "UNAVAILABLE"
    transition = result.get("produced_transition_id")
    if transition is not None:
        transition = _text(transition, "produced_transition_id")
    action_digest = _digest(candidate.concrete_action)
    metadata = {
        "executor_version": EXECUTOR_VERSION,
        "oracle_available": oracle is not None,
        "oracle_error": result.get("oracle_error"),
        "budget": budget_payload,
        "oracle_metadata": _oracle_metadata(result),
    }
    return CandidateExecutionReceipt(
        case_id=case_id, candidate_id=candidate.candidate_id,
        source="structured_memory", action_digest=action_digest,
        compile_result=compile_result, functional_result=functional_result,
        signoff_result=signoff_result, outcome=outcome,
        created_regressions=tuple(sorted(set(regressions))),
        obligations=dict(obligations), toolchain_digest=_text(toolchain_digest, "toolchain_digest"),
        oracle_digest=_text(oracle_digest, "oracle_digest"),
        produced_transition_id=transition, candidate_digest=candidate.candidate_digest,
        budget=budget_value, metadata=metadata)


def _execute_arm(candidate: StructuredRepairCandidate | None,
                 frozen_case: Mapping, oracle: object, budget: int | Mapping,
                 *, arm: str) -> CandidateExecutionReceipt:
    if arm == "NO_MEMORY":
        case = _case_payload(frozen_case)
        case_id = _text(case.get("case_id"), "case_id")
        budget_value, budget_payload = _budget(budget)
        result = _call_oracle(oracle, None, case, budget_payload)
        compile_result = _verdict(result.get("compile_result"), "compile_result")
        functional_result = _verdict(result.get("functional_result"), "functional_result")
        raw_signoff = result.get("signoff_result")
        signoff_result = None if raw_signoff is None else _verdict(raw_signoff, "signoff_result")
        outcome = _outcome(result, compile_result, functional_result,
                           signoff_result or "UNKNOWN")
        regressions = result.get("created_regressions") or ()
        if not isinstance(regressions, (list, tuple)) or any(
                type(item) is not str or not item for item in regressions):
            raise CandidateExecutorError("candidate execution created_regressions is invalid")
        toolchain_digest = result.get("toolchain_digest") or case.get("toolchain_digest") or "UNAVAILABLE"
        oracle_digest = result.get("oracle_digest") or case.get("oracle_digest") or "UNAVAILABLE"
        return CandidateExecutionReceipt(
            case_id=case_id, candidate_id="no_memory:" + case_id,
            source="no_memory", action_digest=_digest({}),
            compile_result=compile_result, functional_result=functional_result,
            signoff_result=signoff_result, outcome=outcome,
            created_regressions=tuple(sorted(set(regressions))), obligations={},
            toolchain_digest=_text(toolchain_digest, "toolchain_digest"),
            oracle_digest=_text(oracle_digest, "oracle_digest"),
            produced_transition_id=None, candidate_digest=_digest({}),
            budget=budget_value,
            metadata={"executor_version": EXECUTOR_VERSION, "arm": arm,
                      "oracle_available": oracle is not None,
                      "oracle_metadata": _oracle_metadata(result)})
    if not isinstance(candidate, StructuredRepairCandidate):
        raise CandidateExecutorError(f"{arm} arm requires a structured candidate")
    return execute_candidate(candidate, frozen_case, oracle=oracle, budget=budget)


def execute_paired_candidates(
        frozen_case: Mapping,
        arm_candidates: Mapping[str, StructuredRepairCandidate | None],
        oracle: object = None,
        budget: int | Mapping = 3,
        *,
        no_skill_reason: str | None = None,
        state_shift_receipt_id: str | None = None,
        risk_receipt_id: str | None = None,
        lineage_id: str | None = None,
) -> PairedCandidateExecutionReceipt:
    """Execute all four P12 arms on one frozen case and fixed budget.

    The same case and injected oracle are passed to every arm. The returned
    bundle rejects missing arms, memory candidates in NO_MEMORY, budget drift,
    or toolchain/oracle digest drift; it remains an evaluation artifact only.
    """
    _case_payload(frozen_case)
    if not isinstance(arm_candidates, Mapping) or set(arm_candidates) != set(P12_ARMS):
        raise CandidateExecutorError("paired execution requires exactly four P12 arms")
    budget_value, _ = _budget(budget)
    receipts = {
        arm: _execute_arm(arm_candidates[arm], frozen_case, oracle, budget,
                          arm=arm)
        for arm in P12_ARMS
    }
    case = _case_payload(frozen_case)
    case_id = _text(case.get("case_id"), "case_id")
    toolchains = {receipt.toolchain_digest for receipt in receipts.values()}
    oracles = {receipt.oracle_digest for receipt in receipts.values()}
    if len(toolchains) != 1 or len(oracles) != 1:
        raise CandidateExecutorError("paired execution toolchain/oracle digest mismatch")
    return PairedCandidateExecutionReceipt(
        case_id=case_id, arm_receipts=receipts, candidate_budget=budget_value,
        case_digest=_digest(case), toolchain_digest=next(iter(toolchains)),
        oracle_digest=next(iter(oracles)), no_skill_reason=no_skill_reason,
        state_shift_receipt_id=state_shift_receipt_id,
        risk_receipt_id=risk_receipt_id, lineage_id=lineage_id)


__all__ = [
    "EXECUTOR_VERSION", "P12_ARMS", "CandidateExecutorError",
    "CandidateExecutionReceipt", "PairedCandidateExecutionReceipt",
    "execute_candidate", "execute_paired_candidates",
]
