"""Reason-aware proposals produced by repeated ``STATE_SHIFT`` observations.

This module deliberately stops at a typed proposal.  A state shift is a
runtime transfer-boundary observation, not permission to edit a Knowledge
claim.  Applying a proposal still requires the existing P13 shadow executor,
an explicit typed claim/evidence payload, and an eligible anti-forgetting
witness.
"""
from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tehm.canonical.transition import OUTCOMES, POSITIVE_OUTCOMES
from tehm.ids import stable_dumps
from tehm.state.shift_receipts import SHIFT_DIMENSIONS, StateShiftReceipt

from .events import load_state_shift_observations


STATE_SHIFT_EVOLUTION_VERSION = "state-shift-evolution-v1"
STATE_SHIFT_EVOLUTION_OPERATIONS = frozenset({
    "RETAIN", "REVISE", "SPECIALIZE", "SPLIT",
})
STATE_SHIFT_EVOLUTION_REASONS = frozenset({
    "SUPPORT_ENVELOPE_EXPANSION", "KNOWLEDGE_SPECIALIZATION",
    "KNOWLEDGE_SPLIT", "RETAIN_UNSAFE", "NOT_LEARNER_ELIGIBLE",
})


class StateShiftEvolutionError(ValueError):
    """Malformed or insufficient evidence for a state-shift proposal."""


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise StateShiftEvolutionError(f"state shift evolution {name} is required")
    return value.strip()


