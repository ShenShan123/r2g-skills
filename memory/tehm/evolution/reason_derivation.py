"""Deterministic, mutation-independent evolution-reason derivation (P12-R).

This module consumes immutable typed receipts and emits a content-addressed
reason witness.  It deliberately has no mutation-plan or canonical-memory
inputs: a detector can establish *why* evolution is worth considering, but it
cannot select or authorize the mutation itself.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from contracts import MEMORY_ROUTING_DECISIONS, MemoryRoutingDecision
from tehm.canonical.transition import HARMFUL_OUTCOMES, POSITIVE_OUTCOMES
from tehm.evaluation.candidate_executor import (
    P12_ARMS, CandidateExecutionReceipt, PairedCandidateExecutionReceipt,
)
from tehm.ids import stable_dumps
from tehm.state.shift_receipts import StateShiftReceipt
from contracts import MemoryRoutingDecision
from tehm.assets.receipts import CapabilityGapReceipt
from .conflict import ConflictReceipt
from .novelty import NoveltyReceipt


EVOLUTION_REASON_DERIVATION_VERSION = "evolution-reason-derivation-v0.1"
DERIVATION_MODES = frozenset({
    "EX_ANTE", "EX_POST_COUNTERFACTUAL", "AGGREGATED_EVENT",
})
EVOLUTION_REASONS = frozenset({
    "NOVELTY", "CONFLICT", "COUNTEREXAMPLE", "REPEATED_FAILURE",
    "CAPABILITY_GAP", "MEMORY_INTERFERENCE", "STATE_SHIFT",
})
_FORBIDDEN_MUTATION_FIELDS = frozenset({
    "localized_update_plan", "mutation_plan", "replacement_knowledge",
    "replacement_asset", "shadow_after_state", "production_authority",
})


class EvolutionReasonDerivationError(ValueError):
    """Typed derivation evidence is malformed or incomplete."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _text(value: object, field_name: str) -> str:
    if type(value) is not str or not value.strip():
        raise EvolutionReasonDerivationError(
            f"evolution reason derivation {field_name} is required")
    return value.strip()


def _digest_text(value: object, field_name: str) -> str:
    text = _text(value, field_name)
    if not text.startswith("sha256:") or len(text) <= len("sha256:"):
        raise EvolutionReasonDerivationError(
            f"evolution reason derivation {field_name} must be a sha256 digest")
    return text


