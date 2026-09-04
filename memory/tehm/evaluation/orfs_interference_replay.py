"""Fail-closed, read-only replay for the Revision3 ORFS challenge lane.

The ORFS challenge producer performs an expensive real execution and writes a
chain of immutable receipts.  This module replays that chain without invoking
ORFS, opening a TEHM database, or importing anything into canonical memory.
It verifies the frozen manifest and external source bindings, then rebuilds
the typed reason, P13 envelope, P12 triggers, and reason-specific admissions
from the stored execution receipts.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

from contracts import MemoryRoutingDecision
from tehm.evaluation.candidate_executor import P12_ARMS
from tehm.evaluation.orfs_candidate_oracle import (
    _source_binding,
    _source_content_binding,
    _source_inputs,
    _verify_external_source_inputs,
)
from tehm.evaluation.orfs_cohort import OrfsPairedCohortReceipt
from tehm.evolution.admission import EvolutionAdmissionReceipt, admit_evolution_reason
from tehm.evolution.p12_shadow_trigger import (
    P12ShadowUpdateTriggerReceipt,
    P13EvolutionReasonReceipt,
    build_p12_shadow_update_triggers_from_reason_receipt,
)
from tehm.evolution.reason_derivation import (
    EvolutionReasonDerivationReceipt,
    derive_memory_interference_reason,
    p13_reason_receipt_from_derivations,
)
from tehm.ids import stable_dumps
from tehm.retrieval.structured_candidate import StructuredRepairCandidate


CAMPAIGN_VERSION = "tehm-r3-orfs-interference-challenge-v0.1"
_TERMINAL_WITHOUT_COHORT = frozenset({"EXECUTION_INTERRUPTED", "EXECUTION_FAILED"})


class OrfsInterferenceReplayError(ValueError):
    """A challenge artifact is malformed, stale, or violates its boundary."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _load_json(path: Path, name: str) -> object:
    if not path.is_file():
        raise OrfsInterferenceReplayError(f"{name} is missing: {path}")
    try:
        value = json.loads(path.read_text())
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        raise OrfsInterferenceReplayError(f"{name} is not valid JSON: {path}") from exc
    if not isinstance(value, Mapping):
        raise OrfsInterferenceReplayError(f"{name} must be a JSON object: {path}")
    return dict(value)


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise OrfsInterferenceReplayError(f"{name} is required")
    return value.strip()


def _digest_text(value: object, name: str) -> str:
    text = _text(value, name)
    if not text.startswith("sha256:") or len(text) != len("sha256:") + 64:
        raise OrfsInterferenceReplayError(f"{name} must be a sha256 digest")
    return text


def _raw_file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _boundary(payload: Mapping, *, name: str) -> None:
    if payload.get("version") != CAMPAIGN_VERSION:
        raise OrfsInterferenceReplayError(f"{name} version is invalid")
    if payload.get("lane") != "EVOLUTION_CHALLENGE":
        raise OrfsInterferenceReplayError(f"{name} lane is invalid")
    if payload.get("challenge_reason") != "MEMORY_INTERFERENCE":
        raise OrfsInterferenceReplayError(f"{name} challenge reason is invalid")
    if payload.get("evaluation_only") is not True:
        raise OrfsInterferenceReplayError(f"{name} is not evaluation-only")
    if payload.get("canonical_memory_mutation") != "none":
        raise OrfsInterferenceReplayError(f"{name} crosses canonical-memory boundary")
    if payload.get("production_runtime_imported") is not False:
        raise OrfsInterferenceReplayError(f"{name} crosses production-runtime boundary")
    if payload.get("memory_docs_submitted") is not False:
        raise OrfsInterferenceReplayError(f"{name} crosses memory/docs boundary")


