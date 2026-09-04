"""Fail-closed replay for the Revision3 ORFS interference shadow stage.

This replay is deliberately read-only.  It authenticates the post-revision
execution manifest before inspecting the paired ORFS receipts, rebuilds the
typed interference proposal and localized plan, and verifies that the P13
shadow/delta and anti-forgetting receipts stay outside canonical memory and
production runtime.  It never invokes ORFS and never opens the campaign DB.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

from tehm.capability.delta import MemoryDeltaReceipt, memory_delta_from_shadow_update
from tehm.evaluation.candidate_executor import P12_ARMS
from tehm.evaluation.orfs_candidate_oracle import (
    _source_binding, _source_content_binding, _source_inputs,
    _verify_external_source_inputs,
)
from tehm.evaluation.orfs_cohort import OrfsPairedCohortReceipt
from tehm.evolution.admission import EvolutionAdmissionReceipt, admit_evolution_reason
from tehm.evolution.anti_forgetting import AntiForgettingWitness
from tehm.evolution.apply_update import AppliedShadowUpdateReceipt
from tehm.evolution.interference_revision import (
    MemoryInterferenceEvolutionProposal,
    interference_proposal_to_localized_plan,
    propose_memory_interference_specialization,
)
from tehm.evolution.local_revision import LocalizedUpdatePlan
from tehm.evolution.p12_shadow_trigger import (
    P12ShadowUpdateTriggerReceipt, P13EvolutionReasonReceipt,
)
from tehm.evolution.reason_derivation import (
    EvolutionReasonDerivationReceipt, derive_memory_interference_reason,
    p13_reason_receipt_from_derivations,
)
from tehm.ids import stable_dumps
from tehm.knowledge import MechanismKnowledge
from tehm.retrieval.structured_candidate import StructuredRepairCandidate
from contracts import MemoryRoutingDecision


CAMPAIGN_VERSION = "tehm-r3-orfs-interference-shadow-v0.1"
_TERMINAL = frozenset({"EXECUTION_INTERRUPTED", "EXECUTION_FAILED"})


class OrfsInterferenceShadowReplayError(ValueError):
    """A shadow artifact is malformed, stale, or crosses its boundary."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _load(path: Path, name: str) -> dict:
    if not path.is_file():
        raise OrfsInterferenceShadowReplayError(f"{name} is missing: {path}")
    try:
        payload = json.loads(path.read_text())
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        raise OrfsInterferenceShadowReplayError(f"{name} is not valid JSON: {path}") from exc
    if not isinstance(payload, Mapping):
        raise OrfsInterferenceShadowReplayError(f"{name} must be an object: {path}")
    return dict(payload)


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise OrfsInterferenceShadowReplayError(f"{name} is required")
    return value.strip()


def _boundary(payload: Mapping, *, name: str) -> None:
    if payload.get("evaluation_only") is not True:
        raise OrfsInterferenceShadowReplayError(f"{name} is not evaluation-only")
    if payload.get("canonical_memory_mutation") != "none":
        raise OrfsInterferenceShadowReplayError(f"{name} crosses canonical-memory boundary")
    if payload.get("memory_docs_submitted") is not False:
        raise OrfsInterferenceShadowReplayError(f"{name} crosses memory/docs boundary")
    if payload.get("production_runtime_imported") is not False:
        raise OrfsInterferenceShadowReplayError(f"{name} crosses production-runtime boundary")