def _strings(value: object, name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
        raise StateShiftEvolutionError(
            f"state shift evolution {name} must be a sequence")
    result = tuple(_text(item, name) for item in value)
    if not allow_empty and not result:
        raise StateShiftEvolutionError(
            f"state shift evolution {name} must not be empty")
    if len(set(result)) != len(result):
        raise StateShiftEvolutionError(
            f"state shift evolution {name} must not contain duplicates")
    return result


def _outcomes(value: object, name: str, expected: int) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
        raise StateShiftEvolutionError(
            f"state shift evolution {name} must be a sequence")
    result = tuple(_text(item, name).upper() for item in value)
    if len(result) != expected:
        raise StateShiftEvolutionError(
            f"state shift evolution {name} must align with every receipt")
    if any(item not in OUTCOMES for item in result):
        raise StateShiftEvolutionError(
            f"state shift evolution {name} contains an invalid outcome")
    return result


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


@dataclass(frozen=True)
class StateShiftEvolutionProposal:
    """A replayable, evaluation-only proposal from repeated state shifts."""

    knowledge_object_id: str
    operation: str
    evolution_reason: str
    trigger_receipt_ids: tuple[str, ...]
    state_resolution_ids: tuple[str, ...]
    transition_ids: tuple[str, ...]
    shifted_dimensions: tuple[str, ...]
    no_memory_outcomes: tuple[str, ...]
    historical_memory_outcomes: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    learner_eligible: bool
    rationale: str
    partition_evidence_refs: tuple[str, ...] = ()
    shadow_only: bool = True
    evaluation_only: bool = True
    version: str = STATE_SHIFT_EVOLUTION_VERSION

    def __post_init__(self) -> None:
        _text(self.knowledge_object_id, "knowledge_object_id")
        if self.operation not in STATE_SHIFT_EVOLUTION_OPERATIONS:
            raise StateShiftEvolutionError("state shift evolution operation is invalid")
        if self.evolution_reason not in STATE_SHIFT_EVOLUTION_REASONS:
            raise StateShiftEvolutionError("state shift evolution reason is invalid")
        receipts = _strings(self.trigger_receipt_ids, "trigger_receipt_ids")
        resolutions = _strings(self.state_resolution_ids, "state_resolution_ids")
        transitions = _strings(self.transition_ids, "transition_ids")
        if len(receipts) < 2:
            raise StateShiftEvolutionError(
                "state shift evolution requires repeated observations")
        if not (len(receipts) == len(resolutions) == len(transitions)):
            raise StateShiftEvolutionError(
                "state shift evolution IDs must align with every receipt")
        dims = _strings(self.shifted_dimensions, "shifted_dimensions")
        if any(item not in SHIFT_DIMENSIONS for item in dims):
            raise StateShiftEvolutionError(
                "state shift evolution dimension is invalid")
        no_memory = _outcomes(self.no_memory_outcomes, "no_memory_outcomes", len(receipts))
        historical = _outcomes(
            self.historical_memory_outcomes, "historical_memory_outcomes", len(receipts))
        refs = _strings(self.evidence_refs, "evidence_refs")
        partitions = _strings(
            self.partition_evidence_refs, "partition_evidence_refs", allow_empty=True)
        if type(self.learner_eligible) is not bool:
            raise StateShiftEvolutionError("state shift evolution learner_eligible must be boolean")
        if type(self.shadow_only) is not bool or self.shadow_only is not True:
            raise StateShiftEvolutionError("state shift evolution must remain shadow-only")
        if type(self.evaluation_only) is not bool or self.evaluation_only is not True:
            raise StateShiftEvolutionError("state shift evolution must be evaluation-only")
        if self.version != STATE_SHIFT_EVOLUTION_VERSION:
            raise StateShiftEvolutionError("state shift evolution version is invalid")
        rationale = _text(self.rationale, "rationale")
        if self.operation == "SPLIT" and len(partitions) < 2:
            raise StateShiftEvolutionError(
                "state shift split requires explicit partition evidence")
        if self.operation != "SPLIT" and partitions:
            raise StateShiftEvolutionError(
                "partition evidence is only valid for a split proposal")
        if self.operation == "RETAIN" and self.evolution_reason not in {
                "RETAIN_UNSAFE", "NOT_LEARNER_ELIGIBLE"}:
            raise StateShiftEvolutionError(
                "retain proposal has an incompatible evolution reason")
        if self.operation == "REVISE" and self.evolution_reason != "SUPPORT_ENVELOPE_EXPANSION":
            raise StateShiftEvolutionError(
                "revise proposal must expand the support envelope")
        if self.operation == "SPECIALIZE" and self.evolution_reason != "KNOWLEDGE_SPECIALIZATION":
            raise StateShiftEvolutionError(
                "specialize proposal has an incompatible evolution reason")
        if self.operation == "SPLIT" and self.evolution_reason != "KNOWLEDGE_SPLIT":
            raise StateShiftEvolutionError(
                "split proposal has an incompatible evolution reason")
        object.__setattr__(self, "knowledge_object_id", self.knowledge_object_id.strip())
        object.__setattr__(self, "trigger_receipt_ids", receipts)
        object.__setattr__(self, "state_resolution_ids", resolutions)
        object.__setattr__(self, "transition_ids", transitions)
        object.__setattr__(self, "shifted_dimensions", dims)
        object.__setattr__(self, "no_memory_outcomes", no_memory)
        object.__setattr__(self, "historical_memory_outcomes", historical)
        object.__setattr__(self, "evidence_refs", refs)
        object.__setattr__(self, "partition_evidence_refs", partitions)
        object.__setattr__(self, "rationale", rationale)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "knowledge_object_id": self.knowledge_object_id,
            "operation": self.operation,
            "evolution_reason": self.evolution_reason,
            "trigger_receipt_ids": list(self.trigger_receipt_ids),
            "state_resolution_ids": list(self.state_resolution_ids),
            "transition_ids": list(self.transition_ids),
            "shifted_dimensions": list(self.shifted_dimensions),
            "no_memory_outcomes": list(self.no_memory_outcomes),
            "historical_memory_outcomes": list(self.historical_memory_outcomes),
            "evidence_refs": list(self.evidence_refs),
            "learner_eligible": self.learner_eligible,
            "rationale": self.rationale,
            "partition_evidence_refs": list(self.partition_evidence_refs),
            "shadow_only": self.shadow_only,
            "evaluation_only": self.evaluation_only,
        }

    @property
    def proposal_digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def proposal_id(self) -> str:
        return "state_shift_proposal_" + self.proposal_digest.split(":", 1)[1][:24]

    @classmethod
    def from_dict(cls, payload: object) -> "StateShiftEvolutionProposal":
        if not isinstance(payload, Mapping):
            raise StateShiftEvolutionError("state shift evolution proposal must be an object")
        required = set(cls.__dataclass_fields__) - {"version"}
        if not required <= set(payload):
            raise StateShiftEvolutionError(
                "state shift evolution proposal is missing fields")
        proposal = cls(
            knowledge_object_id=payload["knowledge_object_id"],
            operation=payload["operation"],
            evolution_reason=payload["evolution_reason"],
            trigger_receipt_ids=tuple(payload["trigger_receipt_ids"]),
            state_resolution_ids=tuple(payload["state_resolution_ids"]),
            transition_ids=tuple(payload["transition_ids"]),
            shifted_dimensions=tuple(payload["shifted_dimensions"]),
            no_memory_outcomes=tuple(payload["no_memory_outcomes"]),
            historical_memory_outcomes=tuple(payload["historical_memory_outcomes"]),
            evidence_refs=tuple(payload["evidence_refs"]),
            learner_eligible=payload["learner_eligible"],
            rationale=payload["rationale"],
            partition_evidence_refs=tuple(payload.get("partition_evidence_refs", ())),
            shadow_only=payload["shadow_only"],
            evaluation_only=payload["evaluation_only"],
            version=payload.get("version", STATE_SHIFT_EVOLUTION_VERSION),
        )
        supplied = payload.get("proposal_digest")
        if supplied is not None and supplied != proposal.proposal_digest:
            raise StateShiftEvolutionError("state shift evolution proposal digest mismatch")
        return proposal


