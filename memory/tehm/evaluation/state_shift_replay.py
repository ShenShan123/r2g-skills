"""Fail-closed, read-only replay for the Revision3 StateShift challenge.

The producer executes real RTL once and writes a content-addressed chain of
manifests and typed receipts.  This module replays that chain without opening
TEHM or invoking an oracle.  It verifies the frozen inputs, rebuilds the typed
STATE_SHIFT reason, P12 triggers, admissions, proposal, and localized plan,
and checks the P13/P14 boundary receipts remain evaluation-only.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

from contracts import MemoryRoutingDecision
from tehm.evaluation.candidate_executor import P12_ARMS
from tehm.evaluation.rtl_cohort import RtlPairedCohortReceipt
from tehm.evolution.admission import EvolutionAdmissionReceipt, admit_evolution_reason
from tehm.evolution.apply_update import AppliedShadowUpdateReceipt
from tehm.evolution.p12_shadow_trigger import (
    P12ShadowUpdateTriggerReceipt, P13EvolutionReasonReceipt,
    build_p12_shadow_update_triggers_from_reason_receipt,
)
from tehm.evolution.reason_derivation import (
    EvolutionReasonDerivationReceipt, derive_state_shift_reason,
    p13_reason_receipt_from_derivations,
)
from tehm.evolution.state_shift_revision import (
    StateShiftEvolutionProposal,
    propose_repeated_state_shift_from_paired_receipts,
    state_shift_proposal_to_localized_plan,
)
from tehm.evolution.local_revision import LocalizedUpdatePlan
from tehm.capability.delta import MemoryDeltaReceipt, memory_delta_from_shadow_update
from tehm.ids import stable_dumps
from tehm.knowledge import MechanismKnowledge
from tehm.state.shift_receipts import StateShiftReceipt
from tehm.state.support_envelope import SupportEnvelope
from tehm.retrieval.structured_candidate import StructuredRepairCandidate


CAMPAIGN_VERSION = "tehm-r3-state-shift-challenge-v0.1"


class StateShiftReplayError(ValueError):
    """A StateShift artifact is malformed, stale, or crosses a boundary."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _load(path: Path, name: str) -> dict:
    if not path.is_file():
        raise StateShiftReplayError(f"{name} is missing: {path}")
    try:
        value = json.loads(path.read_text())
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        raise StateShiftReplayError(f"{name} is not valid JSON: {path}") from exc
    if not isinstance(value, Mapping):
        raise StateShiftReplayError(f"{name} must be a JSON object: {path}")
    return dict(value)


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise StateShiftReplayError(f"{name} is required")
    return value.strip()


def _boundary(payload: Mapping, *, name: str) -> None:
    if payload.get("evaluation_only") is not True:
        raise StateShiftReplayError(f"{name} is not evaluation-only")
    if payload.get("canonical_memory_mutation") != "none":
        raise StateShiftReplayError(f"{name} crosses canonical-memory boundary")
    if payload.get("memory_docs_submitted") is not False:
        raise StateShiftReplayError(f"{name} crosses memory/docs boundary")
    if payload.get("production_runtime_imported") is not False:
        raise StateShiftReplayError(f"{name} crosses production-runtime boundary")


def _file_digest(path: Path) -> str:
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise StateShiftReplayError(f"source file cannot be read: {path}") from exc


