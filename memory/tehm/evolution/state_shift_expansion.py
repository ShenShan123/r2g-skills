"""Derive a shadow Knowledge/support-envelope expansion from real P12 evidence.

The StateShift proposal says *why* a revision is warranted.  This module
defines the missing semantic step: learner-partitioned, oracle-complete target
executions must explain exactly which shifted support facts are added.  The
result is still a proposal for isolated staging; it grants no lifecycle or
production authority and never writes SQLite.
"""
from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tehm.evaluation.counterfactual_oracle import counterfactual_oracle_complete
from tehm.evaluation.orfs_cohort import OrfsPairedCohortReceipt
from tehm.ids import stable_dumps
from tehm.knowledge import MechanismKnowledge
from tehm.state import SupportEnvelope, StateShiftReceipt, evaluate_state_shift
from tehm.state.receipts import ResolvedMemoryState


STATE_SHIFT_SUPPORT_EXPANSION_VERSION = "state-shift-support-expansion-v0.1"
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_DIMENSION_KEYS = {
    "structural_shift": (
        "structural_graph_digest", "structural_signature",
        "structural_graph", "structure"),
    "flow_shift": ("flow_regime", "platform", "toolchain_digest", "orfs_root"),
    "constraint_shift": (
        "constraint_regime", "constraints", "constraint_digest",
        "timing_target", "obligation_set"),
    "oracle_shift": (
        "oracle_regime", "oracle_type", "oracle_digest",
        "verification_regime", "obligations"),
    "history_shift": ("action_history", "prior_action_digests"),
}
_CANONICAL_CONTEXT_KEY = {
    "structural_shift": "structural_signature",
    "mechanism_shift": "mechanism_signature",
    "flow_shift": "flow_regime",
    "constraint_shift": "constraint_regime",
    "oracle_shift": "oracle_regime",
    "history_shift": "action_history",
}