def _load_manifest(artifacts: Path):
    receipts = artifacts / "receipts"
    manifest = _load(receipts / "campaign_manifest.json", "campaign manifest")
    if manifest.get("version") != CAMPAIGN_VERSION or manifest.get("lane") != "EVOLUTION_CHALLENGE":
        raise OrfsInterferenceShadowReplayError("shadow manifest version or lane is invalid")
    if manifest.get("reason") != "MEMORY_INTERFERENCE" or manifest.get("backend") != "external_orfs":
        raise OrfsInterferenceShadowReplayError("shadow manifest reason/backend is invalid")
    _boundary(manifest, name="campaign manifest")
    if manifest.get("production_integration") != "not_attempted":
        raise OrfsInterferenceShadowReplayError("shadow manifest production integration is invalid")
    digest = _digest(manifest)
    raw_cases = manifest.get("post_cases")
    raw_routes = manifest.get("post_routes")
    raw_candidates = manifest.get("post_candidate_payloads")
    if not isinstance(raw_cases, list) or not isinstance(raw_routes, Mapping) or not isinstance(raw_candidates, Mapping):
        raise OrfsInterferenceShadowReplayError("shadow manifest post inputs are incomplete")
    cases = {}
    routes = {}
    candidates = {}
    for raw in raw_cases:
        if not isinstance(raw, Mapping):
            raise OrfsInterferenceShadowReplayError("post case is not an object")
        case_id = _text(raw.get("case_id"), "post case_id")
        if case_id in cases:
            raise OrfsInterferenceShadowReplayError("post case IDs are duplicated")
        cases[case_id] = dict(raw)
        try:
            route = MemoryRoutingDecision.from_dict(raw_routes[case_id])
        except (TypeError, ValueError, KeyError) as exc:
            raise OrfsInterferenceShadowReplayError(f"post route is invalid for {case_id}") from exc
        try:
            arm_payloads = raw_candidates[case_id]
            if set(arm_payloads) != set(P12_ARMS):
                raise ValueError("post candidate arms do not cover P12")
            arm_candidates = {
                arm: (StructuredRepairCandidate.from_dict(value)
                      if value is not None else None)
                for arm, value in arm_payloads.items()
            }
        except (TypeError, ValueError, KeyError) as exc:
            raise OrfsInterferenceShadowReplayError(f"post candidates are invalid for {case_id}") from exc
        if (route.decision != "INAPPLICABLE" or route.memory_budget != 0 or
                route.no_memory_budget != 1 or route.abstain_reasons != ("negative_applicability",) or
                raw.get("routing_receipt_id") != route.routing_receipt_id or
                raw.get("routing_decision") != route.decision or
                arm_candidates["NO_MEMORY"] is not None or
                arm_candidates["APPLICABILITY_GATED"] is not None or
                arm_candidates["CAUSAL_NO_SKILL"] is not None or
                arm_candidates["ALWAYS_MEMORY"] is None):
            raise OrfsInterferenceShadowReplayError(f"post route/candidate policy drifted for {case_id}")
        routes[case_id] = route
        candidates[case_id] = arm_candidates
    if set(cases) != set(raw_routes) or set(cases) != set(raw_candidates):
        raise OrfsInterferenceShadowReplayError("shadow manifest case coverage is invalid")
    return manifest, digest, cases, routes, candidates


def _verify_cohort(artifacts: Path, manifest: Mapping, manifest_digest: str,
                   cases, routes, candidates) -> OrfsPairedCohortReceipt:
    payload = _load(artifacts / "receipts" / "post_revision_cohort.json", "post-revision cohort")
    try:
        cohort = OrfsPairedCohortReceipt.from_dict(payload)
    except (TypeError, ValueError, KeyError) as exc:
        raise OrfsInterferenceShadowReplayError("post-revision cohort is invalid") from exc
    if payload.get("receipt_digest") not in {cohort.receipt_digest, cohort.legacy_receipt_digest}:
        raise OrfsInterferenceShadowReplayError("post-revision cohort digest mismatch")
    if (cohort.campaign_id != manifest["campaign_id"] or
            cohort.campaign_manifest_digest != manifest_digest or
            set(cohort.case_receipts) != set(cases)):
        raise OrfsInterferenceShadowReplayError("post-revision cohort campaign binding is invalid")
    for case_id, pair in cohort.case_receipts.items():
        case = cases[case_id]
        route = routes[case_id]
        try:
            source_inputs = _source_inputs(case.get("source_inputs"))
            project = Path(_text(case.get("project_dir"), "project_dir")).expanduser().resolve()
            source_digest = _source_binding(project, source_inputs)
            content_digest = _source_content_binding(project, source_inputs)
            _verify_external_source_inputs(source_inputs)
        except Exception as exc:
            raise OrfsInterferenceShadowReplayError(f"post source replay failed for {case_id}: {exc}") from exc
        if (pair.case_digest != _digest(case) or pair.lineage_id != case.get("lineage_id") or
                pair.routing_receipt_id != route.routing_receipt_id or pair.routing_decision != route.decision or
                cohort.source_digests.get(case_id) != source_digest or
                cohort.source_content_digests.get(case_id) != content_digest):
            raise OrfsInterferenceShadowReplayError(f"post cohort binding is invalid for {case_id}")
        candidate = candidates[case_id]["ALWAYS_MEMORY"]
        for arm in P12_ARMS:
            receipt = pair.arm_receipts[arm]
            if receipt.case_id != case_id or receipt.toolchain_digest != cohort.toolchain_digest or receipt.oracle_digest != cohort.oracle_digest:
                raise OrfsInterferenceShadowReplayError(f"post arm binding is invalid for {case_id}/{arm}")
            if arm == "ALWAYS_MEMORY":
                if (receipt.source != "structured_memory" or receipt.candidate_id != candidate.candidate_id or
                        receipt.candidate_digest != candidate.candidate_digest or
                        receipt.action_digest != _digest(candidate.concrete_action)):
                    raise OrfsInterferenceShadowReplayError(f"post memory arm is not candidate-bound for {case_id}")
            elif receipt.source != "no_memory" or receipt.candidate_id != "no_memory:" + case_id:
                raise OrfsInterferenceShadowReplayError(f"post fallback arm is malformed for {case_id}/{arm}")
    return cohort