def _load_inputs(artifacts: Path):
    receipts = artifacts / "receipts"
    manifest = _load(receipts / "campaign_manifest.json", "campaign manifest")
    if manifest.get("version") != CAMPAIGN_VERSION or manifest.get("lane") != "EVOLUTION_CHALLENGE":
        raise StateShiftReplayError("campaign manifest version or lane is invalid")
    _boundary(manifest, name="campaign manifest")
    campaign_id = _text(manifest.get("campaign_id"), "campaign_id")
    manifest_digest = _digest(manifest)
    cases_payload = _load(receipts / "cases.json", "cases manifest")
    if cases_payload.get("cases") != manifest.get("cases") or cases_payload.get("routing") != manifest.get("routing"):
        raise StateShiftReplayError("campaign and cases manifests disagree")
    raw_cases = manifest.get("cases")
    raw_routes = manifest.get("routing")
    raw_shifts = manifest.get("state_shifts")
    raw_candidates = manifest.get("candidate_payloads")
    if not isinstance(raw_cases, list) or len(raw_cases) < 2:
        raise StateShiftReplayError("StateShift challenge requires at least two cases")
    if not isinstance(raw_routes, Mapping) or not isinstance(raw_shifts, Mapping) or not isinstance(raw_candidates, Mapping):
        raise StateShiftReplayError("StateShift manifest inputs are incomplete")
    cases = { _text(item.get("case_id"), "case_id"): item for item in raw_cases if isinstance(item, Mapping) }
    if len(cases) != len(raw_cases) or len(cases) != len(raw_routes) or set(cases) != set(raw_shifts) or set(cases) != set(raw_candidates):
        raise StateShiftReplayError("StateShift manifest case coverage is invalid")
    routes: dict[str, MemoryRoutingDecision] = {}
    shifts: dict[str, StateShiftReceipt] = {}
    candidates: dict[str, StructuredRepairCandidate] = {}
    for case_id, case in cases.items():
        source = Path(_text(case.get("rtl_source"), f"{case_id}.rtl_source")).expanduser().resolve()
        if case.get("source_digest") != _file_digest(source):
            raise StateShiftReplayError(f"source freeze digest drifted for {case_id}")
        try:
            route = MemoryRoutingDecision.from_dict(raw_routes[case_id])
            shift = StateShiftReceipt.from_dict(raw_shifts[case_id])
            candidate = StructuredRepairCandidate.from_dict(raw_candidates[case_id])
        except (TypeError, ValueError, KeyError) as exc:
            raise StateShiftReplayError(f"typed input receipt is invalid for {case_id}") from exc
        if (case.get("routing_receipt_id") != route.routing_receipt_id or
                case.get("routing_decision") != route.decision or
                route.decision != "NO_SKILL" or route.no_skill_reason != "STATE_SHIFT" or
                route.state_shift_receipt_id != shift.receipt_id or
                shift.knowledge_object_id != manifest["training_knowledge"].get("knowledge_id") + "@" + str(manifest["training_knowledge"].get("version"))):
            raise StateShiftReplayError(f"route/state-shift binding is invalid for {case_id}")
        if shift.reason != "STATE_SHIFT" or shift.transferable:
            raise StateShiftReplayError(f"StateShift receipt is not non-transferable for {case_id}")
        routes[case_id], shifts[case_id], candidates[case_id] = route, shift, candidate
    training = _load(receipts / "training.json", "training receipt")
    try:
        knowledge = MechanismKnowledge.from_dict(training["knowledge"])
        envelope = SupportEnvelope.from_dict(training["support_envelope"])
    except (TypeError, ValueError, KeyError) as exc:
        raise StateShiftReplayError("training knowledge or support envelope is invalid") from exc
    if (training.get("knowledge_object_id") != knowledge.object_id or
            manifest.get("training_knowledge") != training.get("knowledge") or
            manifest.get("support_envelope") != training.get("support_envelope") or
            tuple(manifest.get("training_transition_ids", ())) != tuple(training.get("transition_ids", ()) ) or
            envelope.knowledge_object_id != knowledge.object_id or
            envelope.envelope_digest != manifest["support_envelope"].get("envelope_digest")):
        raise StateShiftReplayError("training manifest binding is invalid")
    return manifest, manifest_digest, cases, routes, shifts, candidates, knowledge, envelope


