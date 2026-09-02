"""Typed counterexample evidence for the Revision3 evolution plane.

The detector is deliberately downstream of the structured-candidate and
oracle seams.  A failed candidate is not enough: the caller must provide an
explicit historical prediction, an applicable/bound witness, and an oracle
result containing observed mediated effects.  The returned receipt is an
evaluation-only reason witness and has no mutation or promotion authority.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from tehm.evaluation.candidate_executor import CandidateExecutionReceipt
from tehm.ids import stable_dumps
from tehm.retrieval.structured_candidate import StructuredRepairCandidate


COUNTEREXAMPLE_RECEIPT_VERSION = "counterexample-receipt-v0.1"
_GOLD_KEYS = frozenset({"fix", "gold_patch", "repaired_rtl", "heldout_answer"})


def _text(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"counterexample receipt {field} is required")
    return value.strip()


def _mapping(value: object, field: str) -> dict:
    if not isinstance(value, Mapping):
        raise ValueError(f"counterexample receipt {field} must be an object")
    if any(key in _GOLD_KEYS for key in value):
        raise ValueError(f"counterexample receipt {field} contains gold-answer fields")
    try:
        decoded = json.loads(stable_dumps(dict(value)))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"counterexample receipt {field} is not JSON-serializable") from exc
    if not isinstance(decoded, dict):  # pragma: no cover
        raise ValueError(f"counterexample receipt {field} must be an object")
    return decoded


def _mapping_sequence(value: object, field: str) -> tuple[dict, ...]:
    if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
        raise ValueError(f"counterexample receipt {field} must be a sequence")
    result = tuple(_mapping(item, field) for item in value)
    if not result:
        raise ValueError(f"counterexample receipt {field} must not be empty")
    return result


def _strings(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
        raise ValueError(f"counterexample receipt {field} must be a sequence")
    result = tuple(_text(item, field) for item in value)
    if not result:
        raise ValueError(f"counterexample receipt {field} must not be empty")
    if len(set(result)) != len(result):
        raise ValueError(f"counterexample receipt {field} contains duplicates")
    return result


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _execution_receipt_id(execution: CandidateExecutionReceipt) -> str:
    """Stable local ID for an execution receipt (the executor exposes a digest)."""
    return "candidate_execution_" + execution.execution_digest.split(":", 1)[1][:24]


def _effect_keys(values: tuple[dict, ...]) -> frozenset[str]:
    return frozenset(stable_dumps(item) for item in values)


@dataclass(frozen=True)
class CounterexampleReceipt:
    """Immutable witness that a bound memory prediction was contradicted."""

    campaign_id: str
    case_id: str
    resolved_state_id: str
    knowledge_object_id: str
    asset_id: str
    action_family: str
    applicability_receipt_id: str
    applicability_status: str
    binding_receipt_id: str
    binding_status: str
    candidate_id: str
    candidate_digest: str
    action_digest: str
    execution_receipt_id: str
    execution_digest: str
    execution_source: str
    oracle_digest: str
    predicted_outcome: dict
    predicted_effects: tuple[dict, ...]
    observed_outcome: dict
    observed_effects: tuple[dict, ...]
    contradiction_types: tuple[str, ...]
    lineage_id: str
    learner_eligible: bool = True
    oracle_complete: bool = True
    version: str = COUNTEREXAMPLE_RECEIPT_VERSION

    def __post_init__(self) -> None:
        for field in (
                "campaign_id", "case_id", "resolved_state_id",
                "knowledge_object_id", "asset_id", "action_family",
                "applicability_receipt_id", "binding_receipt_id", "candidate_id",
                "candidate_digest", "action_digest", "execution_receipt_id",
                "execution_digest", "execution_source", "oracle_digest",
                "lineage_id"):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        for field in ("candidate_digest", "action_digest", "execution_digest",
                      "oracle_digest"):
            if not getattr(self, field).startswith("sha256:"):
                raise ValueError(f"counterexample receipt {field} must be a sha256 digest")
        if self.applicability_status != "APPLICABLE":
            raise ValueError("counterexample receipt applicability must be APPLICABLE")
        if self.binding_status != "BOUND":
            raise ValueError("counterexample receipt binding must be BOUND")
        if self.execution_source != "structured_memory":
            raise ValueError("counterexample receipt requires structured-memory execution")
        predicted_outcome = _mapping(self.predicted_outcome, "predicted_outcome")
        observed_outcome = _mapping(self.observed_outcome, "observed_outcome")
        predicted_effects = _mapping_sequence(self.predicted_effects, "predicted_effects")
        observed_effects = _mapping_sequence(self.observed_effects, "observed_effects")
        contradictions = _strings(self.contradiction_types, "contradiction_types")
        if type(self.learner_eligible) is not bool:
            raise ValueError("counterexample receipt learner_eligible must be boolean")
        if self.oracle_complete is not True:
            raise ValueError("counterexample receipt requires a complete oracle")
        if not (stable_dumps(predicted_outcome) != stable_dumps(observed_outcome) or
                _effect_keys(predicted_effects) != _effect_keys(observed_effects)):
            raise ValueError("counterexample receipt has no prediction contradiction")
        expected_types = set()
        if stable_dumps(predicted_outcome) != stable_dumps(observed_outcome):
            expected_types.add("OUTCOME_CONTRADICTION")
        if _effect_keys(predicted_effects) != _effect_keys(observed_effects):
            expected_types.add("MEDIATED_EFFECT_CONTRADICTION")
        if not expected_types <= set(contradictions):
            raise ValueError("counterexample receipt contradiction types are incomplete")
        object.__setattr__(self, "predicted_outcome", predicted_outcome)
        object.__setattr__(self, "predicted_effects", predicted_effects)
        object.__setattr__(self, "observed_outcome", observed_outcome)
        object.__setattr__(self, "observed_effects", observed_effects)
        object.__setattr__(self, "contradiction_types", contradictions)
        if self.version != COUNTEREXAMPLE_RECEIPT_VERSION:
            raise ValueError("counterexample receipt version is invalid")

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "campaign_id": self.campaign_id,
            "case_id": self.case_id,
            "resolved_state_id": self.resolved_state_id,
            "knowledge_object_id": self.knowledge_object_id,
            "asset_id": self.asset_id,
            "action_family": self.action_family,
            "applicability_receipt_id": self.applicability_receipt_id,
            "applicability_status": self.applicability_status,
            "binding_receipt_id": self.binding_receipt_id,
            "binding_status": self.binding_status,
            "candidate_id": self.candidate_id,
            "candidate_digest": self.candidate_digest,
            "action_digest": self.action_digest,
            "execution_receipt_id": self.execution_receipt_id,
            "execution_digest": self.execution_digest,
            "execution_source": self.execution_source,
            "oracle_digest": self.oracle_digest,
            "predicted_outcome": self.predicted_outcome,
            "predicted_effects": list(self.predicted_effects),
            "observed_outcome": self.observed_outcome,
            "observed_effects": list(self.observed_effects),
            "contradiction_types": list(self.contradiction_types),
            "lineage_id": self.lineage_id,
            "learner_eligible": self.learner_eligible,
            "oracle_complete": self.oracle_complete,
        }

    @property
    def receipt_digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def receipt_id(self) -> str:
        return "counterexample_" + self.receipt_digest.split(":", 1)[1][:24]

    @classmethod
    def from_dict(cls, payload: object) -> "CounterexampleReceipt":
        if not isinstance(payload, Mapping):
            raise ValueError("counterexample receipt must be an object")
        required = set(cls.__dataclass_fields__)
        if not required <= set(payload):
            raise ValueError("counterexample receipt is missing fields")
        receipt = cls(
            version=payload["version"], campaign_id=payload["campaign_id"],
            case_id=payload["case_id"], resolved_state_id=payload["resolved_state_id"],
            knowledge_object_id=payload["knowledge_object_id"], asset_id=payload["asset_id"],
            action_family=payload["action_family"],
            applicability_receipt_id=payload["applicability_receipt_id"],
            applicability_status=payload["applicability_status"],
            binding_receipt_id=payload["binding_receipt_id"],
            binding_status=payload["binding_status"], candidate_id=payload["candidate_id"],
            candidate_digest=payload["candidate_digest"], action_digest=payload["action_digest"],
            execution_receipt_id=payload["execution_receipt_id"],
            execution_digest=payload["execution_digest"],
            execution_source=payload["execution_source"], oracle_digest=payload["oracle_digest"],
            predicted_outcome=dict(payload["predicted_outcome"]),
            predicted_effects=tuple(payload["predicted_effects"]),
            observed_outcome=dict(payload["observed_outcome"]),
            observed_effects=tuple(payload["observed_effects"]),
            contradiction_types=tuple(payload["contradiction_types"]),
            lineage_id=payload["lineage_id"], learner_eligible=payload["learner_eligible"],
            oracle_complete=payload["oracle_complete"])
        if payload.get("receipt_digest") not in (None, receipt.receipt_digest):
            raise ValueError("counterexample receipt digest mismatch")
        if payload.get("receipt_id") not in (None, receipt.receipt_id):
            raise ValueError("counterexample receipt ID mismatch")
        return receipt


def _prediction(value: object) -> tuple[dict, tuple[dict, ...]]:
    if not isinstance(value, Mapping):
        raise ValueError("counterexample prediction must be an object")
    expected = value.get("expected_outcome")
    effects = value.get("predicted_effects", value.get("mediated_effects"))
    if expected is None or effects is None:
        raise ValueError(
            "counterexample requires explicit expected_outcome and predicted_effects")
    return _mapping(expected, "predicted_outcome"), _mapping_sequence(
        effects, "predicted_effects")


def _witness(value: object, field: str) -> dict:
    if not isinstance(value, Mapping):
        raise ValueError(f"counterexample {field} witness must be an object")
    return dict(value)


def _oracle_observation(execution: CandidateExecutionReceipt) -> tuple[dict, tuple[dict, ...]]:
    if execution.evaluation_only is not True or execution.metadata.get("oracle_available") is not True:
        raise ValueError("counterexample requires a real evaluation oracle")
    oracle_metadata = execution.metadata.get("oracle_metadata")
    if not isinstance(oracle_metadata, Mapping) or oracle_metadata.get("oracle_complete") is not True:
        raise ValueError("counterexample requires a complete oracle metadata witness")
    observed_outcome = oracle_metadata.get("observed_outcome")
    observed_effects = oracle_metadata.get("observed_effects",
                                            oracle_metadata.get("observed_mediated_effects"))
    if observed_outcome is None or observed_effects is None:
        raise ValueError(
            "counterexample oracle must expose observed_outcome and observed_effects")
    outcome = _mapping(observed_outcome, "observed_outcome")
    if outcome.get("outcome") != execution.outcome:
        raise ValueError("counterexample observed outcome is not bound to execution")
    return outcome, _mapping_sequence(observed_effects, "observed_effects")


def detect_counterexample(
        candidate: StructuredRepairCandidate,
        execution: CandidateExecutionReceipt,
        *, prediction: Mapping,
        applicability: Mapping,
        binding: Mapping,
        campaign_id: str,
        lineage_id: str,
        learner_eligible: bool = True,
) -> CounterexampleReceipt | None:
    """Detect a contradiction only from fully bound candidate/oracle evidence."""
    if not isinstance(candidate, StructuredRepairCandidate):
        raise TypeError("counterexample candidate must be StructuredRepairCandidate")
    if not isinstance(execution, CandidateExecutionReceipt):
        raise TypeError("counterexample execution must be CandidateExecutionReceipt")
    campaign_id = _text(campaign_id, "campaign_id")
    lineage_id = _text(lineage_id, "lineage_id")
    if type(learner_eligible) is not bool:
        raise ValueError("counterexample learner_eligible must be boolean")
    if (execution.case_id != candidate.provenance.get("case_id", execution.case_id) or
            execution.candidate_id != candidate.candidate_id or
            execution.candidate_digest != candidate.candidate_digest or
            execution.source != "structured_memory"):
        raise ValueError("counterexample execution is not bound to candidate")
    app = _witness(applicability, "applicability")
    if (app.get("status") != "APPLICABLE" or
            app.get("receipt_id", app.get("applicability_receipt_id")) !=
            candidate.applicability_receipt_id):
        raise ValueError("counterexample applicability witness is invalid")
    bind = _witness(binding, "binding")
    if (bind.get("status") != "BOUND" or
            bind.get("receipt_id", bind.get("binding_receipt_id")) !=
            candidate.binding_receipt_id or
            bind.get("candidate_digest") != candidate.candidate_digest or
            bind.get("action_digest") != execution.action_digest):
        raise ValueError("counterexample binding witness is invalid")
    predicted_outcome, predicted_effects = _prediction(prediction)
    observed_outcome, observed_effects = _oracle_observation(execution)
    contradiction_types: list[str] = []
    if stable_dumps(predicted_outcome) != stable_dumps(observed_outcome):
        contradiction_types.append("OUTCOME_CONTRADICTION")
    if _effect_keys(predicted_effects) != _effect_keys(observed_effects):
        contradiction_types.append("MEDIATED_EFFECT_CONTRADICTION")
    if not contradiction_types:
        return None
    return CounterexampleReceipt(
        campaign_id=campaign_id, case_id=execution.case_id,
        resolved_state_id=candidate.resolved_state_id,
        knowledge_object_id=candidate.knowledge_object_id, asset_id=candidate.asset_id,
        action_family=candidate.action_family,
        applicability_receipt_id=candidate.applicability_receipt_id,
        applicability_status="APPLICABLE", binding_receipt_id=candidate.binding_receipt_id,
        binding_status="BOUND", candidate_id=candidate.candidate_id,
        candidate_digest=candidate.candidate_digest, action_digest=execution.action_digest,
        execution_receipt_id=_execution_receipt_id(execution),
        execution_digest=execution.execution_digest,
        execution_source=execution.source, oracle_digest=execution.oracle_digest,
        predicted_outcome=predicted_outcome, predicted_effects=predicted_effects,
        observed_outcome=observed_outcome, observed_effects=observed_effects,
        contradiction_types=tuple(contradiction_types), lineage_id=lineage_id,
        learner_eligible=learner_eligible, oracle_complete=True)


__all__ = ["COUNTEREXAMPLE_RECEIPT_VERSION", "CounterexampleReceipt",
           "detect_counterexample"]