def _strings(value: object, field_name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
        raise EvolutionReasonDerivationError(
            f"evolution reason derivation {field_name} must be a sequence")
    values = tuple(_text(item, field_name) for item in value)
    if not allow_empty and not values:
        raise EvolutionReasonDerivationError(
            f"evolution reason derivation {field_name} must not be empty")
    if len(set(values)) != len(values):
        raise EvolutionReasonDerivationError(
            f"evolution reason derivation {field_name} contains duplicates")
    return values


@dataclass(frozen=True)
class EvolutionReasonDerivationReceipt:
    """Content-addressed reason evidence with no mutation authority."""

    campaign_id: str
    case_id: str
    reason: str
    derivation_mode: str
    detector_name: str
    detector_version: str
    input_receipt_ids: tuple[str, ...]
    input_digests: tuple[str, ...]
    lineage_ids: tuple[str, ...]
    resolved_state_ids: tuple[str, ...]
    mutation_independent: bool = True
    evaluation_only: bool = True
    canonical_memory_mutation: str = "none"
    version: str = EVOLUTION_REASON_DERIVATION_VERSION

    def __post_init__(self) -> None:
        for field_name in (
                "campaign_id", "case_id", "detector_name", "detector_version"):
            object.__setattr__(self, field_name, _text(getattr(self, field_name), field_name))
        if self.reason not in EVOLUTION_REASONS:
            raise EvolutionReasonDerivationError("evolution reason is invalid")
        if self.derivation_mode not in DERIVATION_MODES:
            raise EvolutionReasonDerivationError("evolution reason derivation mode is invalid")
        if self.version != EVOLUTION_REASON_DERIVATION_VERSION:
            raise EvolutionReasonDerivationError("evolution reason derivation version is invalid")
        receipt_ids = _strings(self.input_receipt_ids, "input_receipt_ids")
        if not isinstance(self.input_digests, (list, tuple)):
            raise EvolutionReasonDerivationError(
                "evolution reason derivation input_digests must be a sequence")
        digests = tuple(_digest_text(item, "input_digests")
                        for item in self.input_digests)
        if len(receipt_ids) != len(digests):
            raise EvolutionReasonDerivationError(
                "input receipt IDs and digests must align")
        lineages = _strings(self.lineage_ids, "lineage_ids")
        states = _strings(self.resolved_state_ids, "resolved_state_ids", allow_empty=True)
        if type(self.mutation_independent) is not bool or not self.mutation_independent:
            raise EvolutionReasonDerivationError(
                "evolution reason derivation must be mutation-independent")
        if type(self.evaluation_only) is not bool or not self.evaluation_only:
            raise EvolutionReasonDerivationError(
                "evolution reason derivation must be evaluation-only")
        if self.canonical_memory_mutation != "none":
            raise EvolutionReasonDerivationError(
                "evolution reason derivation cannot mutate canonical memory")
        object.__setattr__(self, "input_receipt_ids", receipt_ids)
        object.__setattr__(self, "input_digests", digests)
        object.__setattr__(self, "lineage_ids", lineages)
        object.__setattr__(self, "resolved_state_ids", states)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "campaign_id": self.campaign_id,
            "case_id": self.case_id,
            "reason": self.reason,
            "derivation_mode": self.derivation_mode,
            "detector_name": self.detector_name,
            "detector_version": self.detector_version,
            "input_receipt_ids": list(self.input_receipt_ids),
            "input_digests": list(self.input_digests),
            "lineage_ids": list(self.lineage_ids),
            "resolved_state_ids": list(self.resolved_state_ids),
            "mutation_independent": self.mutation_independent,
            "evaluation_only": self.evaluation_only,
            "canonical_memory_mutation": self.canonical_memory_mutation,
        }

    @property
    def receipt_digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def receipt_id(self) -> str:
        return "evolution_reason_derivation_" + self.receipt_digest.split(":", 1)[1][:24]

    @classmethod
    def from_dict(cls, payload: object) -> "EvolutionReasonDerivationReceipt":
        if not isinstance(payload, Mapping):
            raise EvolutionReasonDerivationError(
                "evolution reason derivation receipt must be an object")
        forbidden = sorted(_FORBIDDEN_MUTATION_FIELDS & set(payload))
        if forbidden:
            raise EvolutionReasonDerivationError(
                "evolution reason derivation contains forbidden mutation field: "
                + ",".join(forbidden))
        required = {
            "version", "campaign_id", "case_id", "reason", "derivation_mode",
            "detector_name", "detector_version", "input_receipt_ids",
            "input_digests", "lineage_ids", "resolved_state_ids",
            "mutation_independent", "evaluation_only", "canonical_memory_mutation",
        }
        if not required <= set(payload):
            raise EvolutionReasonDerivationError(
                "evolution reason derivation receipt is missing fields")
        receipt = cls(
            version=payload["version"], campaign_id=payload["campaign_id"],
            case_id=payload["case_id"], reason=payload["reason"],
            derivation_mode=payload["derivation_mode"],
            detector_name=payload["detector_name"],
            detector_version=payload["detector_version"],
            input_receipt_ids=tuple(payload["input_receipt_ids"]),
            input_digests=tuple(payload["input_digests"]),
            lineage_ids=tuple(payload["lineage_ids"]),
            resolved_state_ids=tuple(payload["resolved_state_ids"]),
            mutation_independent=payload["mutation_independent"],
            evaluation_only=payload["evaluation_only"],
            canonical_memory_mutation=payload["canonical_memory_mutation"],
        )
        supplied = payload.get("receipt_digest")
        if supplied is not None and supplied != receipt.receipt_digest:
            raise EvolutionReasonDerivationError(
                "evolution reason derivation receipt digest mismatch")
        supplied_id = payload.get("receipt_id")
        if supplied_id is not None and supplied_id != receipt.receipt_id:
            raise EvolutionReasonDerivationError(
                "evolution reason derivation receipt ID mismatch")
        return receipt