def propose_repeated_state_shift(
    receipts: Sequence[StateShiftReceipt],
    *,
    knowledge_object_id: str,
    transition_ids: Sequence[str],
    no_memory_outcomes: Sequence[str],
    historical_memory_outcomes: Sequence[str],
    evidence_refs: Sequence[str],
    learner_eligible: bool = True,
    min_repeats: int = 2,
    requested_operation: str | None = None,
    partition_evidence_refs: Sequence[str] = (),
) -> StateShiftEvolutionProposal:
    """Propose a reason-specific update from repeated state shifts.

    The operation is deterministic and intentionally conservative:

    * current no-memory execution must be positive before proposing a
      structural change;
    * positive historical memory and current positive no-memory execution
      produce a same-claim ``REVISE`` (support-envelope expansion);
    * a historical harmful/unknown memory outcome produces ``SPECIALIZE``;
    * an unsafe current no-memory execution produces ``RETAIN``;
    * ``SPLIT`` is never inferred and requires explicit partition evidence.

    No function in this module writes SQLite or changes canonical memory.
    """
    if not isinstance(receipts, (list, tuple)) or isinstance(receipts, (str, bytes)):
        raise StateShiftEvolutionError("state shift evolution receipts must be a sequence")
    if type(min_repeats) is not int or min_repeats < 2:
        raise StateShiftEvolutionError("state shift evolution min_repeats must be at least two")
    if len(receipts) < min_repeats:
        raise StateShiftEvolutionError("state shift evolution requires repeated observations")
    if any(not isinstance(item, StateShiftReceipt) for item in receipts):
        raise StateShiftEvolutionError("state shift evolution receipts are invalid")
    parent = _text(knowledge_object_id, "knowledge_object_id")
    if any(item.knowledge_object_id != parent for item in receipts):
        raise StateShiftEvolutionError("state shift evolution knowledge IDs do not match")
    if any(item.reason != "STATE_SHIFT" or item.transferable is not False for item in receipts):
        raise StateShiftEvolutionError("state shift evolution requires non-transferable shifts")
    if len({item.receipt_id for item in receipts}) != len(receipts):
        raise StateShiftEvolutionError("state shift evolution receipts must be unique")
    if len({item.current_resolution_id for item in receipts}) != len(receipts):
        raise StateShiftEvolutionError("state shift evolution resolutions must be unique")
    ids = _strings(transition_ids, "transition_ids")
    if len(ids) != len(receipts):
        raise StateShiftEvolutionError("state shift evolution transition IDs must align")
    refs = _strings(evidence_refs, "evidence_refs")
    no_memory = _outcomes(no_memory_outcomes, "no_memory_outcomes", len(receipts))
    historical = _outcomes(
        historical_memory_outcomes, "historical_memory_outcomes", len(receipts))
    if type(learner_eligible) is not bool:
        raise StateShiftEvolutionError("state shift evolution learner_eligible must be boolean")
    requested = requested_operation
    if requested is not None and requested not in STATE_SHIFT_EVOLUTION_OPERATIONS:
        raise StateShiftEvolutionError("state shift evolution requested operation is invalid")
    partitions = _strings(
        partition_evidence_refs, "partition_evidence_refs", allow_empty=True)
    dimensions = tuple(sorted({dimension for item in receipts for dimension in item.shifted_dimensions}))
    all_current_positive = all(item in POSITIVE_OUTCOMES for item in no_memory)
    all_historical_positive = all(item in POSITIVE_OUTCOMES for item in historical)
    if not learner_eligible:
        operation, reason = "RETAIN", "NOT_LEARNER_ELIGIBLE"
        rationale = "repeated state shifts are audit-only and cannot propose learner mutation"
    elif requested == "SPLIT":
        if not all_current_positive:
            raise StateShiftEvolutionError(
                "state shift split requires positive no-memory oracle outcomes")
        operation, reason = "SPLIT", "KNOWLEDGE_SPLIT"
        rationale = "explicit partition evidence requests a new mechanism branch"
    elif requested is not None:
        operation = requested
        if operation == "REVISE":
            if not all_current_positive or not all_historical_positive:
                raise StateShiftEvolutionError(
                    "support-envelope expansion requires positive paired outcomes")
            reason = "SUPPORT_ENVELOPE_EXPANSION"
            rationale = "repeated shifts remain safe for the historical memory candidate"
        elif operation == "SPECIALIZE":
            if not all_current_positive:
                raise StateShiftEvolutionError(
                    "knowledge specialization requires positive no-memory outcomes")
            reason = "KNOWLEDGE_SPECIALIZATION"
            rationale = "repeated shifts require a narrower applicability branch"
        elif operation == "RETAIN":
            reason = "RETAIN_UNSAFE"
            rationale = "state-shift evidence is retained without a safe mutation proposal"
        else:
            raise StateShiftEvolutionError(
                "requested state shift operation requires explicit split handling")
    elif not all_current_positive:
        operation, reason = "RETAIN", "RETAIN_UNSAFE"
        rationale = "no-memory execution is not uniformly positive; retain without mutation"
    elif all_historical_positive:
        operation, reason = "REVISE", "SUPPORT_ENVELOPE_EXPANSION"
        rationale = "repeated shifted states remain safe and can expand the support envelope"
    else:
        operation, reason = "SPECIALIZE", "KNOWLEDGE_SPECIALIZATION"
        rationale = "historical memory outcomes are not uniformly safe; narrow applicability"
    return StateShiftEvolutionProposal(
        knowledge_object_id=parent, operation=operation,
        evolution_reason=reason,
        trigger_receipt_ids=tuple(item.receipt_id for item in receipts),
        state_resolution_ids=tuple(item.current_resolution_id for item in receipts),
        transition_ids=ids, shifted_dimensions=dimensions,
        no_memory_outcomes=no_memory, historical_memory_outcomes=historical,
        evidence_refs=refs, learner_eligible=learner_eligible,
        rationale=rationale, partition_evidence_refs=partitions,
    )


