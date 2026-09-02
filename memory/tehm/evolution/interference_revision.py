"""Typed ``MEMORY_INTERFERENCE`` proposals for the P13 shadow lane.

The detector and admission layers establish *why* a memory claim should be
narrowed.  This module is the small, reason-specific planning seam between
that evidence and :class:`LocalizedUpdatePlan`: it never reads or constructs
the replacement ``MechanismKnowledge`` payload.  The replacement is supplied
later to the isolated shadow executor and remains unevaluable as authority.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tehm.evaluation.candidate_executor import (
    CandidateExecutionReceipt, PairedCandidateExecutionReceipt,
)
from tehm.ids import stable_dumps

from .local_revision import LocalizedUpdatePlan
from .reason_derivation import (
    EvolutionReasonDerivationError, EvolutionReasonDerivationReceipt,
    derive_memory_interference_reason,
)


INTERFERENCE_REVISION_VERSION = "memory-interference-revision-v0.1"


class MemoryInterferenceRevisionError(ValueError):
    """A negative-applicability proposal is malformed or under-evidenced."""


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise MemoryInterferenceRevisionError(
            f"memory interference revision {name} is required")
    return value.strip()


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _refs(value: object, name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
        raise MemoryInterferenceRevisionError(
            f"memory interference revision {name} must be a sequence")
    refs = tuple(_text(item, name) for item in value)
    if not allow_empty and not refs:
        raise MemoryInterferenceRevisionError(
            f"memory interference revision {name} must not be empty")
    if len(set(refs)) != len(refs):
        raise MemoryInterferenceRevisionError(
            f"memory interference revision {name} contains duplicates")
    return tuple(sorted(refs))


def _ordered_refs(value: object, name: str) -> tuple[str, ...]:
    """Validate aligned witness vectors without changing their pairing order."""
    if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
        raise MemoryInterferenceRevisionError(
            f"memory interference revision {name} must be a sequence")
    refs = tuple(_text(item, name) for item in value)
    if not refs or len(set(refs)) != len(refs):
        raise MemoryInterferenceRevisionError(
            f"memory interference revision {name} must be non-empty and unique")
    return refs


def _contexts(value: object) -> tuple[dict, ...]:
    if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
        raise MemoryInterferenceRevisionError(
            "memory interference revision negative_applicability must be a sequence")
    if not value:
        raise MemoryInterferenceRevisionError(
            "memory interference revision requires a negative applicability context")
    contexts: list[dict] = []
    encoded: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise MemoryInterferenceRevisionError(
                "memory interference negative applicability context must be an object")
        context = dict(item)
        if not context:
            raise MemoryInterferenceRevisionError(
                "memory interference negative applicability context must not be empty")
        try:
            key = stable_dumps(context)
        except (TypeError, ValueError) as exc:
            raise MemoryInterferenceRevisionError(
                "memory interference negative applicability is not JSON-serializable") from exc
        if key in encoded:
            raise MemoryInterferenceRevisionError(
                "memory interference negative applicability contains duplicates")
        encoded.add(key)
        contexts.append(context)
    return tuple(contexts)


@dataclass(frozen=True)
class MemoryInterferenceEvolutionProposal:
    """A typed, shadow-only specialization proposal.

    ``negative_applicability`` is evidence for the eventual replacement claim,
    not the replacement itself.  Keeping it here makes the proposed scope
    auditable while preserving the detector/mutation independence boundary.
    """

    campaign_id: str
    knowledge_object_id: str
    case_ids: tuple[str, ...]
    transition_ids: tuple[str, ...]
    derivation_receipt_ids: tuple[str, ...]
    paired_receipt_digests: tuple[str, ...]
    trigger_receipt_ids: tuple[str, ...]
    negative_applicability: tuple[dict, ...]
    evidence_refs: tuple[str, ...]
    learner_eligible: bool = True
    operation: str = "SPECIALIZE"
    evolution_reason: str = "MEMORY_INTERFERENCE"
    rationale: str = ""
    shadow_only: bool = True
    evaluation_only: bool = True
    version: str = INTERFERENCE_REVISION_VERSION

    def __post_init__(self) -> None:
        for value, name in ((self.campaign_id, "campaign_id"),
                            (self.knowledge_object_id, "knowledge_object_id")):
            object.__setattr__(self, name, _text(value, name))
        if self.operation != "SPECIALIZE":
            raise MemoryInterferenceRevisionError(
                "memory interference proposal operation must be SPECIALIZE")
        if self.evolution_reason != "MEMORY_INTERFERENCE":
            raise MemoryInterferenceRevisionError(
                "memory interference proposal reason is invalid")
        if self.version != INTERFERENCE_REVISION_VERSION:
            raise MemoryInterferenceRevisionError(
                "memory interference proposal version is invalid")
        if type(self.learner_eligible) is not bool or not self.learner_eligible:
            raise MemoryInterferenceRevisionError(
                "memory interference proposal must be learner-eligible")
        if self.shadow_only is not True or self.evaluation_only is not True:
            raise MemoryInterferenceRevisionError(
                "memory interference proposal must remain evaluation-only shadow")
        cases = _ordered_refs(self.case_ids, "case_ids")
        transitions = _ordered_refs(self.transition_ids, "transition_ids")
        derivations = _ordered_refs(self.derivation_receipt_ids, "derivation_receipt_ids")
        paired = _ordered_refs(self.paired_receipt_digests, "paired_receipt_digests")
        triggers = _refs(self.trigger_receipt_ids, "trigger_receipt_ids", allow_empty=True)
        refs = _refs(self.evidence_refs, "evidence_refs")
        if len(cases) < 2:
            raise MemoryInterferenceRevisionError(
                "memory interference proposal requires at least two cases")
        if not (len(cases) == len(transitions) == len(derivations) == len(paired)):
            raise MemoryInterferenceRevisionError(
                "memory interference proposal evidence cardinalities must align")
        contexts = _contexts(self.negative_applicability)
        rationale = _text(self.rationale, "rationale")
        object.__setattr__(self, "case_ids", cases)
        object.__setattr__(self, "transition_ids", transitions)
        object.__setattr__(self, "derivation_receipt_ids", derivations)
        object.__setattr__(self, "paired_receipt_digests", paired)
        object.__setattr__(self, "trigger_receipt_ids", triggers)
        object.__setattr__(self, "negative_applicability", contexts)
        object.__setattr__(self, "evidence_refs", refs)
        object.__setattr__(self, "rationale", rationale)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "campaign_id": self.campaign_id,
            "knowledge_object_id": self.knowledge_object_id,
            "case_ids": list(self.case_ids),
            "transition_ids": list(self.transition_ids),
            "derivation_receipt_ids": list(self.derivation_receipt_ids),
            "paired_receipt_digests": list(self.paired_receipt_digests),
            "trigger_receipt_ids": list(self.trigger_receipt_ids),
            "negative_applicability": [dict(item) for item in self.negative_applicability],
            "evidence_refs": list(self.evidence_refs),
            "learner_eligible": self.learner_eligible,
            "operation": self.operation,
            "evolution_reason": self.evolution_reason,
            "rationale": self.rationale,
            "shadow_only": self.shadow_only,
            "evaluation_only": self.evaluation_only,
        }

    @property
    def proposal_digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def proposal_id(self) -> str:
        return "memory_interference_proposal_" + self.proposal_digest.split(":", 1)[1][:24]

    @classmethod
    def from_dict(cls, payload: object) -> "MemoryInterferenceEvolutionProposal":
        if not isinstance(payload, Mapping):
            raise MemoryInterferenceRevisionError(
                "memory interference proposal must be an object")
        required = set(cls.__dataclass_fields__) - {"version"}
        if not required <= set(payload):
            raise MemoryInterferenceRevisionError(
                "memory interference proposal is missing fields")
        proposal = cls(
            version=payload.get("version", INTERFERENCE_REVISION_VERSION),
            campaign_id=payload["campaign_id"],
            knowledge_object_id=payload["knowledge_object_id"],
            case_ids=tuple(payload["case_ids"]),
            transition_ids=tuple(payload["transition_ids"]),
            derivation_receipt_ids=tuple(payload["derivation_receipt_ids"]),
            paired_receipt_digests=tuple(payload["paired_receipt_digests"]),
            trigger_receipt_ids=tuple(payload["trigger_receipt_ids"]),
            negative_applicability=tuple(payload["negative_applicability"]),
            evidence_refs=tuple(payload["evidence_refs"]),
            learner_eligible=payload["learner_eligible"],
            operation=payload["operation"],
            evolution_reason=payload["evolution_reason"],
            rationale=payload["rationale"],
            shadow_only=payload["shadow_only"],
            evaluation_only=payload["evaluation_only"],
        )
        supplied = payload.get("proposal_digest")
        if supplied is not None and supplied != proposal.proposal_digest:
            raise MemoryInterferenceRevisionError(
                "memory interference proposal digest mismatch")
        supplied_id = payload.get("proposal_id")
        if supplied_id is not None and supplied_id != proposal.proposal_id:
            raise MemoryInterferenceRevisionError(
                "memory interference proposal ID mismatch")
        return proposal


def propose_memory_interference_specialization(
        observations: Sequence[tuple[EvolutionReasonDerivationReceipt,
                                     PairedCandidateExecutionReceipt]], *,
        knowledge_object_id: str, transition_ids: Sequence[str],
        negative_applicability: Sequence[Mapping],
        evidence_refs: Sequence[str], trigger_receipt_ids: Sequence[str] = (),
        min_cases: int = 2) -> MemoryInterferenceEvolutionProposal:
    """Derive a ``SPECIALIZE`` proposal from paired interference receipts.

    The function replays the typed detector for every observation.  It accepts
    no labels and no mutation payload, and fails closed on incomplete/UNKNOWN
    oracle evidence, duplicate lineages, or missing provenance references.
    """
    if (not isinstance(observations, (list, tuple)) or
            isinstance(observations, (str, bytes))):
        raise MemoryInterferenceRevisionError("interference observations must be a sequence")
    if type(min_cases) is not int or min_cases < 2:
        raise MemoryInterferenceRevisionError("interference min_cases must be at least two")
    if len(observations) < min_cases:
        raise MemoryInterferenceRevisionError("interference requires repeated independent cases")
    parent = _text(knowledge_object_id, "knowledge_object_id")
    transitions = _refs(transition_ids, "transition_ids")
    if len(transitions) != len(observations):
        raise MemoryInterferenceRevisionError(
            "interference transition IDs must align with observations")
    contexts = _contexts(negative_applicability)
    refs = set(_refs(evidence_refs, "evidence_refs"))
    trigger_ids = _refs(trigger_receipt_ids, "trigger_receipt_ids", allow_empty=True)
    checked: list[tuple[EvolutionReasonDerivationReceipt,
                        PairedCandidateExecutionReceipt]] = []
    cases: list[str] = []
    derivation_ids: list[str] = []
    paired_digests: list[str] = []
    lineages: set[str] = set()
    campaign: str | None = None
    for item in observations:
        if (not isinstance(item, tuple) or len(item) != 2 or
                not isinstance(item[0], EvolutionReasonDerivationReceipt) or
                not isinstance(item[1], PairedCandidateExecutionReceipt)):
            raise MemoryInterferenceRevisionError(
                "interference observation must be a typed derivation/paired tuple")
        derivation, paired = item
        if campaign is None:
            campaign = derivation.campaign_id
        if derivation.campaign_id != campaign or paired.case_id != derivation.case_id:
            raise MemoryInterferenceRevisionError(
                "interference observations must share campaign and case bindings")
        try:
            replayed = derive_memory_interference_reason(
                paired, campaign_id=campaign)
        except EvolutionReasonDerivationError as exc:
            raise MemoryInterferenceRevisionError(str(exc)) from exc
        if (replayed is None or replayed.receipt_digest != derivation.receipt_digest or
                derivation.reason != "MEMORY_INTERFERENCE"):
            raise MemoryInterferenceRevisionError(
                "interference derivation does not replay from paired evidence")
        lineage = paired.lineage_id
        if type(lineage) is not str or not lineage.strip() or lineage in lineages:
            raise MemoryInterferenceRevisionError(
                "interference observations require distinct explicit lineages")
        lineages.add(lineage)
        cases.append(derivation.case_id)
        derivation_ids.append(derivation.receipt_id)
        paired_digests.append(paired.receipt_digest)
        refs.update({derivation.receipt_id, paired.receipt_digest,
                     *derivation.input_receipt_ids,
                     *derivation.input_digests})
        checked.append(item)
    if campaign is None:  # pragma: no cover - guarded by min_cases
        raise MemoryInterferenceRevisionError("interference campaign is missing")
    refs.update(transitions)
    refs.update(trigger_ids)
    required = set(derivation_ids) | set(paired_digests) | set(transitions) | set(trigger_ids)
    if not required <= refs:
        raise MemoryInterferenceRevisionError(
            "interference evidence_refs do not cover all typed witnesses")
    del checked  # The local list documents the replay boundary; no mutation input is retained.
    return MemoryInterferenceEvolutionProposal(
        campaign_id=campaign, knowledge_object_id=parent,
        case_ids=tuple(cases), transition_ids=transitions,
        derivation_receipt_ids=tuple(derivation_ids),
        paired_receipt_digests=tuple(paired_digests),
        trigger_receipt_ids=trigger_ids, negative_applicability=contexts,
        evidence_refs=tuple(sorted(refs)), learner_eligible=True,
        rationale=("paired no-memory success with forced-memory harm requires "
                   "a narrower applicability branch"))


def interference_proposal_to_localized_plan(
        proposal: MemoryInterferenceEvolutionProposal, *, priority: str = "P1_HIGH",
        value_score: float = 1.0) -> LocalizedUpdatePlan:
    """Convert an interference proposal to the generic P13 plan contract."""
    if not isinstance(proposal, MemoryInterferenceEvolutionProposal):
        raise TypeError("interference plan conversion requires a typed proposal")
    refs = set(proposal.evidence_refs)
    return LocalizedUpdatePlan(
        transition_id=proposal.transition_ids[0], campaign_id=proposal.campaign_id,
        learner_eligible=True, priority=priority, value_score=value_score,
        update_target="UPDATE_CAUSAL_KNOWLEDGE",
        candidate_targets=("UPDATE_CAUSAL_KNOWLEDGE",), operation="SPECIALIZE",
        failure_type="MEMORY_INTERFERENCE", knowledge_refs=(proposal.knowledge_object_id,),
        evidence_refs=tuple(sorted(refs)), rationale=proposal.rationale,
        shadow_only=True)


__all__ = [
    "INTERFERENCE_REVISION_VERSION", "MemoryInterferenceRevisionError",
    "MemoryInterferenceEvolutionProposal", "propose_memory_interference_specialization",
    "interference_proposal_to_localized_plan",
]