class StateShiftSupportExpansionError(ValueError):
    """StateShift evidence cannot justify the proposed support expansion."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _sha(value: object, name: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise StateShiftSupportExpansionError(
            f"state-shift expansion {name} must be sha256")
    return value


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise StateShiftSupportExpansionError(
            f"state-shift expansion {name} is required")
    return value.strip()


def _mapping(value: object, name: str) -> dict:
    if not isinstance(value, Mapping):
        raise StateShiftSupportExpansionError(
            f"state-shift expansion {name} must be an object")
    try:
        result = __import__("json").loads(stable_dumps(dict(value)))
    except (TypeError, ValueError) as exc:
        raise StateShiftSupportExpansionError(
            f"state-shift expansion {name} is not JSON-serializable") from exc
    return result


def _values(value: object) -> tuple:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        return (dict(value),)
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(value)
    return (value,)


def _dimension_values(context: Mapping, dimension: str) -> tuple:
    if dimension == "mechanism_shift":
        signature = context.get("mechanism_signature")
        if isinstance(signature, Mapping):
            return (dict(signature),)
        if ("mechanism_family" in context or
                "compatibility_profile" in context):
            return ({
                "family": context.get("mechanism_family"),
                "profile": context.get("compatibility_profile"),
            },)
        return ()
    for key in _DIMENSION_KEYS[dimension]:
        if key in context and context[key] is not None:
            return _values(context[key])
    return ()


def _append_unique(values: list, additions: Sequence) -> list:
    seen = {stable_dumps(item) for item in values}
    for item in additions:
        encoded = stable_dumps(item)
        if encoded not in seen:
            values.append(copy.deepcopy(item))
            seen.add(encoded)
    return values


def _partition(
    raw: object,
    *,
    case_ids: set[str],
) -> dict[str, dict]:
    if isinstance(raw, Mapping):
        items = []
        for case_id, value in raw.items():
            if not isinstance(value, Mapping):
                raise StateShiftSupportExpansionError(
                    "state-shift expansion learner partition is malformed")
            item = dict(value)
            embedded_case_id = item.get("case_id")
            if embedded_case_id not in {None, case_id}:
                raise StateShiftSupportExpansionError(
                    "state-shift expansion learner partition case identity conflicts")
            item["case_id"] = case_id
            items.append(item)
    elif (isinstance(raw, Sequence) and
          not isinstance(raw, (str, bytes))):
        items = list(raw)
    else:
        raise StateShiftSupportExpansionError(
            "state-shift expansion learner partition must be a sequence")
    result = {}
    for item in items:
        if not isinstance(item, Mapping):
            raise StateShiftSupportExpansionError(
                "state-shift expansion learner partition item is malformed")
        case_id = item.get("case_id")
        if (type(case_id) is not str or not case_id or case_id in result or
                item.get("dataset_split") != "training" or
                item.get("role") != "training" or
                item.get("learner_eligible") is not True):
            raise StateShiftSupportExpansionError(
                "state-shift expansion requires unique learner-eligible training cases")
        result[case_id] = dict(item)
    if set(result) != case_ids:
        raise StateShiftSupportExpansionError(
            "state-shift expansion learner partition does not cover the cohort")
    return dict(sorted(result.items()))


@dataclass(frozen=True)
class StateShiftSupportExpansionReceipt:
    campaign_id: str
    proposal_digest: str
    cohort_receipt_digest: str
    parent_knowledge_object_id: str
    parent_knowledge_digest: str
    child_knowledge_object_id: str
    child_knowledge_digest: str
    parent_support_envelope_digest: str
    child_support_envelope_digest: str
    case_lineages: dict[str, str]
    target_execution_digests: dict[str, str]
    state_context_digests: dict[str, str]
    admitted_state_shift_digests: dict[str, str]
    before_state_shift_digests: dict[str, str]
    after_state_shift_digests: dict[str, str]
    expanded_dimensions: tuple[str, ...]
    evaluation_only: bool = True
    shadow_only: bool = True
    canonical_memory_mutation: str = "none"
    production_runtime_imported: bool = False
    version: str = STATE_SHIFT_SUPPORT_EXPANSION_VERSION

    def __post_init__(self) -> None:
        for value, name in (
                (self.campaign_id, "campaign_id"),
                (self.parent_knowledge_object_id, "parent_knowledge_object_id"),
                (self.child_knowledge_object_id, "child_knowledge_object_id")):
            _text(value, name)
        for value, name in (
                (self.proposal_digest, "proposal_digest"),
                (self.cohort_receipt_digest, "cohort_receipt_digest"),
                (self.parent_knowledge_digest, "parent_knowledge_digest"),
                (self.child_knowledge_digest, "child_knowledge_digest"),
                (self.parent_support_envelope_digest,
                 "parent_support_envelope_digest"),
                (self.child_support_envelope_digest,
                 "child_support_envelope_digest")):
            _sha(value, name)
        if self.parent_knowledge_object_id == self.child_knowledge_object_id:
            raise StateShiftSupportExpansionError(
                "state-shift expansion must create a new Knowledge version")
        maps = (
            self.case_lineages, self.target_execution_digests,
            self.state_context_digests, self.admitted_state_shift_digests,
            self.before_state_shift_digests,
            self.after_state_shift_digests,
        )
        if any(not isinstance(value, dict) for value in maps):
            raise StateShiftSupportExpansionError(
                "state-shift expansion receipt mappings are malformed")
        cases = set(self.case_lineages)
        if (len(cases) < 2 or any(set(value) != cases for value in maps[1:]) or
                len(set(self.case_lineages.values())) < 2):
            raise StateShiftSupportExpansionError(
                "state-shift expansion requires two source-disjoint lineages")
        for mapping in maps[1:]:
            for value in mapping.values():
                _sha(value, "case evidence digest")
        dimensions = tuple(sorted(self.expanded_dimensions))
        if (not dimensions or len(set(dimensions)) != len(dimensions) or
                any(item not in _CANONICAL_CONTEXT_KEY for item in dimensions)):
            raise StateShiftSupportExpansionError(
                "state-shift expansion dimensions are invalid")
        if (self.evaluation_only is not True or self.shadow_only is not True or
                self.canonical_memory_mutation != "none" or
                self.production_runtime_imported is not False or
                self.version != STATE_SHIFT_SUPPORT_EXPANSION_VERSION):
            raise StateShiftSupportExpansionError(
                "state-shift expansion crossed an authority boundary")
        object.__setattr__(self, "case_lineages", dict(sorted(
            self.case_lineages.items())))
        for name in (
                "target_execution_digests", "state_context_digests",
                "admitted_state_shift_digests",
                "before_state_shift_digests", "after_state_shift_digests"):
            object.__setattr__(self, name, dict(sorted(
                getattr(self, name).items())))
        object.__setattr__(self, "expanded_dimensions", dimensions)

    def _payload(self) -> dict:
        return {
            "version": self.version,
            "campaign_id": self.campaign_id,
            "proposal_digest": self.proposal_digest,
            "cohort_receipt_digest": self.cohort_receipt_digest,
            "parent_knowledge_object_id": self.parent_knowledge_object_id,
            "parent_knowledge_digest": self.parent_knowledge_digest,
            "child_knowledge_object_id": self.child_knowledge_object_id,
            "child_knowledge_digest": self.child_knowledge_digest,
            "parent_support_envelope_digest":
                self.parent_support_envelope_digest,
            "child_support_envelope_digest": self.child_support_envelope_digest,
            "case_lineages": self.case_lineages,
            "target_execution_digests": self.target_execution_digests,
            "state_context_digests": self.state_context_digests,
            "admitted_state_shift_digests":
                self.admitted_state_shift_digests,
            "before_state_shift_digests": self.before_state_shift_digests,
            "after_state_shift_digests": self.after_state_shift_digests,
            "expanded_dimensions": list(self.expanded_dimensions),
            "evaluation_only": self.evaluation_only,
            "shadow_only": self.shadow_only,
            "canonical_memory_mutation": self.canonical_memory_mutation,
            "production_runtime_imported": self.production_runtime_imported,
        }

    @property
    def receipt_digest(self) -> str:
        return _digest(self._payload())

    @property
    def receipt_id(self) -> str:
        return "state_shift_support_expansion_" + self.receipt_digest.split(
            ":", 1)[1][:24]

    def to_dict(self) -> dict:
        return {
            **self._payload(),
            "receipt_id": self.receipt_id,
            "receipt_digest": self.receipt_digest,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "StateShiftSupportExpansionReceipt":
        if not isinstance(payload, Mapping):
            raise StateShiftSupportExpansionError(
                "state-shift expansion receipt must be an object")
        required = set(cls.__dataclass_fields__) - {"version"}
        if not required <= set(payload):
            raise StateShiftSupportExpansionError(
                "state-shift expansion receipt is missing fields")
        receipt = cls(
            campaign_id=payload["campaign_id"],
            proposal_digest=payload["proposal_digest"],
            cohort_receipt_digest=payload["cohort_receipt_digest"],
            parent_knowledge_object_id=payload["parent_knowledge_object_id"],
            parent_knowledge_digest=payload["parent_knowledge_digest"],
            child_knowledge_object_id=payload["child_knowledge_object_id"],
            child_knowledge_digest=payload["child_knowledge_digest"],
            parent_support_envelope_digest=payload[
                "parent_support_envelope_digest"],
            child_support_envelope_digest=payload[
                "child_support_envelope_digest"],
            case_lineages=dict(payload["case_lineages"]),
            target_execution_digests=dict(payload[
                "target_execution_digests"]),
            state_context_digests=dict(payload["state_context_digests"]),
            admitted_state_shift_digests=dict(payload[
                "admitted_state_shift_digests"]),
            before_state_shift_digests=dict(payload[
                "before_state_shift_digests"]),
            after_state_shift_digests=dict(payload[
                "after_state_shift_digests"]),
            expanded_dimensions=tuple(payload["expanded_dimensions"]),
            evaluation_only=payload["evaluation_only"],
            shadow_only=payload["shadow_only"],
            canonical_memory_mutation=payload["canonical_memory_mutation"],
            production_runtime_imported=payload[
                "production_runtime_imported"],
            version=payload.get(
                "version", STATE_SHIFT_SUPPORT_EXPANSION_VERSION),
        )
        if payload.get("receipt_id") not in {None, receipt.receipt_id}:
            raise StateShiftSupportExpansionError(
                "state-shift expansion receipt ID mismatch")
        if payload.get("receipt_digest") not in {
                None, receipt.receipt_digest}:
            raise StateShiftSupportExpansionError(
                "state-shift expansion receipt digest mismatch")
        return receipt


def derive_state_shift_support_expansion(
    *,
    campaign_id: str,
    proposal_digest: str,
    parent_knowledge: MechanismKnowledge,
    parent_envelope: SupportEnvelope,
    resolved_state: ResolvedMemoryState | Mapping,
    cohort: OrfsPairedCohortReceipt,
    state_shifts: Mapping[str, StateShiftReceipt],
    current_contexts: Mapping[str, Mapping],
    learner_partition: Sequence[Mapping] | Mapping[str, Mapping],
) -> tuple[
        MechanismKnowledge, SupportEnvelope,
        StateShiftSupportExpansionReceipt]:
    """Build a same-claim shadow revision and prove the targeted shift closes."""
    campaign_id = _text(campaign_id, "campaign_id")
    proposal_digest = _sha(proposal_digest, "proposal_digest")
    if not isinstance(parent_knowledge, MechanismKnowledge):
        raise TypeError("state-shift expansion requires MechanismKnowledge")
    if not isinstance(parent_envelope, SupportEnvelope):
        raise TypeError("state-shift expansion requires SupportEnvelope")
    if not isinstance(cohort, OrfsPairedCohortReceipt):
        raise TypeError("state-shift expansion requires OrfsPairedCohortReceipt")
    if cohort.campaign_id != campaign_id:
        raise StateShiftSupportExpansionError(
            "state-shift expansion cohort campaign mismatch")
    if parent_envelope.knowledge_object_id != parent_knowledge.object_id:
        raise StateShiftSupportExpansionError(
            "state-shift expansion parent envelope mismatch")
    case_ids = set(cohort.case_receipts)
    if (len(case_ids) < 2 or set(state_shifts) != case_ids or
            set(current_contexts) != case_ids):
        raise StateShiftSupportExpansionError(
            "state-shift expansion requires exact multi-case evidence coverage")
    _partition(learner_partition, case_ids=case_ids)

    new_entries: list[dict] = []
    expanded: dict[str, list] = {}
    target_digests = {}
    context_digests = {}
    admitted_shift_digests = {}
    before_digests = {}
    after_receipts = {}
    lineages = {}
    for case_id in sorted(case_ids):
        pair = cohort.case_receipts[case_id]
        lineage = pair.lineage_id
        if type(lineage) is not str or not lineage:
            raise StateShiftSupportExpansionError(
                "state-shift expansion requires explicit case lineages")
        lineages[case_id] = lineage
        target = pair.arm_receipts["ALWAYS_MEMORY"]
        baseline = pair.arm_receipts["NO_MEMORY"]
        if (target.source != "structured_memory" or target.outcome != "PASS" or
                target.created_regressions or
                not counterfactual_oracle_complete(target) or
                baseline.source != "no_memory" or baseline.outcome != "PASS" or
                not counterfactual_oracle_complete(baseline)):
            raise StateShiftSupportExpansionError(
                "state-shift expansion target execution is not safe and complete")
        shift = state_shifts[case_id]
        context = _mapping(current_contexts[case_id], "current context")
        replay = evaluate_state_shift(
            context, resolved_state, parent_knowledge, parent_envelope,
            evidence_refs=shift.evidence_refs)
        admitted_semantics = shift.to_dict()
        replay_semantics = replay.to_dict()
        for payload in (admitted_semantics, replay_semantics):
            for name in (
                    "current_resolution_id", "replay_digest", "receipt_id"):
                payload.pop(name, None)
        if (shift.version != "state-shift-v0.2" or
                shift.reason != "STATE_SHIFT" or shift.transferable is not False or
                pair.state_shift_receipt_id != shift.receipt_id or
                replay.current_context_digest != shift.current_context_digest or
                replay_semantics != admitted_semantics):
            raise StateShiftSupportExpansionError(
                "state-shift expansion input receipt does not replay")
        for dimension in shift.shifted_dimensions:
            values = _dimension_values(context, dimension)
            if not values:
                raise StateShiftSupportExpansionError(
                    "state-shift expansion shifted dimension lacks current facts")
            expanded.setdefault(dimension, [])
            _append_unique(expanded[dimension], values)
            key = _CANONICAL_CONTEXT_KEY[dimension]
            for value in values:
                new_entries.append({key: copy.deepcopy(value)})
        target_digests[case_id] = target.execution_digest
        context_digests[case_id] = shift.current_context_digest
        admitted_shift_digests[case_id] = shift.replay_digest
        before_digests[case_id] = replay.replay_digest

    if len(set(lineages.values())) < 2:
        raise StateShiftSupportExpansionError(
            "state-shift expansion requires distinct learner lineages")
    positive = list(parent_knowledge.positive_applicability)
    _append_unique(positive, new_entries)
    child = MechanismKnowledge(
        knowledge_id=parent_knowledge.knowledge_id,
        version=parent_knowledge.version + 1,
        mechanism_family=parent_knowledge.mechanism_family,
        compatibility_profile=parent_knowledge.compatibility_profile,
        antecedent=dict(parent_knowledge.antecedent),
        intervention=dict(parent_knowledge.intervention),
        mediated_effects=parent_knowledge.mediated_effects,
        expected_outcome=dict(parent_knowledge.expected_outcome),
        positive_applicability=tuple(positive),
        negative_applicability=parent_knowledge.negative_applicability,
        preserved_obligations=parent_knowledge.preserved_obligations,
        known_failure_modes=parent_knowledge.known_failure_modes,
        causal_path_ids=parent_knowledge.causal_path_ids,
        evidence_level=parent_knowledge.evidence_level,
        support_lineages=tuple(sorted({
            *parent_knowledge.support_lineages, *lineages.values()})),
        status="shadow",
    )
    dimensions = copy.deepcopy(parent_envelope.dimensions)
    for shift_name, values in expanded.items():
        dimension = shift_name.removesuffix("_shift")
        raw = dimensions.get(dimension)
        if not isinstance(raw, dict) or not isinstance(raw.get("values"), list):
            raise StateShiftSupportExpansionError(
                "state-shift expansion parent envelope dimensions are malformed")
        _append_unique(raw["values"], values)
    child_envelope = SupportEnvelope(
        knowledge_object_id=child.object_id,
        dimensions=dimensions,
        evidence_refs=tuple(sorted({
            *parent_envelope.evidence_refs,
            proposal_digest,
            cohort.receipt_digest,
            *target_digests.values(),
            *(item.replay_digest for item in state_shifts.values()),
            *before_digests.values(),
        })),
        source_transition_ids=parent_envelope.source_transition_ids,
    )
    for case_id in sorted(case_ids):
        after = evaluate_state_shift(
            current_contexts[case_id], resolved_state, child, child_envelope,
            evidence_refs=(
                target_digests[case_id], state_shifts[case_id].replay_digest))
        if after.transferable is not True or after.reason != "NO_SHIFT":
            raise StateShiftSupportExpansionError(
                "state-shift expansion does not close the targeted support shift")
        after_receipts[case_id] = after.replay_digest

    receipt = StateShiftSupportExpansionReceipt(
        campaign_id=campaign_id,
        proposal_digest=proposal_digest,
        cohort_receipt_digest=cohort.receipt_digest,
        parent_knowledge_object_id=parent_knowledge.object_id,
        parent_knowledge_digest=parent_knowledge.content_digest,
        child_knowledge_object_id=child.object_id,
        child_knowledge_digest=child.content_digest,
        parent_support_envelope_digest=parent_envelope.envelope_digest,
        child_support_envelope_digest=child_envelope.envelope_digest,
        case_lineages=lineages,
        target_execution_digests=target_digests,
        state_context_digests=context_digests,
        admitted_state_shift_digests=admitted_shift_digests,
        before_state_shift_digests=before_digests,
        after_state_shift_digests=after_receipts,
        expanded_dimensions=tuple(expanded),
    )
    return child, child_envelope, receipt


__all__ = [
    "STATE_SHIFT_SUPPORT_EXPANSION_VERSION",
    "StateShiftSupportExpansionError", "StateShiftSupportExpansionReceipt",
    "derive_state_shift_support_expansion",
]