def propose_repeated_state_shift_from_events(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    knowledge_object_id: str,
    transition_ids: Sequence[str],
    no_memory_outcomes: Sequence[str],
    historical_memory_outcomes: Sequence[str],
    evidence_refs: Sequence[str],
    min_repeats: int = 2,
    requested_operation: str | None = None,
    partition_evidence_refs: Sequence[str] = (),
) -> StateShiftEvolutionProposal:
    """Replay state-shift events and produce the corresponding proposal.

    The event log is the provenance authority for learner eligibility.  A
    caller cannot upgrade an audit-only event by passing a boolean, and a
    campaign containing mixed learner/audit state-shift events fails closed.
    The explicit transition order is retained so each oracle outcome remains
    paired with the event that produced it.
    """
    if not isinstance(conn, sqlite3.Connection):
        raise TypeError("state shift evolution conn must be sqlite3.Connection")
    campaign = _text(campaign_id, "campaign_id")
    parent = _text(knowledge_object_id, "knowledge_object_id")
    if type(min_repeats) is not int or min_repeats < 2:
        raise StateShiftEvolutionError(
            "state shift evolution min_repeats must be at least two")
    transitions = _strings(transition_ids, "transition_ids")
    if len(transitions) < min_repeats:
        raise StateShiftEvolutionError(
            "state shift evolution requires repeated transition events")
    observations = load_state_shift_observations(
        conn, campaign_id=campaign, knowledge_object_id=parent)
    by_transition = {event.source_id: (event, receipt)
                     for event, receipt in observations}
    if len(by_transition) != len(observations):
        raise StateShiftEvolutionError(
            "state shift evolution event sources must be unique")
    missing = [transition for transition in transitions
               if transition not in by_transition]
    if missing:
        raise StateShiftEvolutionError(
            "state shift evolution is missing observed transitions: "
            + ",".join(missing))
    if len(set(transitions)) != len(transitions):
        raise StateShiftEvolutionError(
            "state shift evolution transition IDs must be unique")
    selected = [by_transition[transition] for transition in transitions]
    eligibility = {event.learner_eligible for event, _receipt in selected}
    if len(eligibility) != 1:
        raise StateShiftEvolutionError(
            "state shift evolution cannot mix learner and audit observations")
    refs = set(_strings(evidence_refs, "evidence_refs"))
    required_refs = {
        ref for event, receipt in selected
        for ref in (event.event_digest, receipt.receipt_id)
    }
    if not required_refs <= refs:
        missing_refs = sorted(required_refs - refs)
        raise StateShiftEvolutionError(
            "state shift evolution evidence_refs must witness events and receipts: "
            + ",".join(missing_refs))
    return propose_repeated_state_shift(
        [receipt for _event, receipt in selected],
        knowledge_object_id=parent, transition_ids=transitions,
        no_memory_outcomes=no_memory_outcomes,
        historical_memory_outcomes=historical_memory_outcomes,
        evidence_refs=tuple(sorted(refs)),
        learner_eligible=next(iter(eligibility)), min_repeats=min_repeats,
        requested_operation=requested_operation,
        partition_evidence_refs=partition_evidence_refs,
    )


plan_repeated_state_shift = propose_repeated_state_shift


__all__ = [
    "STATE_SHIFT_EVOLUTION_VERSION", "STATE_SHIFT_EVOLUTION_OPERATIONS",
    "STATE_SHIFT_EVOLUTION_REASONS", "StateShiftEvolutionError",
    "StateShiftEvolutionProposal", "propose_repeated_state_shift",
    "plan_repeated_state_shift", "propose_repeated_state_shift_from_events",
]