def _state_shift_receipt(value: object) -> StateShiftReceipt:
    if isinstance(value, StateShiftReceipt):
        return value
    try:
        return StateShiftReceipt.from_dict(value)
    except (TypeError, ValueError, KeyError) as exc:
        raise EvolutionReasonDerivationError(
            "state shift reason input receipt is invalid") from exc


def _capability_gap_receipt(value: object) -> CapabilityGapReceipt:
    if isinstance(value, CapabilityGapReceipt):
        return value
    try:
        return CapabilityGapReceipt.from_dict(value)
    except (TypeError, ValueError, KeyError) as exc:
        raise EvolutionReasonDerivationError(
            "capability gap reason input receipt is invalid") from exc


def _capability_gap_route(value: object) -> MemoryRoutingDecision:
    if isinstance(value, MemoryRoutingDecision):
        route = value
    else:
        try:
            route = MemoryRoutingDecision.from_dict(value)
        except (TypeError, ValueError, KeyError) as exc:
            raise EvolutionReasonDerivationError(
                "capability gap reason route is invalid") from exc
    if (route.decision != "NO_SKILL" or route.no_skill_reason != "NO_MATCH" or
            route.selected_asset_ids or route.memory_budget != 0):
        raise EvolutionReasonDerivationError(
            "capability gap reason requires NO_SKILL/NO_MATCH route")
    return route


def _novelty_receipt(value: object) -> NoveltyReceipt:
    if isinstance(value, NoveltyReceipt):
        return value
    try:
        return NoveltyReceipt.from_dict(value)
    except (TypeError, ValueError, KeyError) as exc:
        raise EvolutionReasonDerivationError(
            "novelty reason input receipt is invalid") from exc


def _conflict_receipt(value: object) -> ConflictReceipt:
    if isinstance(value, ConflictReceipt):
        return value
    try:
        return ConflictReceipt.from_dict(value)
    except (TypeError, ValueError, KeyError) as exc:
        raise EvolutionReasonDerivationError(
            "conflict reason input receipt is invalid") from exc


def derive_state_shift_reason(
        shift: StateShiftReceipt | Mapping, *, campaign_id: str, case_id: str,
        routing: MemoryRoutingDecision,
        lineage_id: str) -> EvolutionReasonDerivationReceipt | None:
    """Adapt a non-transferable typed state-shift receipt into reason evidence."""
    checked = _state_shift_receipt(shift)
    if not isinstance(routing, MemoryRoutingDecision):
        raise EvolutionReasonDerivationError("state shift reason routing is invalid")
    _text(campaign_id, "campaign_id")
    _text(case_id, "case_id")
    _text(lineage_id, "lineage_id")
    if checked.reason != "STATE_SHIFT" or checked.transferable:
        return None
    if (routing.decision != "NO_SKILL" or
            routing.no_skill_reason != "STATE_SHIFT" or
            routing.state_shift_receipt_id != checked.receipt_id or
            routing.state_shift_receipt is None or
            routing.resolved_state_id != checked.current_resolution_id):
        raise EvolutionReasonDerivationError(
            "state shift reason route binding is invalid")
    return EvolutionReasonDerivationReceipt(
        campaign_id=campaign_id, case_id=case_id, reason="STATE_SHIFT",
        derivation_mode="EX_ANTE",
        detector_name="state_shift_receipt_adapter",
        detector_version="state-shift-reason-v1",
        input_receipt_ids=(checked.receipt_id, routing.routing_receipt_id),
        input_digests=(checked.replay_digest, routing.decision_digest),
        lineage_ids=(lineage_id,),
        resolved_state_ids=(checked.current_resolution_id,))