def _verify_cohort(artifacts: Path, manifest: Mapping, manifest_digest: str,
                   cases: Mapping[str, Mapping], routes: Mapping[str, MemoryRoutingDecision],
                   candidates: Mapping[str, StructuredRepairCandidate]) -> RtlPairedCohortReceipt:
    payload = _load(artifacts / "receipts" / "cohort.json", "RTL cohort")
    try:
        cohort = RtlPairedCohortReceipt.from_dict(payload)
    except (TypeError, ValueError, KeyError) as exc:
        raise StateShiftReplayError("RTL cohort receipt is invalid") from exc
    if payload.get("receipt_digest") not in {cohort.receipt_digest, cohort.legacy_receipt_digest}:
        raise StateShiftReplayError("RTL cohort receipt digest mismatch")
    if (cohort.campaign_id != manifest["campaign_id"] or
            cohort.campaign_manifest_digest != manifest_digest or
            set(cohort.case_receipts) != set(cases)):
        raise StateShiftReplayError("RTL cohort campaign binding is invalid")
    for case_id, pair in cohort.case_receipts.items():
        case = cases[case_id]
        route = routes[case_id]
        if (pair.case_digest != _digest(case) or pair.lineage_id != case.get("lineage_id") or
                pair.routing_receipt_id != route.routing_receipt_id or
                pair.routing_decision != route.decision or
                pair.state_shift_receipt_id != route.state_shift_receipt_id or
                cohort.source_digests.get(case_id) != case.get("source_digest")):
            raise StateShiftReplayError(f"cohort case binding is invalid for {case_id}")
        candidate = candidates[case_id]
        for arm in P12_ARMS:
            receipt = pair.arm_receipts[arm]
            if receipt.case_id != case_id or receipt.toolchain_digest != cohort.toolchain_digest or receipt.oracle_digest != cohort.oracle_digest:
                raise StateShiftReplayError(f"cohort arm binding is invalid for {case_id}/{arm}")
            if arm in {"ALWAYS_MEMORY", "APPLICABILITY_GATED"}:
                if (receipt.source != "structured_memory" or receipt.candidate_id != candidate.candidate_id or
                        receipt.candidate_digest != candidate.candidate_digest or
                        receipt.action_digest != _digest(candidate.concrete_action)):
                    raise StateShiftReplayError(f"structured arm is not bound to candidate for {case_id}/{arm}")
            else:
                if receipt.source != "no_memory" or receipt.candidate_id != "no_memory:" + case_id:
                    raise StateShiftReplayError(f"fallback arm is malformed for {case_id}/{arm}")
    return cohort


