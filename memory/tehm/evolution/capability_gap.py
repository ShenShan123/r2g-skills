"""Shadow proposal seam for non-P12 capability expansion.

The proposal is intentionally an immutable description of *what may be
added*.  It is not an Asset Memory registration, Knowledge crystallization,
or production-runtime authorization.  A later evaluator must validate any
concrete asset/knowledge object independently before it can enter lifecycle
authority.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from tehm.assets.receipts import CapabilityGapReceipt
from contracts import MemoryRoutingDecision
from tehm.ids import stable_dumps

from .admission import EvolutionAdmissionReceipt
from .reason_derivation import (
    EvolutionReasonDerivationError, EvolutionReasonDerivationReceipt,
    derive_capability_gap_reason,
)


CAPABILITY_GAP_PROPOSAL_VERSION = "capability-gap-proposal-v0.1"
PROPOSAL_KINDS = frozenset({"ASSET", "KNOWLEDGE", "ASSET_OR_KNOWLEDGE"})


class CapabilityGapProposalError(ValueError):
    """A capability-gap proposal or its authority references are malformed."""


def _text(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise CapabilityGapProposalError(f"capability gap proposal {field} is required")
    return value.strip()


def _strings(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or isinstance(value, (str, bytes)):
        raise CapabilityGapProposalError(
            f"capability gap proposal {field} must be a sequence")
    values = tuple(_text(item, field) for item in value)
    if not values or len(set(values)) != len(values):
        raise CapabilityGapProposalError(
            f"capability gap proposal {field} must be non-empty and unique")
    return values


@dataclass(frozen=True)
class CapabilityGapEvolutionProposal:
    """Evidence-bound ADD proposal emitted after non-P12 admission."""

    campaign_id: str
    case_id: str
    gap_id: str
    mechanism_family: str
    compatibility_profile: str | None
    missing_asset_types: tuple[str, ...]
    operation: str
    proposal_kind: str
    derivation_receipt_id: str
    admission_receipt_id: str
    evidence_transition_ids: tuple[str, ...]
    evidence_lineages: tuple[str, ...]
    evaluation_only: bool = True
    canonical_memory_mutation: str = "none"
    production_runtime_eligible: bool = False
    version: str = CAPABILITY_GAP_PROPOSAL_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "campaign_id", _text(self.campaign_id, "campaign_id"))
        object.__setattr__(self, "case_id", _text(self.case_id, "case_id"))
        object.__setattr__(self, "gap_id", _text(self.gap_id, "gap_id"))
        object.__setattr__(self, "mechanism_family",
                           _text(self.mechanism_family, "mechanism_family"))
        if self.compatibility_profile is not None:
            object.__setattr__(self, "compatibility_profile",
                               _text(self.compatibility_profile, "compatibility_profile"))
        object.__setattr__(self, "missing_asset_types",
                           _strings(self.missing_asset_types, "missing_asset_types"))
        if self.operation != "ADD":
            raise CapabilityGapProposalError("capability gap proposal operation must be ADD")
        if self.proposal_kind not in PROPOSAL_KINDS:
            raise CapabilityGapProposalError("capability gap proposal kind is invalid")
        object.__setattr__(self, "derivation_receipt_id",
                           _text(self.derivation_receipt_id, "derivation_receipt_id"))
        object.__setattr__(self, "admission_receipt_id",
                           _text(self.admission_receipt_id, "admission_receipt_id"))
        object.__setattr__(self, "evidence_transition_ids",
                           _strings(self.evidence_transition_ids, "evidence_transition_ids"))
        object.__setattr__(self, "evidence_lineages",
                           _strings(self.evidence_lineages, "evidence_lineages"))
        if self.version != CAPABILITY_GAP_PROPOSAL_VERSION:
            raise CapabilityGapProposalError("capability gap proposal version is invalid")
        if self.evaluation_only is not True:
            raise CapabilityGapProposalError("capability gap proposal must be evaluation-only")
        if self.canonical_memory_mutation != "none":
            raise CapabilityGapProposalError(
                "capability gap proposal cannot mutate canonical memory")
        if self.production_runtime_eligible is not False:
            raise CapabilityGapProposalError(
                "capability gap proposal cannot be production-runtime eligible")

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "campaign_id": self.campaign_id,
            "case_id": self.case_id,
            "gap_id": self.gap_id,
            "mechanism_family": self.mechanism_family,
            "compatibility_profile": self.compatibility_profile,
            "missing_asset_types": list(self.missing_asset_types),
            "operation": self.operation,
            "proposal_kind": self.proposal_kind,
            "derivation_receipt_id": self.derivation_receipt_id,
            "admission_receipt_id": self.admission_receipt_id,
            "evidence_transition_ids": list(self.evidence_transition_ids),
            "evidence_lineages": list(self.evidence_lineages),
            "evaluation_only": self.evaluation_only,
            "canonical_memory_mutation": self.canonical_memory_mutation,
            "production_runtime_eligible": self.production_runtime_eligible,
        }

    @property
    def proposal_digest(self) -> str:
        return "sha256:" + hashlib.sha256(
            stable_dumps(self.to_dict()).encode()).hexdigest()

    @property
    def proposal_id(self) -> str:
        return "capability_gap_proposal_" + self.proposal_digest.split(":", 1)[1][:24]

    @classmethod
    def from_dict(cls, payload: object) -> "CapabilityGapEvolutionProposal":
        if not isinstance(payload, Mapping):
            raise CapabilityGapProposalError(
                "capability gap proposal must be an object")
        required = {
            "campaign_id", "case_id", "gap_id", "mechanism_family",
            "compatibility_profile", "missing_asset_types", "operation",
            "proposal_kind", "derivation_receipt_id", "admission_receipt_id",
            "evidence_transition_ids", "evidence_lineages", "evaluation_only",
            "canonical_memory_mutation", "production_runtime_eligible",
        }
        if not required <= set(payload):
            raise CapabilityGapProposalError(
                "capability gap proposal is missing fields")
        proposal = cls(
            version=payload.get("version", CAPABILITY_GAP_PROPOSAL_VERSION),
            campaign_id=payload["campaign_id"], case_id=payload["case_id"],
            gap_id=payload["gap_id"], mechanism_family=payload["mechanism_family"],
            compatibility_profile=payload["compatibility_profile"],
            missing_asset_types=tuple(payload["missing_asset_types"]),
            operation=payload["operation"], proposal_kind=payload["proposal_kind"],
            derivation_receipt_id=payload["derivation_receipt_id"],
            admission_receipt_id=payload["admission_receipt_id"],
            evidence_transition_ids=tuple(payload["evidence_transition_ids"]),
            evidence_lineages=tuple(payload["evidence_lineages"]),
            evaluation_only=payload["evaluation_only"],
            canonical_memory_mutation=payload["canonical_memory_mutation"],
            production_runtime_eligible=payload["production_runtime_eligible"],
        )
        supplied_digest = payload.get("proposal_digest")
        if supplied_digest is not None and supplied_digest != proposal.proposal_digest:
            raise CapabilityGapProposalError("capability gap proposal digest mismatch")
        supplied_id = payload.get("proposal_id")
        if supplied_id is not None and supplied_id != proposal.proposal_id:
            raise CapabilityGapProposalError("capability gap proposal ID mismatch")
        return proposal


def propose_capability_gap_expansion(
        gap: CapabilityGapReceipt | Mapping,
        derivation: EvolutionReasonDerivationReceipt,
        admission: EvolutionAdmissionReceipt,
        *, proposal_kind: str = "ASSET_OR_KNOWLEDGE",
        failure_transition_ids: tuple[str, ...] | list[str] | None = None,
        routing: MemoryRoutingDecision | Mapping | None = None,
) -> CapabilityGapEvolutionProposal:
    """Build a shadow ADD proposal only after the typed non-P12 gate admits."""
    if not isinstance(derivation, EvolutionReasonDerivationReceipt):
        raise CapabilityGapProposalError("capability gap proposal derivation is invalid")
    if not isinstance(admission, EvolutionAdmissionReceipt):
        raise CapabilityGapProposalError("capability gap proposal admission is invalid")
    if not admission.admitted or admission.reason != "CAPABILITY_GAP":
        raise CapabilityGapProposalError("capability gap proposal requires admitted CAPABILITY_GAP")
    try:
        checked = (gap if isinstance(gap, CapabilityGapReceipt) else
                   CapabilityGapReceipt.from_dict(gap))
        checked_derivation = derive_capability_gap_reason(
            checked, campaign_id=admission.campaign_id, case_id=admission.case_id,
            failure_transition_ids=failure_transition_ids, routing=routing)
    except EvolutionReasonDerivationError as exc:
        # Preserve the typed derivation boundary in the proposal error.  In
        # particular, a caller cannot supply failure IDs outside the gap and
        # receive an indistinguishable generic parse error.
        if "outside the gap receipt" in str(exc):
            raise CapabilityGapProposalError(
                "capability gap proposal failure evidence does not match gap receipt") from exc
        raise CapabilityGapProposalError(
            "capability gap proposal input evidence is invalid") from exc
    except (TypeError, ValueError) as exc:
        raise CapabilityGapProposalError(
            "capability gap proposal input evidence is invalid") from exc
    if checked_derivation is None or checked_derivation.receipt_digest != derivation.receipt_digest:
        raise CapabilityGapProposalError("capability gap derivation does not match proposal evidence")
    if admission.derivation_receipt_ids != (derivation.receipt_id,):
        raise CapabilityGapProposalError("capability gap admission is not bound to derivation")
    if proposal_kind not in PROPOSAL_KINDS:
        raise CapabilityGapProposalError("capability gap proposal kind is invalid")
    return CapabilityGapEvolutionProposal(
        campaign_id=admission.campaign_id, case_id=admission.case_id,
        gap_id=checked.gap_id, mechanism_family=checked.mechanism_family,
        compatibility_profile=checked.compatibility_profile,
        missing_asset_types=checked.missing_asset_types, operation="ADD",
        proposal_kind=proposal_kind,
        derivation_receipt_id=derivation.receipt_id,
        admission_receipt_id=admission.receipt_id,
        evidence_transition_ids=checked.evidence_transitions,
        evidence_lineages=checked.evidence_lineages)


def propose_capability_gap_asset(
        gap: CapabilityGapReceipt | Mapping,
        derivation: EvolutionReasonDerivationReceipt,
        admission: EvolutionAdmissionReceipt, *,
        failure_transition_ids: tuple[str, ...] | list[str] | None = None,
        routing: MemoryRoutingDecision | Mapping | None = None,
) -> CapabilityGapEvolutionProposal:
    return propose_capability_gap_expansion(
        gap, derivation, admission, proposal_kind="ASSET",
        failure_transition_ids=failure_transition_ids, routing=routing)


def propose_capability_gap_knowledge(
        gap: CapabilityGapReceipt | Mapping,
        derivation: EvolutionReasonDerivationReceipt,
        admission: EvolutionAdmissionReceipt, *,
        failure_transition_ids: tuple[str, ...] | list[str] | None = None,
        routing: MemoryRoutingDecision | Mapping | None = None,
) -> CapabilityGapEvolutionProposal:
    return propose_capability_gap_expansion(
        gap, derivation, admission, proposal_kind="KNOWLEDGE",
        failure_transition_ids=failure_transition_ids, routing=routing)


__all__ = [
    "CAPABILITY_GAP_PROPOSAL_VERSION", "PROPOSAL_KINDS",
    "CapabilityGapProposalError", "CapabilityGapEvolutionProposal",
    "propose_capability_gap_expansion", "propose_capability_gap_asset",
    "propose_capability_gap_knowledge",
]
