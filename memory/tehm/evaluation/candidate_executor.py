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
from tehm.ids import stable_dumps
from tehm.retrieval.structured_candidate import StructuredRepairCandidate


EXECUTOR_VERSION = "candidate-executor-v0.1"
_VERDICTS = frozenset({"PASS", "FAIL", "UNKNOWN"})
_GOLD_KEYS = frozenset({"fix", "gold_patch", "repaired_rtl", "heldout_answer"})


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
        if self.source != "structured_memory":
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


__all__ = [
    "EXECUTOR_VERSION", "CandidateExecutorError", "CandidateExecutionReceipt",
    "execute_candidate",
]