def _failure_boundary(payload: Mapping, *, name: str) -> None:
    """Validate a terminal failure, accepting the pre-hardening field set.

    The first interrupted cross-design attempt was recorded by the operator
    before the producer learned to persist ``challenge_reason`` and the
    logical ``campaign_manifest_digest``.  Its raw manifest SHA is still
    useful diagnostic evidence, so replay accepts that narrow legacy shape
    while applying the same no-canonical/no-runtime/docs boundary.
    """
    if payload.get("version") != CAMPAIGN_VERSION:
        raise OrfsInterferenceReplayError(f"{name} version is invalid")
    if payload.get("lane") != "EVOLUTION_CHALLENGE":
        raise OrfsInterferenceReplayError(f"{name} lane is invalid")
    if payload.get("evaluation_only") is not True:
        raise OrfsInterferenceReplayError(f"{name} is not evaluation-only")
    if payload.get("canonical_memory_mutation") != "none":
        raise OrfsInterferenceReplayError(f"{name} crosses canonical-memory boundary")
    if payload.get("production_runtime_imported") is not False:
        raise OrfsInterferenceReplayError(f"{name} crosses production-runtime boundary")
    if payload.get("memory_docs_submitted") is not False:
        raise OrfsInterferenceReplayError(f"{name} crosses memory/docs boundary")


def _load_manifest(artifacts: Path) -> tuple[dict, dict, str, dict[str, StructuredRepairCandidate],
                                                 dict[str, MemoryRoutingDecision]]:
    receipts = artifacts / "receipts"
    manifest = _load_json(receipts / "campaign_manifest.json", "campaign manifest")
    cases_payload = _load_json(receipts / "cases.json", "cases manifest")
    _boundary(manifest, name="campaign manifest")
    campaign_id = _text(manifest.get("campaign_id"), "campaign_id")
    if manifest.get("candidate_policy") != "pre_registered_harmful_flow_config_delta":
        raise OrfsInterferenceReplayError("campaign candidate policy is invalid")
    core_utilization = _text(manifest.get("core_utilization"), "core_utilization")
    if not core_utilization.isdigit() or not 1 <= int(core_utilization) <= 100:
        raise OrfsInterferenceReplayError("campaign core_utilization is invalid")
    if manifest.get("cases") != cases_payload.get("cases"):
        raise OrfsInterferenceReplayError("campaign and cases manifests disagree")
    if manifest.get("candidate_payloads") != cases_payload.get("candidate_payloads"):
        raise OrfsInterferenceReplayError("campaign and candidate manifests disagree")
    manifest_digest = _digest(manifest)

    raw_cases = manifest.get("cases")
    raw_candidates = manifest.get("candidate_payloads")
    raw_routing = cases_payload.get("routing")
    if not isinstance(raw_cases, list) or len(raw_cases) < 2:
        raise OrfsInterferenceReplayError("challenge requires at least two cases")
    if not isinstance(raw_candidates, Mapping) or not isinstance(raw_routing, Mapping):
        raise OrfsInterferenceReplayError("candidate/routing manifests are missing")

    candidates: dict[str, StructuredRepairCandidate] = {}
    case_ids: list[str] = []
    for raw_case in raw_cases:
        if not isinstance(raw_case, Mapping):
            raise OrfsInterferenceReplayError("challenge case is not an object")
        case_id = _text(raw_case.get("case_id"), "case_id")
        if case_id in case_ids:
            raise OrfsInterferenceReplayError("challenge case IDs are duplicated")
        case_ids.append(case_id)
        if set(raw_case) - {
                "case_id", "lineage_id", "project_dir", "platform", "target_check",
                "run_flow_script", "fix_signoff_script", "orfs_root", "openroad_exe",
                "yosys_exe", "pdk_root", "toolchain_root", "toolchain_manifest",
                "toolchain_digest", "oracle_digest", "platform_digest", "pdk_digest",
                "source_inputs", "source_digest", "routing_receipt_id", "routing_decision",
        }:
            raise OrfsInterferenceReplayError(f"challenge case {case_id} has unexpected fields")
        raw_candidate = raw_candidates.get(case_id)
        if raw_candidate is None:
            raise OrfsInterferenceReplayError(f"candidate is missing for {case_id}")
        try:
            candidate = StructuredRepairCandidate.from_dict(raw_candidate)
        except (TypeError, ValueError, KeyError) as exc:
            raise OrfsInterferenceReplayError(
                f"candidate receipt is invalid for {case_id}") from exc
        action = candidate.concrete_action
        payload = action.get("payload") if isinstance(action, Mapping) else None
        edits = payload.get("config_edits") if isinstance(payload, Mapping) else None
        if (action.get("domain") != "flow.CONFIG_DELTA" or
                edits != {"CORE_UTILIZATION": core_utilization}):
            raise OrfsInterferenceReplayError(
                f"candidate policy drifted for {case_id}")
        candidates[case_id] = candidate

    if set(raw_candidates) != set(case_ids) or set(raw_routing) != set(case_ids):
        raise OrfsInterferenceReplayError("candidate/routing manifests do not cover exactly all cases")
    routes: dict[str, MemoryRoutingDecision] = {}
    for case_id in case_ids:
        try:
            route = MemoryRoutingDecision.from_dict(raw_routing[case_id])
        except (TypeError, ValueError, KeyError) as exc:
            raise OrfsInterferenceReplayError(
                f"routing receipt is invalid for {case_id}") from exc
        case = next(item for item in raw_cases if item["case_id"] == case_id)
        if (case.get("routing_receipt_id") != route.routing_receipt_id or
                case.get("routing_decision") != route.decision):
            raise OrfsInterferenceReplayError(
                f"routing manifest binding is invalid for {case_id}")
        routes[case_id] = route
    return manifest, cases_payload, manifest_digest, candidates, routes