def derive_capability_gap_reason(
        gap: CapabilityGapReceipt | Mapping, *, campaign_id: str, case_id: str,
        min_lineages: int = 2, min_failures: int = 2,
        failure_transition_ids: tuple[str, ...] | list[str] | None = None,
        routing: MemoryRoutingDecision | Mapping | None = None,
        detector_version: str = "capability-gap-reason-v1",
) -> EvolutionReasonDerivationReceipt | None:
    """Derive a non-P12 reason from an aggregated capability-gap receipt.

    The gap detector consumes learner-eligible training transitions.  This
    adapter rechecks the ``NO_SKILL/NO_MATCH`` route, independent lineages,
    repeated source-failure evidence, and the absence of a currently
    authorized asset/action family.  It has no paired-candidate input because
    ``NO_SKILL_NO_MATCH`` has no memory candidate on which a counterfactual
    could run.
    """
    checked = _capability_gap_receipt(gap)
    if routing is None:
        raise EvolutionReasonDerivationError(
            "capability gap reason requires NO_SKILL/NO_MATCH route")
    checked_route = _capability_gap_route(routing)
    campaign_id = _text(campaign_id, "campaign_id")
    case_id = _text(case_id, "case_id")
    if min_lineages < 1 or min_failures < 1:
        raise EvolutionReasonDerivationError(
            "capability gap thresholds must be positive")
    reasons = {item.strip() for item in checked.reason.split("+") if item.strip()}
    if "repeated_unsupported_mechanism" not in reasons:
        return None
    lineages = tuple(dict.fromkeys(
        _text(item, "evidence_lineages") for item in checked.evidence_lineages))
    transitions = tuple(dict.fromkeys(
        _text(item, "evidence_transitions") for item in checked.evidence_transitions))
    if len(lineages) < min_lineages:
        return None
    coverage = checked.current_action_coverage
    if not isinstance(coverage, Mapping):
        raise EvolutionReasonDerivationError(
            "capability gap current_action_coverage is invalid")
    try:
        failure_count = int(coverage.get("failure_evidence", 0))
        initial_failure_count = int(coverage.get("initial_failure_evidence", 0))
        unresolved_failure_count = int(coverage.get("failures", 0))
    except (TypeError, ValueError) as exc:
        raise EvolutionReasonDerivationError(
            "capability gap failure evidence counts are invalid") from exc
    # ``initial_failure_evidence`` covers independently repaired source
    # failures (original_failure=REMOVED); unresolved FAIL/REGRESSION is kept
    # separate but contributes to the same repeated-failure threshold.
    if failure_count < min_failures or (
            initial_failure_count + unresolved_failure_count < min_failures):
        return None
    if bool(coverage.get("promoted_asset", False)) or bool(
            coverage.get("promoted_rule", False)):
        return None
    current_success = bool(coverage.get("successful_action_family", False))
    if "successful_action_families" in coverage:
        families = coverage.get("successful_action_families")
        if not isinstance(families, (list, tuple, set, frozenset)):
            raise EvolutionReasonDerivationError(
                "capability gap successful_action_families is invalid")
        current_success = current_success or bool(families)
    if current_success or not transitions:
        return None
    if failure_transition_ids is None:
        selected_failures = transitions[:min(failure_count, len(transitions))]
    else:
        if not isinstance(failure_transition_ids, (list, tuple)):
            raise EvolutionReasonDerivationError(
                "capability gap failure_transition_ids must be a sequence")
        selected_failures = tuple(dict.fromkeys(
            _text(item, "failure_transition_ids") for item in failure_transition_ids))
        if not set(selected_failures) <= set(transitions):
            raise EvolutionReasonDerivationError(
                "capability gap failure evidence is outside the gap receipt")
    if len(selected_failures) < min_failures:
        return None
    input_ids = (checked.receipt_id, checked_route.routing_receipt_id, *(
        "transition_evidence:" + item for item in selected_failures))
    input_digests = (
        checked.receipt_digest,
        checked_route.decision_digest,
        *(_digest({"transition_id": item, "gap_id": checked.gap_id})
          for item in selected_failures),
    )
    return EvolutionReasonDerivationReceipt(
        campaign_id=campaign_id, case_id=case_id, reason="CAPABILITY_GAP",
        derivation_mode="AGGREGATED_EVENT",
        detector_name="capability_gap_aggregator",
        detector_version=detector_version,
        input_receipt_ids=input_ids, input_digests=input_digests,
        lineage_ids=lineages, resolved_state_ids=())