def _replay_receipts(artifacts: Path, manifest: Mapping, manifest_digest: str,
                     cases, routes, candidates, cohort: OrfsPairedCohortReceipt) -> dict:
    receipts = artifacts / "receipts"
    pre_payload = _load(receipts / "pre_challenge_cohort.json", "pre-challenge cohort")
    try:
        pre = OrfsPairedCohortReceipt.from_dict(pre_payload)
    except (TypeError, ValueError, KeyError) as exc:
        raise OrfsInterferenceShadowReplayError("pre-challenge cohort is invalid") from exc
    if pre_payload.get("receipt_digest") not in {pre.receipt_digest, pre.legacy_receipt_digest} or \
            pre.receipt_digest != manifest.get("pre_challenge_cohort_digest"):
        raise OrfsInterferenceShadowReplayError("pre-challenge cohort digest is not manifest-bound")
    training = _load(receipts / "training.json", "shadow training receipt")
    try:
        parent = MechanismKnowledge.from_dict(training["knowledge"])
    except (TypeError, ValueError, KeyError) as exc:
        raise OrfsInterferenceShadowReplayError("shadow parent knowledge is invalid") from exc
    if (training.get("knowledge_object_id") != parent.object_id or
            training.get("transition_ids") != manifest.get("training_transition_ids") or
            training.get("lineages") != manifest.get("training_lineages") or
            manifest.get("parent_knowledge") != training.get("knowledge")):
        raise OrfsInterferenceShadowReplayError("shadow training manifest binding is invalid")

    raw_derivations = _load(receipts / "reason_derivation.json", "reason derivation").get("derivations")
    if not isinstance(raw_derivations, Mapping) or set(raw_derivations) != set(pre.case_receipts):
        raise OrfsInterferenceShadowReplayError("shadow derivation coverage is invalid")
    derivations = {}
    for case_id, values in raw_derivations.items():
        if not isinstance(values, list) or len(values) != 1:
            raise OrfsInterferenceShadowReplayError(f"shadow derivation count is invalid for {case_id}")
        try:
            item = EvolutionReasonDerivationReceipt.from_dict(values[0])
            expected = derive_memory_interference_reason(
                pre.case_receipts[case_id], campaign_id=pre.campaign_id,
                memory_arm="ALWAYS_MEMORY")
        except (TypeError, ValueError, KeyError) as exc:
            raise OrfsInterferenceShadowReplayError(f"shadow derivation is invalid for {case_id}") from exc
        if expected is None or item.receipt_digest != expected.receipt_digest:
            raise OrfsInterferenceShadowReplayError(f"shadow derivation drifted for {case_id}")
        derivations[case_id] = (item,)

    reason_payload = _load(receipts / "p13_reason_receipt.json", "P13 reason receipt")
    try:
        reason = P13EvolutionReasonReceipt.from_dict(reason_payload)
    except (TypeError, ValueError, KeyError) as exc:
        raise OrfsInterferenceShadowReplayError("P13 reason receipt is invalid") from exc
    expected_reason = p13_reason_receipt_from_derivations(
        derivations, campaign_id=pre.campaign_id, cohort_receipt_digest=pre.receipt_digest)
    if (reason_payload.get("receipt_digest") != reason.receipt_digest or
            reason.receipt_digest != expected_reason.receipt_digest or
            reason_payload.get("receipt_digest") != manifest.get("pre_reason_receipt_digest")):
        raise OrfsInterferenceShadowReplayError("shadow reason receipt drifted")

    raw_triggers = _load(receipts / "p12_triggers.json", "P12 triggers").get("triggers")
    if not isinstance(raw_triggers, list) or len(raw_triggers) != len(pre.case_receipts):
        raise OrfsInterferenceShadowReplayError("shadow trigger coverage is invalid")
    triggers = {}
    for raw in raw_triggers:
        try:
            item = P12ShadowUpdateTriggerReceipt.from_dict(raw)
        except (TypeError, ValueError, KeyError) as exc:
            raise OrfsInterferenceShadowReplayError("P12 trigger receipt is invalid") from exc
        if raw.get("receipt_digest") != item.receipt_digest or item.case_id in triggers:
            raise OrfsInterferenceShadowReplayError("P12 trigger digest/case coverage is invalid")
        if item.cohort_receipt_digest != pre.receipt_digest or not item.triggered:
            raise OrfsInterferenceShadowReplayError(f"P12 trigger binding is invalid for {item.case_id}")
        triggers[item.case_id] = item
    if set(triggers) != set(pre.case_receipts) or sorted(item.receipt_digest for item in triggers.values()) != sorted(manifest.get("pre_trigger_receipt_digests", ())):
        raise OrfsInterferenceShadowReplayError("P12 trigger manifest binding is invalid")

    raw_admissions = _load(receipts / "admissions.json", "admissions").get("admissions")
    if not isinstance(raw_admissions, Mapping) or set(raw_admissions) != set(derivations):
        raise OrfsInterferenceShadowReplayError("shadow admission coverage is invalid")
    admissions = {}
    for case_id, raw in raw_admissions.items():
        try:
            item = EvolutionAdmissionReceipt.from_dict(raw)
            expected = admit_evolution_reason(
                derivations[case_id][0], campaign_id=pre.campaign_id, learner_eligible=True,
                paired=pre.case_receipts[case_id], memory_arm="ALWAYS_MEMORY")
        except (TypeError, ValueError, KeyError) as exc:
            raise OrfsInterferenceShadowReplayError(f"admission is invalid for {case_id}") from exc
        if raw.get("receipt_digest") != item.receipt_digest or item.receipt_digest != expected.receipt_digest or not item.admitted:
            raise OrfsInterferenceShadowReplayError(f"admission drifted for {case_id}")
        admissions[case_id] = item

    proposal_payload = _load(receipts / "proposal.json", "interference proposal")
    try:
        proposal = MemoryInterferenceEvolutionProposal.from_dict(proposal_payload)
        expected_proposal = propose_memory_interference_specialization(
            [(derivations[cid][0], pre.case_receipts[cid]) for cid in sorted(pre.case_receipts)],
            knowledge_object_id=parent.object_id,
            transition_ids=tuple(training["transition_ids"]),
            negative_applicability=proposal.negative_applicability,
            trigger_receipt_ids=proposal.trigger_receipt_ids,
            evidence_refs=proposal.evidence_refs)
    except (TypeError, ValueError, KeyError) as exc:
        raise OrfsInterferenceShadowReplayError("interference proposal is invalid") from exc
    if (proposal_payload.get("proposal_digest") != proposal.proposal_digest or
            proposal_payload.get("proposal_id") != proposal.proposal_id or
            proposal.proposal_digest != expected_proposal.proposal_digest or
            proposal.campaign_id != pre.campaign_id or
            proposal.knowledge_object_id != parent.object_id):
        raise OrfsInterferenceShadowReplayError("interference proposal drifted")

    witness_payload = _load(receipts / "anti_forgetting.json", "anti-forgetting witness")
    try:
        witness = AntiForgettingWitness.from_dict(witness_payload["witness"])
    except (TypeError, ValueError, KeyError) as exc:
        raise OrfsInterferenceShadowReplayError("anti-forgetting witness is invalid") from exc
    if (witness_payload.get("campaign_id") != pre.campaign_id or
            witness_payload.get("eligible") is not True or
            witness.receipt_digest != witness_payload.get("witness", {}).get("receipt_digest")):
        raise OrfsInterferenceShadowReplayError("anti-forgetting witness binding is invalid")
    expected_plan = interference_proposal_to_localized_plan(expected_proposal)
    refs = set(expected_plan.evidence_refs)
    refs.update({reason.receipt_digest, witness.receipt_digest})
    expected_plan = LocalizedUpdatePlan(**{**expected_plan.__dict__, "evidence_refs": tuple(sorted(refs))})
    plan_payload = _load(receipts / "localized_update_plan.json", "localized update plan")
    try:
        plan = LocalizedUpdatePlan.from_dict(plan_payload)
    except (TypeError, ValueError, KeyError) as exc:
        raise OrfsInterferenceShadowReplayError("localized update plan is invalid") from exc
    if plan_payload.get("plan_digest") != plan.plan_digest or plan.plan_digest != expected_plan.plan_digest:
        raise OrfsInterferenceShadowReplayError("localized update plan drifted")

    shadow_payload = _load(receipts / "shadow_update.json", "shadow update")
    delta_payload = _load(receipts / "memory_delta.json", "memory delta")
    try:
        shadow = AppliedShadowUpdateReceipt.from_dict(shadow_payload)
        delta = MemoryDeltaReceipt.from_dict(delta_payload)
        expected_delta = memory_delta_from_shadow_update(shadow)
    except (TypeError, ValueError, KeyError) as exc:
        raise OrfsInterferenceShadowReplayError("shadow/delta receipt is invalid") from exc
    if (shadow_payload.get("receipt_digest") != shadow.receipt_digest or
            shadow.campaign_id != pre.campaign_id or shadow.plan_digest != plan.plan_digest or
            shadow.operation != proposal.operation or not delta.eligible or
            delta_payload.get("receipt_digest") != delta.receipt_digest or
            delta.receipt_digest != expected_delta.receipt_digest):
        raise OrfsInterferenceShadowReplayError("shadow/delta binding is invalid")
    summary = _load(artifacts / "summary.json", "shadow summary")
    _boundary(summary, name="shadow summary")
    if (summary.get("campaign_id") != pre.campaign_id or
            summary.get("shadow_campaign_id") != manifest["campaign_id"] or
            summary.get("campaign_manifest_digest") != manifest_digest or
            summary.get("post_cohort_receipt_digest") != cohort.receipt_digest or
            summary.get("challenge_case_count") != len(cohort.case_receipts) or
            summary.get("proposal_operation") != proposal.operation or
            summary.get("shadow_operation") != shadow.operation or
            summary.get("memory_delta_eligible") is not True or
            summary.get("anti_forgetting_eligible") is not True or
            summary.get("canonical_counts_unchanged") is not True or
            summary.get("source_db_unchanged") is not True or
            summary.get("production_authority_changed") is not False):
        raise OrfsInterferenceShadowReplayError("shadow summary disagrees with receipts")
    return {
        "derivation_count": sum(len(items) for items in derivations.values()),
        "triggered_count": len(triggers), "admitted_count": len(admissions),
        "proposal_operation": proposal.operation, "shadow_operation": shadow.operation,
        "memory_delta_eligible": delta.eligible,
    }