def _verify_case_sources(manifest: Mapping, cohort: OrfsPairedCohortReceipt) -> None:
    raw_cases = manifest["cases"]
    expected_ids = set(cohort.case_receipts)
    if {case["case_id"] for case in raw_cases} != expected_ids:
        raise OrfsInterferenceReplayError("cohort cases do not match campaign manifest")
    for case in raw_cases:
        case_id = case["case_id"]
        try:
            source_inputs = _source_inputs(case.get("source_inputs"))
            project = Path(str(case["project_dir"])).expanduser().resolve()
            source_digest = _source_binding(project, source_inputs)
            content_digest = _source_content_binding(project, source_inputs)
            _verify_external_source_inputs(source_inputs)
        except Exception as exc:
            raise OrfsInterferenceReplayError(
                f"external source replay failed for {case_id}: {exc}") from exc
        if (cohort.source_digests.get(case_id) != source_digest or
                cohort.source_content_digests.get(case_id) != content_digest):
            raise OrfsInterferenceReplayError(
                f"external source digest drifted for {case_id}")
        for field in ("toolchain_digest", "oracle_digest", "platform_digest", "pdk_digest"):
            if case.get(field) != getattr(cohort, field):
                raise OrfsInterferenceReplayError(
                    f"fixed {field} drifted for {case_id}")


def _verify_cohort(campaign: Mapping, cases_payload: Mapping, manifest_digest: str,
                   candidates: Mapping[str, StructuredRepairCandidate],
                   routes: Mapping[str, MemoryRoutingDecision],
                   cohort_payload: Mapping) -> OrfsPairedCohortReceipt:
    try:
        cohort = OrfsPairedCohortReceipt.from_dict(cohort_payload)
    except (TypeError, ValueError, KeyError) as exc:
        raise OrfsInterferenceReplayError("ORFS cohort receipt is invalid") from exc
    supplied = cohort_payload.get("receipt_digest")
    if supplied not in {cohort.receipt_digest, cohort.legacy_receipt_digest}:
        raise OrfsInterferenceReplayError("ORFS cohort receipt digest mismatch")
    if (cohort.campaign_id != campaign["campaign_id"] or
            cohort.campaign_manifest_digest != manifest_digest):
        raise OrfsInterferenceReplayError("ORFS cohort campaign binding is invalid")
    _verify_case_sources(campaign, cohort)
    raw_cases = {item["case_id"]: item for item in campaign["cases"]}
    if set(routes) != set(cohort.case_receipts):
        raise OrfsInterferenceReplayError("ORFS cohort routing coverage is invalid")
    for case_id, pair in cohort.case_receipts.items():
        case = raw_cases[case_id]
        route = routes[case_id]
        if (pair.case_digest != _digest(case) or pair.lineage_id != case.get("lineage_id") or
                pair.routing_receipt_id != route.routing_receipt_id or
                pair.routing_decision != route.decision):
            raise OrfsInterferenceReplayError(
                f"ORFS paired receipt binding is invalid for {case_id}")
        candidate = candidates[case_id]
        for arm in P12_ARMS:
            receipt = pair.arm_receipts[arm]
            if receipt.case_id != case_id or receipt.budget != cohort.candidate_budget:
                raise OrfsInterferenceReplayError(
                    f"ORFS arm receipt binding is invalid for {case_id}/{arm}")
            if receipt.toolchain_digest != cohort.toolchain_digest or receipt.oracle_digest != cohort.oracle_digest:
                raise OrfsInterferenceReplayError(
                    f"ORFS arm fixed-environment digest drifted for {case_id}/{arm}")
            if arm == "NO_MEMORY":
                if receipt.source != "no_memory" or receipt.candidate_id != "no_memory:" + case_id:
                    raise OrfsInterferenceReplayError(
                        f"NO_MEMORY arm is malformed for {case_id}")
            elif receipt.source == "structured_memory":
                if (receipt.candidate_id != candidate.candidate_id or
                        receipt.candidate_digest != candidate.candidate_digest or
                        receipt.action_digest != _digest(candidate.concrete_action)):
                    raise OrfsInterferenceReplayError(
                        f"structured-memory arm is not bound to candidate for {case_id}/{arm}")
            else:
                if not (arm == "CAUSAL_NO_SKILL" and
                        route.decision in {"NO_SKILL", "ABSTAIN", "INAPPLICABLE"} and
                        receipt.source == "no_memory"):
                    raise OrfsInterferenceReplayError(
                        f"ORFS arm source is invalid for {case_id}/{arm}")
    return cohort