def derive_novelty_reason(
        novelty: NoveltyReceipt | Mapping, *, campaign_id: str, case_id: str,
        detector_version: str = "novelty-reason-v1",
) -> EvolutionReasonDerivationReceipt | None:
    """Adapt the existing learner/audit novelty detector into reason evidence."""
    checked = _novelty_receipt(novelty)
    campaign_id = _text(campaign_id, "campaign_id")
    case_id = _text(case_id, "case_id")
    if checked.campaign_id != campaign_id:
        raise EvolutionReasonDerivationError("novelty reason campaign mismatch")
    if checked.status != "NOVEL_MECHANISM" or checked.path_exists:
        return None
    if not checked.lineage_id:
        raise EvolutionReasonDerivationError(
            "novelty reason requires a lineage witness")
    return EvolutionReasonDerivationReceipt(
        campaign_id=campaign_id, case_id=case_id, reason="NOVELTY",
        derivation_mode="EX_ANTE", detector_name="novelty_receipt_adapter",
        detector_version=detector_version,
        input_receipt_ids=(checked.receipt_id,
                           "transition_evidence:" + checked.transition_id),
        input_digests=(checked.receipt_digest,
                       _digest({"transition_id": checked.transition_id,
                                "campaign_id": campaign_id})),
        lineage_ids=(checked.lineage_id,), resolved_state_ids=())


def derive_conflict_reason(
        conflict: ConflictReceipt | Mapping, *, campaign_id: str, case_id: str,
        detector_version: str = "conflict-reason-v1",
) -> EvolutionReasonDerivationReceipt | None:
    """Adapt a typed conflict witness into a mutation-independent reason."""
    checked = _conflict_receipt(conflict)
    campaign_id = _text(campaign_id, "campaign_id")
    case_id = _text(case_id, "case_id")
    if checked.campaign_id != campaign_id:
        raise EvolutionReasonDerivationError("conflict reason campaign mismatch")
    if not checked.has_conflict:
        return None
    if not checked.lineage_id:
        raise EvolutionReasonDerivationError(
            "conflict reason requires a lineage witness")
    evidence = tuple(dict.fromkeys(checked.evidence_transition_ids))
    if not evidence:
        raise EvolutionReasonDerivationError(
            "conflict reason requires conflicting transition evidence")
    input_ids = (checked.receipt_id, *(
        "transition_evidence:" + item for item in evidence))
    input_digests = (
        checked.receipt_digest,
        *(_digest({"transition_id": item, "campaign_id": campaign_id})
          for item in evidence),
    )
    return EvolutionReasonDerivationReceipt(
        campaign_id=campaign_id, case_id=case_id, reason="CONFLICT",
        derivation_mode="AGGREGATED_EVENT",
        detector_name="conflict_receipt_adapter",
        detector_version=detector_version,
        input_receipt_ids=input_ids, input_digests=input_digests,
        lineage_ids=(checked.lineage_id,), resolved_state_ids=())


def _oracle_complete(receipt: CandidateExecutionReceipt) -> bool:
    return (
        receipt.evaluation_only is True and
        receipt.metadata.get("oracle_available") is True and
        receipt.compile_result != "UNKNOWN" and
        receipt.functional_result != "UNKNOWN" and
        receipt.signoff_result not in {None, "UNKNOWN"} and
        receipt.outcome != "UNKNOWN")


def derive_memory_interference_reason(
        paired: PairedCandidateExecutionReceipt, *, campaign_id: str,
        memory_arm: str = "ALWAYS_MEMORY") -> EvolutionReasonDerivationReceipt | None:
    """Derive interference only from a complete baseline/forced-memory pair."""
    if not isinstance(paired, PairedCandidateExecutionReceipt):
        raise EvolutionReasonDerivationError("memory interference paired receipt is invalid")
    if memory_arm not in P12_ARMS[1:]:
        raise EvolutionReasonDerivationError("memory interference memory_arm is invalid")
    baseline = paired.arm_receipts["NO_MEMORY"]
    memory = paired.arm_receipts[memory_arm]
    if baseline.source != "no_memory" or memory.source != "structured_memory":
        return None
    if not _oracle_complete(baseline) or not _oracle_complete(memory):
        raise EvolutionReasonDerivationError(
            "memory interference requires complete oracle receipts")
    if baseline.outcome not in POSITIVE_OUTCOMES:
        return None
    harmful = (memory.outcome in HARMFUL_OUTCOMES or
               bool(memory.created_regressions))
    if not harmful:
        return None
    if type(paired.lineage_id) is not str or not paired.lineage_id.strip():
        raise EvolutionReasonDerivationError(
            "memory interference requires explicit lineage_id")
    _text(campaign_id, "campaign_id")
    return EvolutionReasonDerivationReceipt(
        campaign_id=campaign_id, case_id=paired.case_id,
        reason="MEMORY_INTERFERENCE", derivation_mode="EX_POST_COUNTERFACTUAL",
        detector_name="paired_memory_interference",
        detector_version="memory-interference-v1",
        input_receipt_ids=(paired.receipt_digest,
                           baseline.execution_digest, memory.execution_digest),
        input_digests=(paired.receipt_digest,
                       baseline.execution_digest, memory.execution_digest),
        lineage_ids=(paired.lineage_id.strip(),), resolved_state_ids=())