def _replay_downstream(artifacts: Path, manifest: Mapping, cases, routes, shifts,
                       candidates, knowledge, envelope, cohort: RtlPairedCohortReceipt,
                       manifest_digest: str) -> dict:
    receipts = artifacts / "receipts"
    raw_derivations = _load(receipts / "reason_derivation.json", "reason derivation").get("derivations")
    if not isinstance(raw_derivations, Mapping) or set(raw_derivations) != set(cohort.case_receipts):
        raise StateShiftReplayError("reason derivation coverage is invalid")
    derivations: dict[str, tuple[EvolutionReasonDerivationReceipt, ...]] = {}
    for case_id, raw_items in raw_derivations.items():
        if not isinstance(raw_items, list) or len(raw_items) != 1:
            raise StateShiftReplayError(f"reason derivation count is invalid for {case_id}")
        try:
            item = EvolutionReasonDerivationReceipt.from_dict(raw_items[0])
            expected = derive_state_shift_reason(
                shifts[case_id], campaign_id=manifest["campaign_id"], case_id=case_id,
                routing=routes[case_id], lineage_id=cases[case_id]["lineage_id"])
        except (TypeError, ValueError, KeyError) as exc:
            raise StateShiftReplayError(f"reason derivation is invalid for {case_id}") from exc
        if expected is None or item.receipt_digest != expected.receipt_digest:
            raise StateShiftReplayError(f"typed STATE_SHIFT derivation drifted for {case_id}")
        derivations[case_id] = (item,)

    reason_payload = _load(receipts / "p13_reason_receipt.json", "P13 reason receipt")
    try:
        reason = P13EvolutionReasonReceipt.from_dict(reason_payload)
    except (TypeError, ValueError, KeyError) as exc:
        raise StateShiftReplayError("P13 reason receipt is invalid") from exc
    expected_reason = p13_reason_receipt_from_derivations(
        derivations, campaign_id=manifest["campaign_id"], cohort_receipt_digest=cohort.receipt_digest)
    if reason_payload.get("receipt_digest") != reason.receipt_digest or reason.receipt_digest != expected_reason.receipt_digest:
        raise StateShiftReplayError("P13 reason aggregation drifted")

    expected_triggers = build_p12_shadow_update_triggers_from_reason_receipt(
        cohort, memory_arm="ALWAYS_MEMORY", learner_eligible=True, reason_receipt=reason,
        min_lineages=2, routing_decisions=routes,
        case_learner_eligibility={case_id: True for case_id in cohort.case_receipts},
        derivation_receipts=derivations)
    raw_triggers = _load(receipts / "p12_triggers.json", "P12 triggers").get("triggers")
    if not isinstance(raw_triggers, list) or len(raw_triggers) != len(expected_triggers):
        raise StateShiftReplayError("P12 trigger coverage is invalid")
    stored_triggers = {}
    for raw in raw_triggers:
        try:
            item = P12ShadowUpdateTriggerReceipt.from_dict(raw)
        except (TypeError, ValueError, KeyError) as exc:
            raise StateShiftReplayError("P12 trigger receipt is invalid") from exc
        if raw.get("receipt_digest") != item.receipt_digest or item.case_id in stored_triggers:
            raise StateShiftReplayError("P12 trigger digest or case coverage is invalid")
        stored_triggers[item.case_id] = item
    if any(stored_triggers.get(exp.case_id, object()).receipt_digest != exp.receipt_digest for exp in expected_triggers):
        raise StateShiftReplayError("P12 trigger derivation drifted")

    raw_admissions = _load(receipts / "admissions.json", "admissions").get("admissions")
    if not isinstance(raw_admissions, Mapping) or set(raw_admissions) != set(derivations):
        raise StateShiftReplayError("admission coverage is invalid")
    admitted = 0
    for case_id, raw in raw_admissions.items():
        try:
            item = EvolutionAdmissionReceipt.from_dict(raw)
            expected = admit_evolution_reason(
                derivations[case_id][0], campaign_id=manifest["campaign_id"], learner_eligible=True,
                paired=cohort.case_receipts[case_id], state_shift=shifts[case_id], routing=routes[case_id])
        except (TypeError, ValueError, KeyError) as exc:
            raise StateShiftReplayError(f"admission receipt is invalid for {case_id}") from exc
        if raw.get("receipt_digest") != item.receipt_digest or item.receipt_digest != expected.receipt_digest:
            raise StateShiftReplayError(f"admission derivation drifted for {case_id}")
        admitted += int(item.admitted)

    proposal_payload = _load(receipts / "proposal.json", "StateShift proposal")
    try:
        proposal = StateShiftEvolutionProposal.from_dict(proposal_payload)
        expected_proposal = propose_repeated_state_shift_from_paired_receipts(
            [(shifts[cid], cohort.case_receipts[cid]) for cid in sorted(cohort.case_receipts)],
            knowledge_object_id=knowledge.object_id,
            transition_ids=tuple(manifest["training_transition_ids"]),
            evidence_refs=proposal.evidence_refs, learner_eligible=proposal.learner_eligible,
            min_repeats=2, historical_memory_arm="ALWAYS_MEMORY")
    except (TypeError, ValueError, KeyError) as exc:
        raise StateShiftReplayError("StateShift proposal is invalid") from exc
    if (proposal_payload.get("proposal_digest") != proposal.proposal_digest or
            proposal_payload.get("proposal_id") != proposal.proposal_id or
            proposal.proposal_digest != expected_proposal.proposal_digest):
        raise StateShiftReplayError("StateShift proposal derivation drifted")

    plan_payload = _load(receipts / "localized_update_plan.json", "localized update plan")
    try:
        plan = LocalizedUpdatePlan.from_dict(plan_payload)
        expected_plan = state_shift_proposal_to_localized_plan(
            expected_proposal, campaign_id=manifest["campaign_id"],
            p12_trigger_digest=stored_triggers[sorted(stored_triggers)[0]].receipt_digest)
        refs = set(expected_plan.evidence_refs)
        refs.update({stored_triggers[sorted(stored_triggers)[1]].receipt_digest, reason.receipt_digest})
        expected_plan = expected_plan.__class__(**{**expected_plan.__dict__, "evidence_refs": tuple(sorted(refs))})
        witness_payload = _load(receipts / "anti_forgetting.json", "anti-forgetting witness")
        witness = witness_payload["witness"]
        refs.add(witness["receipt_digest"])
        expected_plan = expected_plan.__class__(**{**expected_plan.__dict__, "evidence_refs": tuple(sorted(refs))})
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        raise StateShiftReplayError("localized plan or witness is invalid") from exc
    if plan_payload.get("plan_digest") != plan.plan_digest or plan.plan_digest != expected_plan.plan_digest:
        raise StateShiftReplayError("localized plan derivation drifted")

    shadow_payload = _load(receipts / "shadow_update.json", "shadow update")
    try:
        shadow = AppliedShadowUpdateReceipt.from_dict(shadow_payload)
        delta_payload = _load(receipts / "memory_delta.json", "memory delta")
        delta = MemoryDeltaReceipt.from_dict(delta_payload)
        expected_delta = memory_delta_from_shadow_update(shadow)
    except (TypeError, ValueError, KeyError) as exc:
        raise StateShiftReplayError("P13 shadow or memory delta receipt is invalid") from exc
    if (shadow_payload.get("receipt_digest") != shadow.receipt_digest or
            shadow.plan_digest != plan.plan_digest or shadow.campaign_id != manifest["campaign_id"] or
            shadow.operation != proposal.operation or shadow_payload.get("production_runtime_imported") is not False or
            shadow_payload.get("canonical_memory_mutation") != "none" or
            delta_payload.get("receipt_digest") != delta.receipt_digest or
            delta.receipt_digest != expected_delta.receipt_digest or not delta.eligible):
        raise StateShiftReplayError("P13 shadow/delta binding is invalid")

    witness_payload = _load(receipts / "anti_forgetting.json", "anti-forgetting witness")
    if (witness_payload.get("canonical_memory_mutation") != "none" or
            witness_payload.get("memory_docs_submitted") is not False or
            witness_payload.get("production_runtime_imported") is not False or
            witness_payload.get("production_integration") != "not_attempted" or
            witness_payload.get("eligible") is not True):
        raise StateShiftReplayError("anti-forgetting witness crosses an authority boundary")
    for name in ("p14_strategy_attribution.json", "p14_capability_heldout_attribution.json"):
        payload = _load(receipts / name, name)
        if (payload.get("evaluation_only") is not True or
                payload.get("canonical_memory_mutation") != "none" or
                payload.get("memory_docs_submitted") is not False or
                payload.get("production_authority_changed") is not False):
            raise StateShiftReplayError(f"{name} crosses an authority boundary")
    summary = _load(artifacts / "summary.json", "challenge summary")
    if (summary.get("evaluation_only") is not True or
            summary.get("canonical_memory_mutation") != "none" or
            summary.get("memory_docs_submitted") is not False or
            summary.get("production_authority_changed") is not False):
        raise StateShiftReplayError("challenge summary crosses an authority boundary")
    if (summary.get("campaign_id") != manifest["campaign_id"] or
            (summary.get("campaign_manifest_digest") is not None and
             summary.get("campaign_manifest_digest") != manifest_digest) or
            (summary.get("cohort_receipt_digest") is not None and
             summary.get("cohort_receipt_digest") != cohort.receipt_digest) or
            summary.get("case_count") != len(cohort.case_receipts) or
            summary.get("lineage_count") != cohort.lineage_count or
            summary.get("state_shift_count") != len(shifts) or
            summary.get("triggered_count") != sum(item.triggered for item in expected_triggers) or
            summary.get("admitted_count") != admitted or
            summary.get("proposal_operation") != proposal.operation or
            summary.get("shadow_operation") != shadow.operation or
            summary.get("canonical_counts_unchanged") is not True or
            summary.get("memory_delta_eligible") is not True or
            summary.get("production_authority_changed") is not False):
        raise StateShiftReplayError("challenge summary disagrees with receipts")
    return {
        "derivation_count": sum(len(v) for v in derivations.values()),
        "triggered_count": sum(item.triggered for item in expected_triggers),
        "admitted_count": admitted, "proposal_operation": proposal.operation,
        "shadow_operation": shadow.operation, "memory_delta_eligible": delta.eligible,
    }


def replay(artifacts: Path | str) -> dict:
    """Replay one completed StateShift challenge artifact read-only."""
    artifacts = Path(artifacts).expanduser().resolve()
    if not artifacts.is_dir():
        raise StateShiftReplayError(f"artifacts is not a directory: {artifacts}")
    manifest, manifest_digest, cases, routes, shifts, candidates, knowledge, envelope = _load_inputs(artifacts)
    cohort = _verify_cohort(artifacts, manifest, manifest_digest, cases, routes, candidates)
    downstream = _replay_downstream(
        artifacts, manifest, cases, routes, shifts, candidates, knowledge, envelope,
        cohort, manifest_digest)
    return {
        "mode": "replay", "status": "REPLAY_PASS", "terminal_status": "COMPLETE",
        "campaign_id": manifest["campaign_id"], "manifest_digest": manifest_digest,
        "cohort_receipt_digest": cohort.receipt_digest,
        "case_count": len(cohort.case_receipts), "lineage_count": cohort.lineage_count,
        **downstream, "evaluation_only": True, "canonical_memory_mutation": "none",
        "production_runtime_imported": False, "memory_docs_submitted": False,
    }


__all__ = ["StateShiftReplayError", "replay"]