def _replay_downstream(artifacts: Path, campaign: Mapping, cases_payload: Mapping,
                       manifest_digest: str, candidates: Mapping[str, StructuredRepairCandidate],
                       routes: Mapping[str, MemoryRoutingDecision], cohort: OrfsPairedCohortReceipt,
                       *, require_downstream: bool) -> dict:
    receipts = artifacts / "receipts"
    derivation_payload = _load_json(receipts / "reason_derivation.json", "reason derivation")
    raw_derivations = derivation_payload.get("derivations")
    raw_errors = derivation_payload.get("errors")
    if not isinstance(raw_derivations, Mapping) or not isinstance(raw_errors, Mapping):
        raise OrfsInterferenceReplayError("reason derivation receipt is malformed")
    if raw_errors:
        if require_downstream:
            raise OrfsInterferenceReplayError("successful challenge contains derivation errors")
        if not raw_derivations:
            return {"derivation_error_count": len(raw_errors)}
        raise OrfsInterferenceReplayError("failed challenge has partial derivations")
    if set(raw_derivations) != set(cohort.case_receipts):
        raise OrfsInterferenceReplayError("reason derivation coverage is invalid")
    derivations: dict[str, tuple[EvolutionReasonDerivationReceipt, ...]] = {}
    for case_id, raw_items in raw_derivations.items():
        if not isinstance(raw_items, list) or not raw_items:
            raise OrfsInterferenceReplayError(f"reason derivations are empty for {case_id}")
        checked: list[EvolutionReasonDerivationReceipt] = []
        for raw in raw_items:
            try:
                item = EvolutionReasonDerivationReceipt.from_dict(raw)
            except (TypeError, ValueError, KeyError) as exc:
                raise OrfsInterferenceReplayError(
                    f"reason derivation is invalid for {case_id}") from exc
            expected = derive_memory_interference_reason(
                cohort.case_receipts[case_id], campaign_id=campaign["campaign_id"],
                memory_arm="ALWAYS_MEMORY")
            if expected is None or item.receipt_digest != expected.receipt_digest:
                raise OrfsInterferenceReplayError(
                    f"typed memory-interference derivation drifted for {case_id}")
            checked.append(item)
        derivations[case_id] = tuple(checked)

    reason_payload = _load_json(receipts / "p13_reason_receipt.json", "P13 reason receipt")
    try:
        reason_receipt = P13EvolutionReasonReceipt.from_dict(reason_payload)
    except (TypeError, ValueError, KeyError) as exc:
        raise OrfsInterferenceReplayError("P13 reason receipt is invalid") from exc
    if reason_payload.get("receipt_digest") != reason_receipt.receipt_digest:
        raise OrfsInterferenceReplayError("P13 reason receipt digest mismatch")
    expected_reason = p13_reason_receipt_from_derivations(
        derivations, campaign_id=campaign["campaign_id"],
        cohort_receipt_digest=cohort.receipt_digest)
    if reason_receipt.receipt_digest != expected_reason.receipt_digest:
        raise OrfsInterferenceReplayError("P13 typed reason aggregation drifted")
    eligibility = {case_id: True for case_id in cohort.case_receipts}
    expected_triggers = build_p12_shadow_update_triggers_from_reason_receipt(
        cohort, memory_arm="ALWAYS_MEMORY", learner_eligible=True,
        reason_receipt=reason_receipt, min_lineages=2,
        routing_decisions=routes, case_learner_eligibility=eligibility,
        derivation_receipts=derivations)
    trigger_payload = _load_json(receipts / "p12_triggers.json", "P12 trigger receipt")
    raw_triggers = trigger_payload.get("triggers")
    if not isinstance(raw_triggers, list) or len(raw_triggers) != len(expected_triggers):
        raise OrfsInterferenceReplayError("P12 trigger coverage is invalid")
    stored_triggers: dict[str, P12ShadowUpdateTriggerReceipt] = {}
    for raw in raw_triggers:
        try:
            item = P12ShadowUpdateTriggerReceipt.from_dict(raw)
        except (TypeError, ValueError, KeyError) as exc:
            raise OrfsInterferenceReplayError("P12 trigger receipt is invalid") from exc
        if raw.get("receipt_digest") != item.receipt_digest:
            raise OrfsInterferenceReplayError("P12 trigger digest mismatch")
        if item.case_id in stored_triggers:
            raise OrfsInterferenceReplayError("P12 trigger case IDs are duplicated")
        stored_triggers[item.case_id] = item
    if {item.case_id for item in expected_triggers} != set(stored_triggers):
        raise OrfsInterferenceReplayError("P12 trigger case coverage is invalid")
    for expected in expected_triggers:
        if stored_triggers[expected.case_id].receipt_digest != expected.receipt_digest:
            raise OrfsInterferenceReplayError(
                f"P12 trigger derivation drifted for {expected.case_id}")

    admission_payload = _load_json(receipts / "admissions.json", "admission receipts")
    raw_admissions = admission_payload.get("admissions")
    if not isinstance(raw_admissions, Mapping) or set(raw_admissions) != set(derivations):
        raise OrfsInterferenceReplayError("admission coverage is invalid")
    admitted_count = 0
    for case_id, raw in raw_admissions.items():
        try:
            item = EvolutionAdmissionReceipt.from_dict(raw)
        except (TypeError, ValueError, KeyError) as exc:
            raise OrfsInterferenceReplayError(
                f"admission receipt is invalid for {case_id}") from exc
        expected = admit_evolution_reason(
            derivations[case_id][0], campaign_id=campaign["campaign_id"],
            learner_eligible=True, paired=cohort.case_receipts[case_id],
            memory_arm="ALWAYS_MEMORY")
        if raw.get("receipt_digest") != item.receipt_digest or item.receipt_digest != expected.receipt_digest:
            raise OrfsInterferenceReplayError(
                f"admission derivation drifted for {case_id}")
        admitted_count += int(item.admitted)
    summary = _load_json(artifacts / "summary.json", "challenge summary")
    _boundary(summary, name="challenge summary")
    if (summary.get("campaign_id") != campaign["campaign_id"] or
            summary.get("cohort_receipt_digest") != cohort.receipt_digest or
            summary.get("case_count") != len(cohort.case_receipts) or
            summary.get("lineage_count") != cohort.lineage_count or
            summary.get("triggered_count") != sum(item.triggered for item in expected_triggers) or
            summary.get("admitted_count") != admitted_count):
        raise OrfsInterferenceReplayError("challenge summary disagrees with receipts")
    return {
        "derivation_count": sum(len(items) for items in derivations.values()),
        "triggered_count": sum(item.triggered for item in expected_triggers),
        "admitted_count": admitted_count,
    }


