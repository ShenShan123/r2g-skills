"""Typed bridge from a real P12 cohort to the P13 shadow lane.

P12 produces four-arm execution receipts, while P13 consumes an explicit
shadow-update witness.  This module joins those planes without inferring a
capability gain or a failure cause: it only allows a replayable trigger when
the frozen cohort, lineage, routing, both oracle outcomes, and an explicit
Revision2 evolution signal are complete.
The trigger is evaluation/shadow-only and never writes canonical memory.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tehm.canonical.transition import OUTCOMES
from contracts import MEMORY_ROUTING_DECISIONS, MemoryRoutingDecision
from tehm.evaluation.candidate_executor import (
    P12_ARMS, CandidateExecutionReceipt, PairedCandidateExecutionReceipt,
)
from tehm.ids import stable_dumps


P12_SHADOW_TRIGGER_VERSION = "p12-shadow-trigger-v0.2"
_LEGACY_P12_SHADOW_TRIGGER_VERSION = "p12-shadow-trigger-v0.1"
_MEMORY_ARMS = P12_ARMS[1:]
P13_EVOLUTION_REASONS = frozenset({
    "NOVELTY", "CONFLICT", "COUNTEREXAMPLE", "REPEATED_FAILURE",
    "CAPABILITY_GAP", "MEMORY_INTERFERENCE", "STATE_SHIFT",
})
P13_EVOLUTION_REASON_RECEIPT_VERSION = "p13-evolution-reason-receipt-v1"
_TRIGGER_REASONS = frozenset({
    "oracle_complete",
    "no_evolution_signal",
    "not_learner_eligible",
    "missing_routing_receipt",
    "missing_routing_decision",
    "routing_not_memory_eligible",
    "baseline_oracle_incomplete",
    "memory_oracle_incomplete",
})


class P12ShadowTriggerError(ValueError):
    """A P12-to-P13 shadow trigger is malformed or unsafe to consume."""


def _reason_evidence_refs(value: object) -> tuple[dict[str, str], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise P12ShadowTriggerError(
            "P13 evolution reason evidence_refs must be a non-empty sequence")
    refs: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise P12ShadowTriggerError(
                "P13 evolution reason evidence_ref must be an object")
        path = _text(item.get("path"), "evolution reason evidence_ref path")
        digest = _digest_text(
            item.get("sha256", item.get("digest")),
            "evolution reason evidence_ref sha256")
        ref = {"path": path, "sha256": digest}
        if item.get("id") is not None:
            ref["id"] = _text(item.get("id"), "evolution reason evidence_ref id")
        key = stable_dumps(ref)
        if key in seen:
            raise P12ShadowTriggerError(
                "P13 evolution reason evidence_refs contain duplicates")
        seen.add(key)
        refs.append(ref)
    return tuple(refs)


def _reason_map(value: object) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, Mapping) or not value:
        raise P12ShadowTriggerError(
            "P13 evolution reasons must be a non-empty object")
    result: dict[str, tuple[str, ...]] = {}
    for case_id, raw in value.items():
        case_id = _text(case_id, "evolution reason case_id")
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
            raise P12ShadowTriggerError(
                f"P13 evolution reasons for {case_id} must be non-empty")
        reasons = tuple(sorted(_text(item, "evolution reason") for item in raw))
        if any(item not in P13_EVOLUTION_REASONS for item in reasons):
            raise P12ShadowTriggerError(
                f"P13 evolution reasons for {case_id} are invalid")
        if len(set(reasons)) != len(reasons):
            raise P12ShadowTriggerError(
                f"P13 evolution reasons for {case_id} contain duplicates")
        result[case_id] = reasons
    return dict(sorted(result.items()))


def _case_reason_evidence_refs(
        value: object, case_ids: Sequence[str],
        *, error_prefix: str = "P13 evolution reason") -> dict[str, tuple[dict[str, str], ...]]:
    """Normalize optional evidence refs bound to each individual case.

    Global evidence refs remain supported for legacy receipts, but a new
    receipt may additionally provide this map to prove which immutable event
    supports each case's reason.  The map is deliberately exact: missing or
    extra case IDs would make provenance ambiguous at replay time.
    """
    if not isinstance(value, Mapping):
        raise P12ShadowTriggerError(
            f"{error_prefix} case_evidence_refs must be an object")
    expected = set(case_ids)
    if set(value) != expected:
        raise P12ShadowTriggerError(
            f"{error_prefix} case_evidence_refs must cover exactly all cases")
    result: dict[str, tuple[dict[str, str], ...]] = {}
    for case_id in sorted(expected):
        raw = value[case_id]
        result[case_id] = _reason_evidence_refs(raw)
    return result


@dataclass(frozen=True)
class P13EvolutionReasonReceipt:
    """Content-addressed, externally sourced P13 evolution signals.

    This receipt is a provenance boundary, not a label generator.  It binds
    explicit per-case reasons to the exact P12 cohort and immutable evidence
    references; it never reads execution outcomes or grants mutation authority.
    """

    campaign_id: str
    cohort_receipt_digest: str
    label_source: str
    evidence_refs: tuple[dict[str, str], ...]
    evolution_reasons: dict[str, tuple[str, ...]]
    case_evidence_refs: dict[str, tuple[dict[str, str], ...]] | None = None
    evaluation_only: bool = True
    canonical_memory_mutation: str = "none"
    version: str = P13_EVOLUTION_REASON_RECEIPT_VERSION

    def __post_init__(self) -> None:
        _text(self.campaign_id, "evolution reason campaign_id")
        _digest_text(self.cohort_receipt_digest,
                     "evolution reason cohort_receipt_digest")
        _text(self.label_source, "evolution reason label_source")
        if self.evaluation_only is not True:
            raise P12ShadowTriggerError(
                "P13 evolution reason receipt must be evaluation-only")
        if self.canonical_memory_mutation != "none":
            raise P12ShadowTriggerError(
                "P13 evolution reason receipt cannot mutate canonical memory")
        if self.version != P13_EVOLUTION_REASON_RECEIPT_VERSION:
            raise P12ShadowTriggerError(
                "P13 evolution reason receipt version is invalid")
        object.__setattr__(self, "evidence_refs", _reason_evidence_refs(self.evidence_refs))
        reasons = _reason_map(self.evolution_reasons)
        object.__setattr__(self, "evolution_reasons", reasons)
        if self.case_evidence_refs is not None:
            object.__setattr__(
                self, "case_evidence_refs",
                _case_reason_evidence_refs(
                    self.case_evidence_refs, tuple(reasons)))

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "version": self.version,
            "campaign_id": self.campaign_id,
            "cohort_receipt_digest": self.cohort_receipt_digest,
            "label_source": self.label_source,
            "evidence_refs": [dict(item) for item in self.evidence_refs],
            "evolution_reasons": {
                case_id: list(reasons)
                for case_id, reasons in self.evolution_reasons.items()
            },
            "evaluation_only": self.evaluation_only,
            "canonical_memory_mutation": self.canonical_memory_mutation,
        }
        if self.case_evidence_refs is not None:
            payload["case_evidence_refs"] = {
                case_id: [dict(item) for item in refs]
                for case_id, refs in self.case_evidence_refs.items()
            }
        return payload

    @property
    def receipt_digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def receipt_id(self) -> str:
        return "p13_evolution_reason_" + self.receipt_digest.split(":", 1)[1][:24]

    @classmethod
    def from_dict(cls, payload: object) -> "P13EvolutionReasonReceipt":
        if not isinstance(payload, Mapping):
            raise P12ShadowTriggerError(
                "P13 evolution reason receipt must be an object")
        required = {
            "version", "campaign_id", "cohort_receipt_digest", "label_source",
            "evidence_refs", "evolution_reasons", "evaluation_only",
            "canonical_memory_mutation",
        }
        if not required <= set(payload):
            raise P12ShadowTriggerError(
                "P13 evolution reason receipt is missing fields")
        raw_case_refs = payload.get("case_evidence_refs")
        case_refs = None
        if raw_case_refs is not None:
            if not isinstance(raw_case_refs, Mapping):
                raise P12ShadowTriggerError(
                    "P13 evolution reason case_evidence_refs must be an object")
            case_refs = {
                case_id: tuple(refs)
                for case_id, refs in raw_case_refs.items()
            }
        receipt = cls(
            campaign_id=payload["campaign_id"],
            cohort_receipt_digest=payload["cohort_receipt_digest"],
            label_source=payload["label_source"],
            evidence_refs=tuple(payload["evidence_refs"]),
            evolution_reasons=dict(payload["evolution_reasons"]),
            case_evidence_refs=case_refs,
            evaluation_only=payload["evaluation_only"],
            canonical_memory_mutation=payload["canonical_memory_mutation"],
            version=payload["version"],
        )
        supplied = payload.get("receipt_digest")
        if supplied is not None and supplied != receipt.receipt_digest:
            raise P12ShadowTriggerError(
                "P13 evolution reason receipt digest mismatch")
        return receipt


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise P12ShadowTriggerError(f"P12 shadow trigger {name} is required")
    return value.strip()


def _digest_text(value: object, name: str) -> str:
    text = _text(value, name)
    if not text.startswith("sha256:") or len(text) <= len("sha256:"):
        raise P12ShadowTriggerError(
            f"P12 shadow trigger {name} must be a sha256 digest")
    return text


def _strict_bool(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise P12ShadowTriggerError(f"P12 shadow trigger {name} must be boolean")
    return value


def _oracle_complete(receipt: CandidateExecutionReceipt) -> bool:
    """Require an explicit available oracle and complete component verdicts."""
    if not isinstance(receipt, CandidateExecutionReceipt):
        raise P12ShadowTriggerError("P12 shadow trigger execution receipt is invalid")
    if receipt.evaluation_only is not True:
        return False
    if receipt.metadata.get("oracle_available") is not True:
        return False
    if receipt.compile_result == "UNKNOWN" or receipt.functional_result == "UNKNOWN":
        return False
    if receipt.signoff_result is None or receipt.signoff_result == "UNKNOWN":
        return False
    return receipt.outcome != "UNKNOWN"


def _cohort_fields(cohort: object) -> tuple[str, str, dict[str, PairedCandidateExecutionReceipt]]:
    """Read the common immutable surface of RTL/ORFS cohort receipts."""
    if not hasattr(cohort, "campaign_id") or not hasattr(cohort, "case_receipts"):
        raise P12ShadowTriggerError("P12 shadow trigger requires an RTL/ORFS cohort receipt")
    campaign_id = _text(getattr(cohort, "campaign_id"), "campaign_id")
    cases = getattr(cohort, "case_receipts")
    if not isinstance(cases, dict) or not cases:
        raise P12ShadowTriggerError("P12 shadow trigger cohort cases are required")
    if any(type(case_id) is not str or not case_id.strip()
           for case_id in cases):
        raise P12ShadowTriggerError("P12 shadow trigger case IDs are invalid")
    if any(not isinstance(receipt, PairedCandidateExecutionReceipt)
           for receipt in cases.values()):
        raise P12ShadowTriggerError("P12 shadow trigger cohort case receipt is invalid")
    if getattr(cohort, "evaluation_only", None) is not True:
        raise P12ShadowTriggerError("P12 shadow trigger cohort must be evaluation-only")
    if getattr(cohort, "source_disjoint", None) is not True:
        raise P12ShadowTriggerError("P12 shadow trigger cohort is not source-disjoint")
    if getattr(cohort, "source_restore_verified", None) is not True:
        raise P12ShadowTriggerError("P12 shadow trigger cohort source restore is unverified")
    receipt_digest = getattr(cohort, "receipt_digest", None)
    return campaign_id, _digest_text(receipt_digest, "cohort_receipt_digest"), dict(cases)


def _explicit_lineages(cases: Mapping[str, PairedCandidateExecutionReceipt]) -> set[str]:
    values: set[str] = set()
    for case_id, bundle in cases.items():
        lineage = bundle.lineage_id
        if type(lineage) is not str or not lineage.strip():
            raise P12ShadowTriggerError(
                f"P12 shadow trigger case {case_id} lacks explicit lineage_id")
        values.add(lineage.strip())
    return values


def _case_learner_eligibility(
        cases: Mapping[str, PairedCandidateExecutionReceipt],
        *, learner_eligible: bool,
        case_learner_eligibility: Mapping[str, bool] | None,
        ) -> dict[str, bool]:
    """Validate the learner partition before producing any P13 trigger.

    A cohort can contain both training and audit-only (held-out/calibration)
    cases.  A single campaign-level boolean is therefore not sufficient to
    authorize a shadow update: allowing it would let a held-out case inherit a
    training assertion and leak evaluation evidence into P13.  Callers that
    request learner evidence must provide an exact, per-case manifest binding;
    mixed cohorts fail closed and must be split into separate reports.
    """
    if case_learner_eligibility is None:
        if learner_eligible:
            raise P12ShadowTriggerError(
                "P12 shadow trigger requires explicit per-case learner eligibility")
        return {case_id: False for case_id in cases}
    if not isinstance(case_learner_eligibility, Mapping):
        raise P12ShadowTriggerError(
            "P12 shadow trigger case_learner_eligibility must be an object")
    if set(case_learner_eligibility) != set(cases):
        raise P12ShadowTriggerError(
            "P12 shadow trigger per-case learner eligibility must cover exactly all cases")
    result: dict[str, bool] = {}
    for case_id in cases:
        value = case_learner_eligibility[case_id]
        if type(value) is not bool:
            raise P12ShadowTriggerError(
                f"P12 shadow trigger learner eligibility for {case_id} must be boolean")
        result[case_id] = learner_eligible and value
    if learner_eligible and not all(result.values()):
        raise P12ShadowTriggerError(
            "P12 shadow trigger cohort mixes learner-eligible and audit-only cases")
    return result


def _evolution_reason_map(
        cases: Mapping[str, PairedCandidateExecutionReceipt],
        raw: Mapping[str, Sequence[str]] | None,
        ) -> dict[str, tuple[str, ...]]:
    """Validate explicit P13 evolution signals without inferring them.

    A complete PASS cohort is evaluation evidence, not an online-learning
    event.  The caller must bind one or more typed Revision2 reasons to every
    case that is intended to trigger a P13 shadow mutation.
    """
    if raw is None:
        return {case_id: () for case_id in cases}
    if not isinstance(raw, Mapping) or set(raw) != set(cases):
        raise P12ShadowTriggerError(
            "P12 shadow trigger evolution reasons must cover exactly all cases")
    result: dict[str, tuple[str, ...]] = {}
    for case_id in cases:
        values = raw[case_id]
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise P12ShadowTriggerError(
                f"P12 shadow trigger evolution reasons for {case_id} must be a sequence")
        if any(type(value) is not str for value in values):
            raise P12ShadowTriggerError(
                f"P12 shadow trigger evolution reasons for {case_id} are invalid")
        normalized = tuple(sorted(value.strip() for value in values))
        if any(not value or value not in P13_EVOLUTION_REASONS for value in normalized):
            raise P12ShadowTriggerError(
                f"P12 shadow trigger evolution reasons for {case_id} are invalid")
        if len(set(normalized)) != len(normalized):
            raise P12ShadowTriggerError(
                f"P12 shadow trigger evolution reasons for {case_id} contain duplicates")
        result[case_id] = normalized
    return result


def _reason(*, learner_eligible: bool, routing_id: str | None,
            routing_decision: str | None, baseline_complete: bool,
            memory_complete: bool,
            no_skill_reason: str | None,
            state_shift_receipt_id: str | None,
            state_shift_receipt: Mapping | None,
            evolution_reasons: Sequence[str]) -> tuple[bool, str]:
    if not learner_eligible:
        return False, "not_learner_eligible"
    if routing_id is None:
        return False, "missing_routing_receipt"
    if routing_decision is None:
        return False, "missing_routing_decision"
    route_is_memory_eligible = routing_decision in {"APPLY", "CONSIDER"}
    # Revision2 8A.9 has one deliberate no-memory exception: a typed,
    # non-transferable STATE_SHIFT refusal has already established the
    # transfer-boundary observation and therefore needs the paired historical
    # memory arm for the counterfactual comparison.  NO_MATCH/RISK and all
    # unresolved decisions remain non-triggering here.
    route_is_state_shift_observation = (
        routing_decision == "NO_SKILL" and
        no_skill_reason == "STATE_SHIFT" and
        state_shift_receipt_id is not None and
        state_shift_receipt is not None)
    if not (route_is_memory_eligible or route_is_state_shift_observation):
        return False, "routing_not_memory_eligible"
    if not baseline_complete:
        return False, "baseline_oracle_incomplete"
    if not memory_complete:
        return False, "memory_oracle_incomplete"
    if not evolution_reasons:
        return False, "no_evolution_signal"
    return True, "oracle_complete"


@dataclass(frozen=True)
class P12ShadowUpdateTriggerReceipt:
    """Content-addressed P12 evidence eligible to trigger P13 shadow work."""

    cohort_receipt_digest: str
    campaign_id: str
    case_id: str
    lineage_id: str
    memory_arm: str
    routing_receipt_id: str | None
    routing_decision: str | None
    routing_decision_digest: str | None
    no_skill_reason: str | None
    state_shift_receipt_id: str | None
    risk_receipt_id: str | None
    baseline_candidate_id: str
    memory_candidate_id: str
    baseline_execution_digest: str
    memory_execution_digest: str
    baseline_outcome: str
    memory_outcome: str
    baseline_oracle_complete: bool
    memory_oracle_complete: bool
    learner_eligible: bool
    triggered: bool
    reason: str
    evolution_reasons: tuple[str, ...] = ()
    evaluation_only: bool = True
    version: str = P12_SHADOW_TRIGGER_VERSION
    state_shift_receipt: dict | None = None

    def __post_init__(self) -> None:
        for value, name in (
                (self.cohort_receipt_digest, "cohort_receipt_digest"),
                (self.baseline_execution_digest, "baseline_execution_digest"),
                (self.memory_execution_digest, "memory_execution_digest")):
            _digest_text(value, name)
        for value, name in ((self.campaign_id, "campaign_id"),
                            (self.case_id, "case_id"),
                            (self.lineage_id, "lineage_id"),
                            (self.baseline_candidate_id, "baseline_candidate_id"),
                            (self.memory_candidate_id, "memory_candidate_id")):
            _text(value, name)
        if self.memory_arm not in _MEMORY_ARMS:
            raise P12ShadowTriggerError("P12 shadow trigger memory_arm is invalid")
        if self.routing_receipt_id is not None:
            _text(self.routing_receipt_id, "routing_receipt_id")
        if self.routing_decision is not None and self.routing_decision not in MEMORY_ROUTING_DECISIONS:
            raise P12ShadowTriggerError("P12 shadow trigger routing_decision is invalid")
        if self.routing_decision_digest is not None:
            _digest_text(self.routing_decision_digest, "routing_decision_digest")
        if self.no_skill_reason is not None:
            _text(self.no_skill_reason, "no_skill_reason")
        for value, name in ((self.state_shift_receipt_id, "state_shift_receipt_id"),
                            (self.risk_receipt_id, "risk_receipt_id")):
            if value is not None:
                _text(value, name)
        if self.state_shift_receipt is not None:
            try:
                from tehm.state.shift_receipts import StateShiftReceipt

                checked_shift = StateShiftReceipt.from_dict(
                    self.state_shift_receipt)
            except (TypeError, ValueError, KeyError) as exc:
                raise P12ShadowTriggerError(
                    "P12 shadow trigger state_shift_receipt is invalid") from exc
            if (self.no_skill_reason != "STATE_SHIFT" or
                    self.state_shift_receipt_id != checked_shift.receipt_id or
                    checked_shift.reason != "STATE_SHIFT" or
                    checked_shift.transferable):
                raise P12ShadowTriggerError(
                    "P12 shadow trigger state_shift_receipt binding is invalid")
        if self.baseline_outcome not in OUTCOMES or self.memory_outcome not in OUTCOMES:
            raise P12ShadowTriggerError("P12 shadow trigger outcome is invalid")
        for value, name in (
                (self.baseline_oracle_complete, "baseline_oracle_complete"),
                (self.memory_oracle_complete, "memory_oracle_complete"),
                (self.learner_eligible, "learner_eligible"),
                (self.triggered, "triggered"),
                (self.evaluation_only, "evaluation_only")):
            _strict_bool(value, name)
        if self.evaluation_only is not True:
            raise P12ShadowTriggerError("P12 shadow trigger must be evaluation-only")
        if self.reason not in _TRIGGER_REASONS:
            raise P12ShadowTriggerError("P12 shadow trigger reason is invalid")
        if not isinstance(self.evolution_reasons, tuple):
            raise P12ShadowTriggerError(
                "P12 shadow trigger evolution_reasons must be a tuple")
        if any(type(value) is not str or value not in P13_EVOLUTION_REASONS
               for value in self.evolution_reasons):
            raise P12ShadowTriggerError(
                "P12 shadow trigger evolution_reasons are invalid")
        if len(set(self.evolution_reasons)) != len(self.evolution_reasons):
            raise P12ShadowTriggerError(
                "P12 shadow trigger evolution_reasons contain duplicates")
        expected = self.reason == "oracle_complete"
        if self.triggered != expected:
            raise P12ShadowTriggerError(
                "P12 shadow trigger triggered flag disagrees with reason")
        route_is_memory_eligible = self.routing_decision in {"APPLY", "CONSIDER"}
        route_is_state_shift_observation = (
            self.routing_decision == "NO_SKILL" and
            self.no_skill_reason == "STATE_SHIFT" and
            self.state_shift_receipt_id is not None and
            self.state_shift_receipt is not None)
        if self.triggered and (
                not self.learner_eligible or self.routing_receipt_id is None or
                not (route_is_memory_eligible or route_is_state_shift_observation) or
                self.routing_decision_digest is None or
                not self.baseline_oracle_complete or not self.memory_oracle_complete):
            raise P12ShadowTriggerError(
                "triggered P12 shadow receipt lacks required evidence")
        if self.version == P12_SHADOW_TRIGGER_VERSION and self.triggered and not self.evolution_reasons:
            raise P12ShadowTriggerError(
                "triggered P12 shadow receipt lacks an evolution signal")
        if self.version not in {P12_SHADOW_TRIGGER_VERSION, _LEGACY_P12_SHADOW_TRIGGER_VERSION}:
            raise P12ShadowTriggerError("P12 shadow trigger version is invalid")

    @property
    def receipt_digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "cohort_receipt_digest": self.cohort_receipt_digest,
            "campaign_id": self.campaign_id,
            "case_id": self.case_id,
            "lineage_id": self.lineage_id,
            "memory_arm": self.memory_arm,
            "routing_receipt_id": self.routing_receipt_id,
            "routing_decision": self.routing_decision,
            "routing_decision_digest": self.routing_decision_digest,
            "no_skill_reason": self.no_skill_reason,
            "state_shift_receipt_id": self.state_shift_receipt_id,
            "risk_receipt_id": self.risk_receipt_id,
            **({"state_shift_receipt": dict(self.state_shift_receipt)}
               if self.state_shift_receipt is not None else {}),
            "baseline_candidate_id": self.baseline_candidate_id,
            "memory_candidate_id": self.memory_candidate_id,
            "baseline_execution_digest": self.baseline_execution_digest,
            "memory_execution_digest": self.memory_execution_digest,
            "baseline_outcome": self.baseline_outcome,
            "memory_outcome": self.memory_outcome,
            "baseline_oracle_complete": self.baseline_oracle_complete,
            "memory_oracle_complete": self.memory_oracle_complete,
            "learner_eligible": self.learner_eligible,
            "triggered": self.triggered,
            "reason": self.reason,
            "evolution_reasons": list(self.evolution_reasons),
            "evaluation_only": self.evaluation_only,
        }

    @property
    def legacy_receipt_digest(self) -> str:
        """Digest used by v0.1 receipts, before explicit reasons existed."""
        payload = self.to_dict()
        payload.pop("evolution_reasons", None)
        # v0.1 had no typed StateShiftReceipt payload.  Do not allow that
        # digest to authenticate a current receipt carrying a new witness.
        payload.pop("state_shift_receipt", None)
        payload["version"] = _LEGACY_P12_SHADOW_TRIGGER_VERSION
        return _digest(payload)

    @classmethod
    def from_dict(cls, payload: object) -> "P12ShadowUpdateTriggerReceipt":
        if not isinstance(payload, Mapping):
            raise P12ShadowTriggerError("P12 shadow trigger receipt must be an object")
        required = {
            "cohort_receipt_digest", "campaign_id", "case_id", "lineage_id",
            "memory_arm", "routing_receipt_id", "no_skill_reason",
            "routing_decision", "routing_decision_digest",
            "state_shift_receipt_id", "risk_receipt_id", "baseline_candidate_id",
            "memory_candidate_id", "baseline_execution_digest",
            "memory_execution_digest", "baseline_outcome", "memory_outcome",
            "baseline_oracle_complete", "memory_oracle_complete",
            "learner_eligible", "triggered", "reason", "evaluation_only",
        }
        if not required <= set(payload):
            raise P12ShadowTriggerError("P12 shadow trigger receipt is missing fields")
        receipt = cls(
            cohort_receipt_digest=payload["cohort_receipt_digest"],
            campaign_id=payload["campaign_id"], case_id=payload["case_id"],
            lineage_id=payload["lineage_id"], memory_arm=payload["memory_arm"],
            routing_receipt_id=payload["routing_receipt_id"],
            routing_decision=payload["routing_decision"],
            routing_decision_digest=payload["routing_decision_digest"],
            no_skill_reason=payload["no_skill_reason"],
            state_shift_receipt_id=payload["state_shift_receipt_id"],
            risk_receipt_id=payload["risk_receipt_id"],
            state_shift_receipt=(
                dict(payload["state_shift_receipt"])
                if payload.get("state_shift_receipt") is not None else None),
            baseline_candidate_id=payload["baseline_candidate_id"],
            memory_candidate_id=payload["memory_candidate_id"],
            baseline_execution_digest=payload["baseline_execution_digest"],
            memory_execution_digest=payload["memory_execution_digest"],
            baseline_outcome=payload["baseline_outcome"],
            memory_outcome=payload["memory_outcome"],
            baseline_oracle_complete=payload["baseline_oracle_complete"],
            memory_oracle_complete=payload["memory_oracle_complete"],
            learner_eligible=payload["learner_eligible"],
            triggered=payload["triggered"], reason=payload["reason"],
            evolution_reasons=tuple(payload.get("evolution_reasons", ())),
            evaluation_only=payload["evaluation_only"],
            version=payload.get("version", P12_SHADOW_TRIGGER_VERSION),
        )
        supplied = payload.get("receipt_digest")
        valid_digests = {receipt.receipt_digest}
        if (receipt.version == _LEGACY_P12_SHADOW_TRIGGER_VERSION and
                receipt.state_shift_receipt is None):
            valid_digests.add(receipt.legacy_receipt_digest)
        if supplied is not None and supplied not in valid_digests:
            raise P12ShadowTriggerError("P12 shadow trigger receipt digest mismatch")
        return receipt


def build_p12_shadow_update_triggers(
        cohort: object, *, memory_arm: str, learner_eligible: bool,
        min_lineages: int = 2,
        routing_decisions: Mapping[str, MemoryRoutingDecision] | None = None,
        case_learner_eligibility: Mapping[str, bool] | None = None,
        evolution_reasons: Mapping[str, Sequence[str]] | None = None,
        ) -> tuple[P12ShadowUpdateTriggerReceipt, ...]:
    """Convert each cohort case into a replayable P13 shadow trigger.

    ``learner_eligible`` and ``case_learner_eligibility`` are explicit caller
    assertions checked by the campaign manifest; they are never inferred from
    an execution outcome.  A learner-enabled call must provide an exact
    all-training per-case map, so a mixed training/held-out cohort cannot
    silently expand the learner support envelope.  A false campaign assertion
    yields non-trigger receipts.  Structural violations (tampered cohort,
    missing explicit lineages, or invalid arm identity) raise instead of
    silently becoming evidence.  ``evolution_reasons`` is likewise an exact
    per-case binding from the Revision2 signal vocabulary; complete PASS
    evidence without it is retain-only.
    """
    if memory_arm not in _MEMORY_ARMS:
        raise P12ShadowTriggerError("P12 shadow trigger memory_arm is invalid")
    if type(learner_eligible) is not bool:
        raise P12ShadowTriggerError("P12 shadow trigger learner_eligible must be boolean")
    if type(min_lineages) is not int or min_lineages < 1:
        raise P12ShadowTriggerError("P12 shadow trigger min_lineages must be positive")
    campaign_id, cohort_digest, cases = _cohort_fields(cohort)
    if routing_decisions is not None and not isinstance(routing_decisions, Mapping):
        raise P12ShadowTriggerError("P12 shadow trigger routing_decisions must be an object")
    if routing_decisions is not None:
        for case_id, decision in routing_decisions.items():
            if type(case_id) is not str or not case_id.strip() or not isinstance(
                    decision, MemoryRoutingDecision):
                raise P12ShadowTriggerError(
                    "P12 shadow trigger routing decision mapping is malformed")
    lineages = _explicit_lineages(cases)
    if len(lineages) < min_lineages:
        raise P12ShadowTriggerError(
            "P12 shadow trigger cohort lacks the required distinct lineages")
    case_learner = _case_learner_eligibility(
        cases, learner_eligible=learner_eligible,
        case_learner_eligibility=case_learner_eligibility)
    case_reasons = _evolution_reason_map(cases, evolution_reasons)
    results: list[P12ShadowUpdateTriggerReceipt] = []
    for case_id, bundle in sorted(cases.items()):
        if bundle.case_id != case_id:
            raise P12ShadowTriggerError("P12 shadow trigger case identity mismatch")
        baseline = bundle.arm_receipts["NO_MEMORY"]
        memory = bundle.arm_receipts[memory_arm]
        if baseline.source != "no_memory" or memory.source != "structured_memory":
            raise P12ShadowTriggerError("P12 shadow trigger arm source is invalid")
        routing_id = bundle.routing_receipt_id
        if routing_id is not None:
            routing_id = _text(routing_id, "routing_receipt_id")
        routing = routing_decisions.get(case_id) if routing_decisions is not None else None
        routing_decision = routing.decision if routing is not None else None
        routing_digest = routing.decision_digest if routing is not None else None
        state_shift_receipt = (
            routing.state_shift_receipt if routing is not None else None)
        if routing is not None:
            if routing.routing_receipt_id != routing_id:
                raise P12ShadowTriggerError(
                    f"P12 shadow trigger routing receipt mismatch for {case_id}")
            if (bundle.routing_decision is not None and
                    bundle.routing_decision != routing.decision):
                raise P12ShadowTriggerError(
                    f"P12 shadow trigger routing decision mismatch for {case_id}")
            if (routing.no_skill_reason != bundle.no_skill_reason or
                    routing.state_shift_receipt_id != bundle.state_shift_receipt_id or
                    routing.risk_receipt_id != bundle.risk_receipt_id):
                raise P12ShadowTriggerError(
                    f"P12 shadow trigger routing metadata mismatch for {case_id}")
        baseline_complete = _oracle_complete(baseline)
        memory_complete = _oracle_complete(memory)
        triggered, reason = _reason(
            learner_eligible=case_learner[case_id],
            routing_id=routing_id, routing_decision=routing_decision,
            baseline_complete=baseline_complete,
            memory_complete=memory_complete,
            no_skill_reason=bundle.no_skill_reason,
            state_shift_receipt_id=bundle.state_shift_receipt_id,
            state_shift_receipt=state_shift_receipt,
            evolution_reasons=case_reasons[case_id])
        results.append(P12ShadowUpdateTriggerReceipt(
            cohort_receipt_digest=cohort_digest, campaign_id=campaign_id,
            case_id=case_id, lineage_id=bundle.lineage_id.strip(),
            memory_arm=memory_arm, routing_receipt_id=routing_id,
            routing_decision=routing_decision,
            routing_decision_digest=routing_digest,
            no_skill_reason=bundle.no_skill_reason,
            state_shift_receipt_id=bundle.state_shift_receipt_id,
            state_shift_receipt=state_shift_receipt,
            risk_receipt_id=bundle.risk_receipt_id,
            baseline_candidate_id=baseline.candidate_id,
            memory_candidate_id=memory.candidate_id,
            baseline_execution_digest=baseline.execution_digest,
            memory_execution_digest=memory.execution_digest,
            baseline_outcome=baseline.outcome, memory_outcome=memory.outcome,
            baseline_oracle_complete=baseline_complete,
            memory_oracle_complete=memory_complete,
            learner_eligible=case_learner[case_id], triggered=triggered, reason=reason,
            evolution_reasons=case_reasons[case_id]))
    return tuple(results)


def build_p12_shadow_update_triggers_from_reason_receipt(
        cohort: object, *, memory_arm: str, learner_eligible: bool,
        reason_receipt: P13EvolutionReasonReceipt, min_lineages: int = 2,
        routing_decisions: Mapping[str, MemoryRoutingDecision] | None = None,
        case_learner_eligibility: Mapping[str, bool] | None = None,
        derivation_receipts: Mapping[str, Sequence[object]] | None = None,
        ) -> tuple[P12ShadowUpdateTriggerReceipt, ...]:
    """Build triggers from replayed typed detector output, not a label map.

    A typed P13 envelope is only a compact aggregation index.  Requiring the
    caller to provide the underlying derivation receipts here closes the
    provenance seam: a forged ``typed-detector:`` envelope with arbitrary
    ``receipt://`` references cannot become a trigger merely by recomputing its
    own envelope digest.  Manual/audit envelopes continue to use the legacy
    ``build_p12_shadow_update_triggers`` path.
    """
    if not isinstance(reason_receipt, P13EvolutionReasonReceipt):
        raise P12ShadowTriggerError(
            "typed P13 evolution reason receipt is invalid")
    if not reason_receipt.label_source.startswith("typed-detector:"):
        raise P12ShadowTriggerError(
            "typed P13 trigger path requires a typed-detector reason receipt")
    if reason_receipt.case_evidence_refs is None:
        raise P12ShadowTriggerError(
            "typed P13 trigger path requires per-case detector evidence")
    _verify_typed_derivation_receipts(
        reason_receipt, derivation_receipts)
    campaign_id, cohort_digest, cases = _cohort_fields(cohort)
    if (reason_receipt.campaign_id != campaign_id or
            reason_receipt.cohort_receipt_digest != cohort_digest or
            set(reason_receipt.evolution_reasons) != set(cases)):
        raise P12ShadowTriggerError(
            "typed P13 reason receipt does not match the cohort")
    return build_p12_shadow_update_triggers(
        cohort, memory_arm=memory_arm, learner_eligible=learner_eligible,
        min_lineages=min_lineages, routing_decisions=routing_decisions,
        case_learner_eligibility=case_learner_eligibility,
        evolution_reasons=reason_receipt.evolution_reasons)


def _verify_typed_derivation_receipts(
        reason_receipt: P13EvolutionReasonReceipt,
        derivation_receipts: Mapping[str, Sequence[object]] | None) -> None:
    """Replay every typed derivation referenced by a P13 envelope.

    The function intentionally compares the normalized, content-addressed
    references rather than trusting only the reason labels.  It also enforces
    exact case/reason coverage and campaign binding, so an envelope cannot
    smuggle an additional reason or omit a detector receipt at trigger time.
    """
    if derivation_receipts is None:
        raise P12ShadowTriggerError(
            "typed P13 trigger path requires derivation receipts")
    if not isinstance(derivation_receipts, Mapping):
        raise P12ShadowTriggerError(
            "typed P13 derivation receipts must be an object")
    expected_cases = set(reason_receipt.evolution_reasons)
    if set(derivation_receipts) != expected_cases:
        raise P12ShadowTriggerError(
            "typed P13 derivation receipts must cover exactly all cases")

    # Import lazily to keep the existing p12_shadow_trigger -> reason_derivation
    # aggregation import cycle one-directional.
    try:
        from .reason_derivation import (
            EvolutionReasonDerivationError,
            EvolutionReasonDerivationReceipt,
        )
    except ImportError as exc:  # pragma: no cover - package wiring failure
        raise P12ShadowTriggerError(
            "typed P13 derivation receipt type is unavailable") from exc

    seen_ids: set[str] = set()
    expected_case_refs: dict[str, tuple[dict[str, str], ...]] = {}
    all_refs: list[dict[str, str]] = []
    for case_id in sorted(expected_cases):
        raw_items = derivation_receipts[case_id]
        if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)) or not raw_items:
            raise P12ShadowTriggerError(
                f"typed P13 derivation receipts for {case_id} must not be empty")
        refs: list[dict[str, str]] = []
        reasons: set[str] = set()
        for raw_item in raw_items:
            try:
                item = (raw_item if isinstance(raw_item, EvolutionReasonDerivationReceipt)
                        else EvolutionReasonDerivationReceipt.from_dict(raw_item))
            except (EvolutionReasonDerivationError, TypeError, ValueError, KeyError) as exc:
                raise P12ShadowTriggerError(
                    f"typed P13 derivation receipt is invalid for {case_id}") from exc
            if item.campaign_id != reason_receipt.campaign_id or item.case_id != case_id:
                raise P12ShadowTriggerError(
                    f"typed P13 derivation receipt binding is invalid for {case_id}")
            if item.receipt_id in seen_ids:
                raise P12ShadowTriggerError(
                    "typed P13 derivation receipts contain duplicate receipts")
            seen_ids.add(item.receipt_id)
            reasons.add(item.reason)
            ref = {"path": "receipt://" + item.receipt_id,
                   "sha256": item.receipt_digest, "id": item.receipt_id}
            refs.append(ref)
            all_refs.append(ref)
        if reasons != set(reason_receipt.evolution_reasons[case_id]):
            raise P12ShadowTriggerError(
                f"typed P13 derivation reasons disagree for {case_id}")
        expected_case_refs[case_id] = tuple(
            sorted(refs, key=stable_dumps))

    actual_case_refs = {
        case_id: tuple(sorted(refs, key=stable_dumps))
        for case_id, refs in reason_receipt.case_evidence_refs.items()
    }
    if actual_case_refs != expected_case_refs:
        raise P12ShadowTriggerError(
            "typed P13 case evidence refs do not match derivation receipts")
    expected_global = tuple(sorted(all_refs, key=stable_dumps))
    actual_global = tuple(sorted(reason_receipt.evidence_refs, key=stable_dumps))
    if actual_global != expected_global:
        raise P12ShadowTriggerError(
            "typed P13 evidence refs do not match derivation receipts")


__all__ = [
    "P12_SHADOW_TRIGGER_VERSION", "P13_EVOLUTION_REASONS",
    "P13_EVOLUTION_REASON_RECEIPT_VERSION", "P12ShadowTriggerError",
    "P13EvolutionReasonReceipt",
    "P12ShadowUpdateTriggerReceipt", "build_p12_shadow_update_triggers",
    "build_p12_shadow_update_triggers_from_reason_receipt",
]