def p13_reason_receipt_from_derivations(
        derivations: Mapping[str, tuple[EvolutionReasonDerivationReceipt, ...]] | Mapping,
        *, campaign_id: str, cohort_receipt_digest: str):
    """Aggregate typed detector receipts into the existing P13 reason envelope.

    The returned ``P13EvolutionReasonReceipt`` remains a provenance envelope:
    it carries detector receipt references but grants no mutation authority.
    Every case must have at least one typed derivation, and no manually supplied
    reason label is accepted by this path.
    """
    if not isinstance(derivations, Mapping) or not derivations:
        raise EvolutionReasonDerivationError(
            "typed evolution derivations must be a non-empty object")
    campaign_id = _text(campaign_id, "campaign_id")
    cohort_receipt_digest = _digest_text(
        cohort_receipt_digest, "cohort_receipt_digest")
    reasons: dict[str, tuple[str, ...]] = {}
    evidence_refs: list[dict[str, str]] = []
    case_evidence_refs: dict[str, tuple[dict[str, str], ...]] = {}
    detector_names: set[str] = set()
    seen_receipts: set[str] = set()
    for raw_case_id, raw_items in sorted(derivations.items()):
        case_id = _text(raw_case_id, "case_id")
        if not isinstance(raw_items, (list, tuple)) or not raw_items:
            raise EvolutionReasonDerivationError(
                f"typed evolution derivations for {case_id} must not be empty")
        items: list[EvolutionReasonDerivationReceipt] = []
        refs: list[dict[str, str]] = []
        case_reasons: set[str] = set()
        for item in raw_items:
            if not isinstance(item, EvolutionReasonDerivationReceipt):
                raise EvolutionReasonDerivationError(
                    f"typed evolution derivation for {case_id} is invalid")
            if item.campaign_id != campaign_id or item.case_id != case_id:
                raise EvolutionReasonDerivationError(
                    f"typed evolution derivation binding is invalid for {case_id}")
            if item.receipt_id in seen_receipts:
                raise EvolutionReasonDerivationError(
                    "typed evolution derivations contain duplicate receipts")
            seen_receipts.add(item.receipt_id)
            detector_names.add(item.detector_name)
            case_reasons.add(item.reason)
            ref = {
                "path": "receipt://" + item.receipt_id,
                "sha256": item.receipt_digest,
                "id": item.receipt_id,
            }
            refs.append(ref)
            evidence_refs.append(ref)
            items.append(item)
        reasons[case_id] = tuple(sorted(case_reasons))
        case_evidence_refs[case_id] = tuple(refs)
    if not reasons:
        raise EvolutionReasonDerivationError("typed evolution derivations are empty")
    try:
        from .p12_shadow_trigger import P13EvolutionReasonReceipt

        return P13EvolutionReasonReceipt(
            campaign_id=campaign_id,
            cohort_receipt_digest=cohort_receipt_digest,
            label_source=("typed-detector:" + "+".join(sorted(detector_names))),
            evidence_refs=tuple(evidence_refs),
            evolution_reasons=reasons,
            case_evidence_refs=case_evidence_refs,
        )
    except (TypeError, ValueError) as exc:
        raise EvolutionReasonDerivationError(
            "typed evolution reason aggregation failed") from exc


__all__ = [
    "EVOLUTION_REASON_DERIVATION_VERSION", "DERIVATION_MODES",
    "EVOLUTION_REASONS", "EvolutionReasonDerivationError",
    "EvolutionReasonDerivationReceipt", "derive_state_shift_reason",
    "derive_capability_gap_reason", "derive_novelty_reason",
    "derive_conflict_reason", "derive_memory_interference_reason",
    "p13_reason_receipt_from_derivations",
]