def replay(artifacts: Path | str) -> dict:
    """Replay one completed or terminal ORFS challenge artifact read-only."""
    artifacts = Path(artifacts).expanduser().resolve()
    if not artifacts.is_dir():
        raise OrfsInterferenceReplayError(f"artifacts is not a directory: {artifacts}")
    manifest, cases_payload, manifest_digest, candidates, routes = _load_manifest(artifacts)
    failure_path = artifacts / "failure.json"
    cohort_path = artifacts / "receipts" / "cohort.json"
    if not cohort_path.is_file():
        if not failure_path.is_file():
            raise OrfsInterferenceReplayError("challenge has neither cohort nor terminal failure")
        failure = _load_json(failure_path, "challenge failure")
        _failure_boundary(failure, name="challenge failure")
        status = _text(failure.get("status"), "failure status")
        if status not in _TERMINAL_WITHOUT_COHORT:
            raise OrfsInterferenceReplayError(
                "failure without cohort must be an execution terminal state")
        declared_manifest = failure.get("campaign_manifest_digest")
        if declared_manifest is not None:
            if declared_manifest != manifest_digest:
                raise OrfsInterferenceReplayError("terminal failure manifest binding is invalid")
        else:
            declared_manifest = failure.get("manifest_sha256")
            raw_manifest = _raw_file_digest(
                artifacts / "receipts" / "campaign_manifest.json")
            if declared_manifest != raw_manifest:
                raise OrfsInterferenceReplayError(
                    "terminal failure raw manifest digest is invalid")
        if failure.get("campaign_id") != manifest["campaign_id"] or \
                failure.get("cohort_available") is not False:
            raise OrfsInterferenceReplayError("terminal failure boundary is invalid")
        return {
            "mode": "replay", "status": "REPLAY_PASS",
            "terminal_status": status, "campaign_id": manifest["campaign_id"],
            "manifest_digest": manifest_digest, "cohort_available": False,
            "evaluation_only": True, "canonical_memory_mutation": "none",
            "production_runtime_imported": False, "memory_docs_submitted": False,
        }

    cohort_payload = _load_json(cohort_path, "ORFS cohort")
    cohort = _verify_cohort(
        manifest, cases_payload, manifest_digest, candidates, routes, cohort_payload)
    if failure_path.is_file():
        failure = _load_json(failure_path, "challenge failure")
        _boundary(failure, name="challenge failure")
        if (failure.get("status") != "REASON_DERIVATION_FAILED" or
                failure.get("campaign_id") != manifest["campaign_id"] or
                failure.get("campaign_manifest_digest") not in {None, manifest_digest} or
                failure.get("cohort_available") not in {None, True}):
            raise OrfsInterferenceReplayError("cohort failure boundary is invalid")
        downstream = _replay_downstream(
            artifacts, manifest, cases_payload, manifest_digest, candidates, routes,
            cohort, require_downstream=False)
        if "derivation_error_count" not in downstream:
            raise OrfsInterferenceReplayError(
                "reason-derivation failure must retain non-empty errors")
        return {
            "mode": "replay", "status": "REPLAY_PASS",
            "terminal_status": "REASON_DERIVATION_FAILED",
            "campaign_id": manifest["campaign_id"], "manifest_digest": manifest_digest,
            "cohort_receipt_digest": cohort.receipt_digest,
            "cohort_available": True, **downstream,
            "evaluation_only": True, "canonical_memory_mutation": "none",
            "production_runtime_imported": False, "memory_docs_submitted": False,
        }
    downstream = _replay_downstream(
        artifacts, manifest, cases_payload, manifest_digest, candidates, routes,
        cohort, require_downstream=True)
    return {
        "mode": "replay", "status": "REPLAY_PASS", "terminal_status": "COMPLETE",
        "campaign_id": manifest["campaign_id"], "manifest_digest": manifest_digest,
        "cohort_receipt_digest": cohort.receipt_digest,
        "case_count": len(cohort.case_receipts), "lineage_count": cohort.lineage_count,
        "cohort_available": True, **downstream,
        "evaluation_only": True, "canonical_memory_mutation": "none",
        "production_runtime_imported": False, "memory_docs_submitted": False,
    }


__all__ = ["OrfsInterferenceReplayError", "replay"]