def replay(artifacts: Path | str) -> dict:
    """Replay one completed or terminal ORFS interference shadow artifact."""
    artifacts = Path(artifacts).expanduser().resolve()
    if not artifacts.is_dir():
        raise OrfsInterferenceShadowReplayError(f"artifacts is not a directory: {artifacts}")
    manifest, manifest_digest, cases, routes, candidates = _load_manifest(artifacts)
    cohort_path = artifacts / "receipts" / "post_revision_cohort.json"
    failure_path = artifacts / "failure.json"
    if not cohort_path.is_file():
        if not failure_path.is_file():
            raise OrfsInterferenceShadowReplayError("shadow has neither cohort nor terminal failure")
        failure = _load(failure_path, "shadow failure")
        if (failure.get("version") != CAMPAIGN_VERSION or
                failure.get("status") not in _TERMINAL or
                failure.get("campaign_id") != manifest["campaign_id"] or
                failure.get("campaign_manifest_digest") != manifest_digest or
                failure.get("cohort_available") is not False):
            raise OrfsInterferenceShadowReplayError("terminal shadow failure boundary is invalid")
        _boundary(failure, name="shadow failure")
        return {
            "mode": "replay", "status": "REPLAY_PASS",
            "terminal_status": failure["status"], "campaign_id": manifest["campaign_id"],
            "manifest_digest": manifest_digest, "cohort_available": False,
            "evaluation_only": True, "canonical_memory_mutation": "none",
            "production_runtime_imported": False, "memory_docs_submitted": False,
        }
    cohort = _verify_cohort(artifacts, manifest, manifest_digest, cases, routes, candidates)
    downstream = _replay_receipts(
        artifacts, manifest, manifest_digest, cases, routes, candidates, cohort)
    return {
        "mode": "replay", "status": "REPLAY_PASS", "terminal_status": "COMPLETE",
        "campaign_id": manifest["campaign_id"], "source_campaign_id": manifest["source_campaign_id"],
        "manifest_digest": manifest_digest, "cohort_receipt_digest": cohort.receipt_digest,
        "case_count": len(cohort.case_receipts), "lineage_count": cohort.lineage_count,
        **downstream, "evaluation_only": True, "canonical_memory_mutation": "none",
        "production_runtime_imported": False, "memory_docs_submitted": False,
    }


__all__ = ["OrfsInterferenceShadowReplayError", "replay"]
