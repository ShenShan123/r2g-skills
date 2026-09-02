"""Reason-specific admission gates for the P13 shadow lane (Revision3)."""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from tehm.evaluation.candidate_executor import (
    P12_ARMS, CandidateExecutionReceipt, PairedCandidateExecutionReceipt,
)
from tehm.ids import stable_dumps
from tehm.state.shift_receipts import StateShiftReceipt
from tehm.assets.receipts import CapabilityGapReceipt
from .conflict import ConflictReceipt
from .novelty import NoveltyReceipt

from .reason_derivation import (
    EVOLUTION_REASONS, EvolutionReasonDerivationError,
    EvolutionReasonDerivationReceipt, derive_capability_gap_reason,
    derive_conflict_reason, derive_memory_interference_reason,
    derive_novelty_reason, derive_state_shift_reason,
)


EVOLUTION_ADMISSION_VERSION = "evolution-admission-v0.1"


class EvolutionAdmissionError(ValueError):
    """An admission request or receipt is malformed."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _text(value: object, field_name: str) -> str:
    if type(value) is not str or not value.strip():
        raise EvolutionAdmissionError(
            f"evolution admission {field_name} is required")
    return value.strip()


def _strings(value: object, field_name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
        raise EvolutionAdmissionError(
            f"evolution admission {field_name} must be a sequence")
    values = tuple(_text(item, field_name) for item in value)
    if not allow_empty and not values:
        raise EvolutionAdmissionError(
            f"evolution admission {field_name} must not be empty")
    if len(set(values)) != len(values):
        raise EvolutionAdmissionError(
            f"evolution admission {field_name} contains duplicates")
    return values


def _unique(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


@dataclass(frozen=True)
class EvolutionAdmissionReceipt:
    """Content-addressed result of a reason-specific evidence gate."""

    campaign_id: str
    case_id: str
    reason: str
    derivation_receipt_ids: tuple[str, ...]
    evidence_receipt_ids: tuple[str, ...]
    required_evidence: tuple[str, ...]
    satisfied_evidence: tuple[str, ...]
    admitted: bool
    blocked_reason: str | None = None
    evaluation_only: bool = True
    canonical_memory_mutation: str = "none"
    version: str = EVOLUTION_ADMISSION_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "campaign_id", _text(self.campaign_id, "campaign_id"))
        object.__setattr__(self, "case_id", _text(self.case_id, "case_id"))
        if self.reason not in EVOLUTION_REASONS:
            raise EvolutionAdmissionError("evolution admission reason is invalid")
        if self.version != EVOLUTION_ADMISSION_VERSION:
            raise EvolutionAdmissionError("evolution admission version is invalid")
        derivations = _strings(self.derivation_receipt_ids, "derivation_receipt_ids")
        evidence = _strings(self.evidence_receipt_ids, "evidence_receipt_ids")
        required = _strings(self.required_evidence, "required_evidence", allow_empty=True)
        satisfied = _strings(self.satisfied_evidence, "satisfied_evidence", allow_empty=True)
        if not isinstance(self.admitted, bool):
            raise EvolutionAdmissionError("evolution admission admitted must be boolean")
        if self.blocked_reason is not None:
            object.__setattr__(self, "blocked_reason", _text(
                self.blocked_reason, "blocked_reason"))
        if self.admitted:
            if self.blocked_reason is not None:
                raise EvolutionAdmissionError(
                    "admitted evolution cannot have blocked_reason")
            if not set(required) <= set(satisfied):
                raise EvolutionAdmissionError(
                    "admitted evolution lacks required evidence")
        elif self.blocked_reason is None:
            raise EvolutionAdmissionError(
                "blocked evolution requires blocked_reason")
        if self.evaluation_only is not True:
            raise EvolutionAdmissionError("evolution admission must be evaluation-only")
        if self.canonical_memory_mutation != "none":
            raise EvolutionAdmissionError(
                "evolution admission cannot mutate canonical memory")
        object.__setattr__(self, "derivation_receipt_ids", derivations)
        object.__setattr__(self, "evidence_receipt_ids", evidence)
        object.__setattr__(self, "required_evidence", required)
        object.__setattr__(self, "satisfied_evidence", satisfied)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "campaign_id": self.campaign_id,
            "case_id": self.case_id,
            "reason": self.reason,
            "derivation_receipt_ids": list(self.derivation_receipt_ids),
            "evidence_receipt_ids": list(self.evidence_receipt_ids),
            "required_evidence": list(self.required_evidence),
            "satisfied_evidence": list(self.satisfied_evidence),
            "admitted": self.admitted,
            "blocked_reason": self.blocked_reason,
            "evaluation_only": self.evaluation_only,
            "canonical_memory_mutation": self.canonical_memory_mutation,
        }

    @property
    def receipt_digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def receipt_id(self) -> str:
        return "evolution_admission_" + self.receipt_digest.split(":", 1)[1][:24]

    @classmethod
    def from_dict(cls, payload: object) -> "EvolutionAdmissionReceipt":
        if not isinstance(payload, Mapping):
            raise EvolutionAdmissionError("evolution admission receipt must be an object")
        required = {
            "version", "campaign_id", "case_id", "reason",
            "derivation_receipt_ids", "evidence_receipt_ids",
            "required_evidence", "satisfied_evidence", "admitted",
            "blocked_reason", "evaluation_only", "canonical_memory_mutation",
        }
        if not required <= set(payload):
            raise EvolutionAdmissionError("evolution admission receipt is missing fields")
        receipt = cls(
            version=payload["version"], campaign_id=payload["campaign_id"],
            case_id=payload["case_id"], reason=payload["reason"],
            derivation_receipt_ids=tuple(payload["derivation_receipt_ids"]),
            evidence_receipt_ids=tuple(payload["evidence_receipt_ids"]),
            required_evidence=tuple(payload["required_evidence"]),
            satisfied_evidence=tuple(payload["satisfied_evidence"]),
            admitted=payload["admitted"], blocked_reason=payload["blocked_reason"],
            evaluation_only=payload["evaluation_only"],
            canonical_memory_mutation=payload["canonical_memory_mutation"],
        )
        supplied = payload.get("receipt_digest")
        if supplied is not None and supplied != receipt.receipt_digest:
            raise EvolutionAdmissionError("evolution admission receipt digest mismatch")
        supplied_id = payload.get("receipt_id")
        if supplied_id is not None and supplied_id != receipt.receipt_id:
            raise EvolutionAdmissionError("evolution admission receipt ID mismatch")
        return receipt


def _oracle_complete(receipt: CandidateExecutionReceipt) -> bool:
    return (
        receipt.evaluation_only is True and
        receipt.metadata.get("oracle_available") is True and
        receipt.compile_result != "UNKNOWN" and
        receipt.functional_result != "UNKNOWN" and
        receipt.signoff_result not in {None, "UNKNOWN"} and
        receipt.outcome != "UNKNOWN")


def _blocked(derivation: EvolutionReasonDerivationReceipt, *,
             required: tuple[str, ...], satisfied: tuple[str, ...],
             evidence: tuple[str, ...], reason: str) -> EvolutionAdmissionReceipt:
    return EvolutionAdmissionReceipt(
        campaign_id=derivation.campaign_id, case_id=derivation.case_id,
        reason=derivation.reason, derivation_receipt_ids=(derivation.receipt_id,),
        evidence_receipt_ids=_unique(evidence), required_evidence=required,
        satisfied_evidence=satisfied, admitted=False, blocked_reason=reason)


def admit_evolution_reason(
        derivation: EvolutionReasonDerivationReceipt, *, campaign_id: str,
        learner_eligible: bool, paired: PairedCandidateExecutionReceipt | None = None,
        state_shift: StateShiftReceipt | Mapping | None = None,
        capability_gap: CapabilityGapReceipt | Mapping | None = None,
        failure_transition_ids: tuple[str, ...] | list[str] | None = None,
        novelty: NoveltyReceipt | Mapping | None = None,
        conflict: ConflictReceipt | Mapping | None = None,
        routing: MemoryRoutingDecision | None = None,
        memory_arm: str = "ALWAYS_MEMORY") -> EvolutionAdmissionReceipt:
    """Apply a reason-specific P13 gate without accepting a mutation plan."""
    if not isinstance(derivation, EvolutionReasonDerivationReceipt):
        raise EvolutionAdmissionError("evolution admission derivation is invalid")
    campaign_id = _text(campaign_id, "campaign_id")
    if derivation.campaign_id != campaign_id:
        raise EvolutionAdmissionError("evolution admission campaign mismatch")
    if type(learner_eligible) is not bool:
        raise EvolutionAdmissionError("evolution admission learner_eligible must be boolean")
    if memory_arm not in P12_ARMS[1:]:
        raise EvolutionAdmissionError("evolution admission memory_arm is invalid")
    evidence = (derivation.receipt_id, *derivation.input_receipt_ids)

    if derivation.reason == "STATE_SHIFT":
        required = ("learner_eligible", "typed_state_shift", "typed_route",
                    "paired_counterfactual")
        satisfied: list[str] = []
        if learner_eligible:
            satisfied.append("learner_eligible")
        if state_shift is not None and routing is not None:
            try:
                checked = derive_state_shift_reason(
                    state_shift, campaign_id=campaign_id, case_id=derivation.case_id,
                    routing=routing, lineage_id=derivation.lineage_ids[0])
            except EvolutionReasonDerivationError:
                checked = None
            if checked is not None and checked.receipt_digest == derivation.receipt_digest:
                satisfied.extend(("typed_state_shift", "typed_route"))
        if (paired is not None and paired.case_id == derivation.case_id and
                paired.routing_decision == "NO_SKILL" and
                paired.no_skill_reason == "STATE_SHIFT" and
                _oracle_complete(paired.arm_receipts["NO_MEMORY"]) and
                memory_arm in P12_ARMS[1:] and
                _oracle_complete(paired.arm_receipts[memory_arm]) and
                paired.arm_receipts[memory_arm].source == "structured_memory"):
            satisfied.append("paired_counterfactual")
            evidence = (*evidence, paired.receipt_digest,
                        paired.arm_receipts["NO_MEMORY"].execution_digest,
                        paired.arm_receipts[memory_arm].execution_digest)
        if not learner_eligible:
            return _blocked(derivation, required=required,
                            satisfied=tuple(satisfied), evidence=tuple(evidence),
                            reason="not_learner_eligible")
        if "typed_state_shift" not in satisfied:
            return _blocked(derivation, required=required,
                            satisfied=tuple(satisfied), evidence=tuple(evidence),
                            reason="missing_typed_state_shift")
        if "paired_counterfactual" not in satisfied:
            return _blocked(derivation, required=required,
                            satisfied=tuple(satisfied), evidence=tuple(evidence),
                            reason="missing_paired_counterfactual")
        return EvolutionAdmissionReceipt(
            campaign_id=campaign_id, case_id=derivation.case_id,
            reason=derivation.reason, derivation_receipt_ids=(derivation.receipt_id,),
            evidence_receipt_ids=_unique(evidence), required_evidence=required,
            satisfied_evidence=tuple(satisfied), admitted=True)

    if derivation.reason == "MEMORY_INTERFERENCE":
        required = ("learner_eligible", "paired_counterfactual",
                    "memory_interference_derivation")
        satisfied: list[str] = []
        if learner_eligible:
            satisfied.append("learner_eligible")
        if paired is not None and paired.case_id == derivation.case_id:
            try:
                checked = derive_memory_interference_reason(
                    paired, campaign_id=campaign_id, memory_arm=memory_arm)
            except EvolutionReasonDerivationError:
                checked = None
            if checked is not None and checked.receipt_digest == derivation.receipt_digest:
                satisfied.extend(("paired_counterfactual",
                                  "memory_interference_derivation"))
                evidence = (*evidence, paired.receipt_digest,
                            paired.arm_receipts["NO_MEMORY"].execution_digest,
                            paired.arm_receipts[memory_arm].execution_digest)
        if not learner_eligible:
            return _blocked(derivation, required=required,
                            satisfied=tuple(satisfied), evidence=tuple(evidence),
                            reason="not_learner_eligible")
        if "memory_interference_derivation" not in satisfied:
            return _blocked(derivation, required=required,
                            satisfied=tuple(satisfied), evidence=tuple(evidence),
                            reason="missing_memory_interference_evidence")
        return EvolutionAdmissionReceipt(
            campaign_id=campaign_id, case_id=derivation.case_id,
            reason=derivation.reason, derivation_receipt_ids=(derivation.receipt_id,),
            evidence_receipt_ids=_unique(evidence), required_evidence=required,
            satisfied_evidence=tuple(satisfied), admitted=True)

    if derivation.reason == "CAPABILITY_GAP":
        required = (
            "learner_eligible", "typed_capability_gap",
            "repeated_independent_failures", "no_eligible_current_asset",
            "no_successful_current_action_family", "no_skill_no_match_route",
        )
        satisfied: list[str] = []
        if learner_eligible:
            satisfied.append("learner_eligible")
        checked_gap = None
        checked_derivation = None
        if capability_gap is not None:
            try:
                checked_gap = (capability_gap if isinstance(
                    capability_gap, CapabilityGapReceipt) else
                    CapabilityGapReceipt.from_dict(capability_gap))
                checked_derivation = derive_capability_gap_reason(
                    checked_gap, campaign_id=campaign_id,
                    case_id=derivation.case_id,
                    failure_transition_ids=failure_transition_ids,
                    routing=routing)
            except (EvolutionReasonDerivationError, TypeError, ValueError):
                checked_derivation = None
        if (checked_gap is not None and checked_derivation is not None and
                checked_derivation.receipt_digest == derivation.receipt_digest):
            satisfied.append("typed_capability_gap")
            satisfied.append("no_skill_no_match_route")
            evidence = (*evidence, checked_gap.receipt_id,
                        checked_gap.receipt_digest)
            coverage = checked_gap.current_action_coverage
            failure_count = coverage.get("failure_evidence", 0)
            try:
                enough_failures = int(failure_count) >= 2
            except (TypeError, ValueError):
                enough_failures = False
            if enough_failures and len(checked_gap.evidence_lineages) >= 2:
                satisfied.append("repeated_independent_failures")
            if (not coverage.get("promoted_asset", False) and
                    not coverage.get("promoted_rule", False)):
                satisfied.append("no_eligible_current_asset")
            if not coverage.get("successful_action_family", False) and not (
                    coverage.get("successful_action_families") or ()):
                satisfied.append("no_successful_current_action_family")
            evidence = (*evidence, *checked_derivation.input_receipt_ids)
        if not learner_eligible:
            return _blocked(derivation, required=required,
                            satisfied=tuple(satisfied), evidence=tuple(evidence),
                            reason="not_learner_eligible")
        if "typed_capability_gap" not in satisfied:
            return _blocked(derivation, required=required,
                            satisfied=tuple(satisfied), evidence=tuple(evidence),
                            reason="missing_typed_capability_gap")
        if "no_skill_no_match_route" not in satisfied:
            return _blocked(derivation, required=required,
                            satisfied=tuple(satisfied), evidence=tuple(evidence),
                            reason="missing_no_skill_no_match_route")
        if "repeated_independent_failures" not in satisfied:
            return _blocked(derivation, required=required,
                            satisfied=tuple(satisfied), evidence=tuple(evidence),
                            reason="missing_repeated_independent_failures")
        if "no_eligible_current_asset" not in satisfied:
            return _blocked(derivation, required=required,
                            satisfied=tuple(satisfied), evidence=tuple(evidence),
                            reason="eligible_current_asset_exists")
        if "no_successful_current_action_family" not in satisfied:
            return _blocked(derivation, required=required,
                            satisfied=tuple(satisfied), evidence=tuple(evidence),
                            reason="successful_current_action_family_exists")
        return EvolutionAdmissionReceipt(
            campaign_id=campaign_id, case_id=derivation.case_id,
            reason=derivation.reason, derivation_receipt_ids=(derivation.receipt_id,),
            evidence_receipt_ids=_unique(evidence), required_evidence=required,
            satisfied_evidence=tuple(satisfied), admitted=True)

    if derivation.reason == "NOVELTY":
        required = ("learner_eligible", "typed_novelty", "no_existing_learner_path")
        satisfied: list[str] = []
        if learner_eligible:
            satisfied.append("learner_eligible")
        checked_novelty = None
        checked_derivation = None
        if novelty is not None:
            try:
                checked_novelty = (novelty if isinstance(novelty, NoveltyReceipt)
                                   else NoveltyReceipt.from_dict(novelty))
                checked_derivation = derive_novelty_reason(
                    checked_novelty, campaign_id=campaign_id,
                    case_id=derivation.case_id)
            except (EvolutionReasonDerivationError, TypeError, ValueError):
                checked_derivation = None
        if (checked_novelty is not None and checked_derivation is not None and
                checked_derivation.receipt_digest == derivation.receipt_digest):
            satisfied.append("typed_novelty")
            if (not checked_novelty.path_exists and
                    checked_novelty.status == "NOVEL_MECHANISM"):
                satisfied.append("no_existing_learner_path")
            evidence = (*evidence, checked_novelty.receipt_id,
                        *checked_derivation.input_receipt_ids)
        if not learner_eligible:
            return _blocked(derivation, required=required,
                            satisfied=tuple(satisfied), evidence=tuple(evidence),
                            reason="not_learner_eligible")
        if "typed_novelty" not in satisfied:
            return _blocked(derivation, required=required,
                            satisfied=tuple(satisfied), evidence=tuple(evidence),
                            reason="missing_typed_novelty")
        if "no_existing_learner_path" not in satisfied:
            return _blocked(derivation, required=required,
                            satisfied=tuple(satisfied), evidence=tuple(evidence),
                            reason="existing_learner_path")
        return EvolutionAdmissionReceipt(
            campaign_id=campaign_id, case_id=derivation.case_id,
            reason=derivation.reason, derivation_receipt_ids=(derivation.receipt_id,),
            evidence_receipt_ids=_unique(evidence), required_evidence=required,
            satisfied_evidence=tuple(satisfied), admitted=True)

    if derivation.reason == "CONFLICT":
        required = ("learner_eligible", "typed_conflict",
                    "independent_conflicting_evidence")
        satisfied: list[str] = []
        if learner_eligible:
            satisfied.append("learner_eligible")
        checked_conflict = None
        checked_derivation = None
        if conflict is not None:
            try:
                checked_conflict = (conflict if isinstance(conflict, ConflictReceipt)
                                    else ConflictReceipt.from_dict(conflict))
                checked_derivation = derive_conflict_reason(
                    checked_conflict, campaign_id=campaign_id,
                    case_id=derivation.case_id)
            except (EvolutionReasonDerivationError, TypeError, ValueError):
                checked_derivation = None
        if (checked_conflict is not None and checked_derivation is not None and
                checked_derivation.receipt_digest == derivation.receipt_digest):
            satisfied.append("typed_conflict")
            if checked_conflict.has_conflict and checked_conflict.evidence_transition_ids:
                satisfied.append("independent_conflicting_evidence")
            evidence = (*evidence, checked_conflict.receipt_id,
                        *checked_derivation.input_receipt_ids)
        if not learner_eligible:
            return _blocked(derivation, required=required,
                            satisfied=tuple(satisfied), evidence=tuple(evidence),
                            reason="not_learner_eligible")
        if "typed_conflict" not in satisfied:
            return _blocked(derivation, required=required,
                            satisfied=tuple(satisfied), evidence=tuple(evidence),
                            reason="missing_typed_conflict")
        if "independent_conflicting_evidence" not in satisfied:
            return _blocked(derivation, required=required,
                            satisfied=tuple(satisfied), evidence=tuple(evidence),
                            reason="missing_conflicting_evidence")
        return EvolutionAdmissionReceipt(
            campaign_id=campaign_id, case_id=derivation.case_id,
            reason=derivation.reason, derivation_receipt_ids=(derivation.receipt_id,),
            evidence_receipt_ids=_unique(evidence), required_evidence=required,
            satisfied_evidence=tuple(satisfied), admitted=True)

    return _blocked(
        derivation, required=("learner_eligible", "supported_reason"),
        satisfied=("learner_eligible",) if learner_eligible else (),
        evidence=evidence, reason="reason_not_supported_by_admission_v0")


__all__ = [
    "EVOLUTION_ADMISSION_VERSION", "EvolutionAdmissionError",
    "EvolutionAdmissionReceipt", "admit_evolution_reason",
]
