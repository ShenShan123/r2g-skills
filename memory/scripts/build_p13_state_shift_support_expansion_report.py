#!/usr/bin/env python3
"""Derive the proposed StateShift support expansion from frozen P12 evidence.

This command replays the source-bound plan, learner partition, P12 cohort,
preregistered StateShift contexts, parent Knowledge, and parent support
envelope.  It emits a typed child Knowledge/envelope proposal only.  It does
not run EDA, build an anti-forgetting witness, execute a shadow mutation, or
write canonical/production state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.evaluation import OrfsPairedCohortReceipt  # noqa: E402
from tehm.evolution import (  # noqa: E402
    LocalizedUpdatePlan, StateShiftEvolutionProposal,
    StateResolutionRebaseReceipt,
    StateShiftSupportExpansionError,
    derive_state_shift_support_expansion,
    state_semantic_digest,
)
from tehm.ids import stable_dumps  # noqa: E402
from tehm.knowledge import get_knowledge_by_object_id  # noqa: E402
from tehm.state import (  # noqa: E402
    ResolvedMemoryState, StateShiftReceipt, SupportEnvelope,
    SuppressionReceipt,
)


REPORT_VERSION = "p13-state-shift-support-expansion-report-v1"
SOURCE_BOUND_VERSION = "p13-state-shift-source-bound-plan-v1"
SOURCE_SNAPSHOT_VERSION = "p13-state-shift-source-snapshot-v1"
PLAN_REPORT_VERSION = "p13-state-shift-plan-report-v1"
PROPOSAL_REPORT_VERSION = "p13-state-shift-proposal-report-v1"
ADMISSION_REPORT_VERSION = "p13-state-shift-admission-report-v1"
COHORT_REPORT_VERSION = "p12-orfs-cohort-run-report-v1"
PARTITION_VERSION = "p13-learner-partition-v1"


class P13StateShiftSupportExpansionReportError(ValueError):
    """The frozen chain cannot justify a support-envelope expansion."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _load(path: Path, name: str) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise P13StateShiftSupportExpansionReportError(
            f"cannot read {name}: {path}") from exc
    if not isinstance(payload, dict):
        raise P13StateShiftSupportExpansionReportError(
            f"{name} must be a JSON object")
    return payload


def _content_digest(payload: Mapping, field: str, name: str) -> str:
    supplied = payload.get(field)
    if type(supplied) is not str or not supplied.startswith("sha256:"):
        raise P13StateShiftSupportExpansionReportError(
            f"{name} requires {field}")
    replay = dict(payload)
    replay.pop(field, None)
    if supplied != _digest(replay):
        raise P13StateShiftSupportExpansionReportError(
            f"{name} {field} mismatch")
    return supplied


def _path(raw: object, *, relative_to: Path, name: str) -> Path:
    if type(raw) is not str or not raw.strip():
        raise P13StateShiftSupportExpansionReportError(
            f"{name} path is missing")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = relative_to.parent / path
    path = path.resolve()
    if not path.is_file():
        raise P13StateShiftSupportExpansionReportError(
            f"{name} is not a file")
    return path


def _binding(raw: object, *, relative_to: Path, name: str) -> tuple[dict, Path]:
    if not isinstance(raw, Mapping):
        raise P13StateShiftSupportExpansionReportError(
            f"{name} binding is missing")
    path = _path(raw.get("path"), relative_to=relative_to, name=name)
    if raw.get("sha256") != _sha256(path):
        raise P13StateShiftSupportExpansionReportError(
            f"{name} file binding mismatch")
    return dict(raw), path


def _authority_closed(payload: Mapping, name: str) -> None:
    if (payload.get("evaluation_only") is not True or
            payload.get("canonical_memory_mutation") != "none" or
            payload.get("production_runtime_imported") is not False or
            payload.get("production_integration") not in {
                None, "not_attempted"} or
            payload.get("memory_docs_submitted") not in {None, False}):
        raise P13StateShiftSupportExpansionReportError(
            f"{name} crossed an authority boundary")


def _logical_digest(path: Path) -> str:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return _digest("\n".join(conn.iterdump()))
    finally:
        conn.close()


def _resolved_state(raw: object) -> ResolvedMemoryState:
    if not isinstance(raw, Mapping):
        raise P13StateShiftSupportExpansionReportError(
            "source snapshot resolved state is malformed")
    try:
        suppressed = tuple(
            item if isinstance(item, SuppressionReceipt)
            else SuppressionReceipt(**dict(item))
            for item in raw.get("suppressed", ()))
        return ResolvedMemoryState(
            resolution_id=raw["resolution_id"],
            input_memory_digest=raw["input_memory_digest"],
            scope=dict(raw["scope"]),
            active_rules=tuple(raw["active_rules"]),
            active_causal_paths=tuple(raw["active_causal_paths"]),
            active_knowledge_claims=tuple(raw["active_knowledge_claims"]),
            active_assets=tuple(raw["active_assets"]),
            active_capabilities=tuple(raw["active_capabilities"]),
            suppressed=suppressed,
            unresolved_conflicts=tuple(raw["unresolved_conflicts"]),
            relation_ids=tuple(raw["relation_ids"]),
            shadow_relation_ids=tuple(raw["shadow_relation_ids"]),
            resolution_digest=raw["resolution_digest"],
            resolver_version=raw["resolver_version"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise P13StateShiftSupportExpansionReportError(
            "source snapshot resolved state is malformed") from exc


def _load_parent(source: Mapping, source_path: Path, parent_id: str):
    database = source.get("source_database")
    if not isinstance(database, Mapping):
        raise P13StateShiftSupportExpansionReportError(
            "source snapshot database binding is missing")
    database_path = _path(
        database.get("path"), relative_to=source_path, name="source database")
    if (database.get("sha256") != _sha256(database_path) or
            database.get("logical_digest") != _logical_digest(database_path) or
            database.get("sidecar_free") is not True):
        raise P13StateShiftSupportExpansionReportError(
            "source database binding mismatch")
    replay = source.get("replay")
    state = replay.get("resolved_source_state") if isinstance(
        replay, Mapping) else None
    if not isinstance(state, Mapping):
        raise P13StateShiftSupportExpansionReportError(
            "source snapshot resolved state is missing")
    scope = state.get("scope")
    if not isinstance(scope, Mapping):
        raise P13StateShiftSupportExpansionReportError(
            "source snapshot scope is malformed")
    target_scope = scope.get("target_scope")
    if type(target_scope) is not str or not target_scope:
        raise P13StateShiftSupportExpansionReportError(
            "source snapshot target scope is missing")
    conn = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        parent = get_knowledge_by_object_id(
            conn, parent_id, target_scope=target_scope)
    finally:
        conn.close()
    if (parent.object_id not in state.get("active_knowledge_claims", ()) or
            parent.content_digest != replay.get("knowledge_content_digest")):
        raise P13StateShiftSupportExpansionReportError(
            "source snapshot parent Knowledge mismatch")
    return parent, dict(state), database_path


def _p12_inputs(
    admission: Mapping,
    admission_path: Path,
) -> tuple[dict, Path, dict, Path, dict, Path]:
    inputs = admission.get("inputs")
    if not isinstance(inputs, Mapping):
        raise P13StateShiftSupportExpansionReportError(
            "admission inputs are missing")
    cohort_binding, cohort_path = _binding(
        inputs.get("cohort"), relative_to=admission_path, name="P12 cohort")
    partition_binding, partition_path = _binding(
        inputs.get("learner_partition"), relative_to=admission_path,
        name="learner partition")
    audit_binding, audit_path = _binding(
        inputs.get("preregistration_audit"), relative_to=admission_path,
        name="preregistration audit")
    return (
        cohort_binding, cohort_path, partition_binding, partition_path,
        audit_binding, audit_path,
    )


def build_p13_state_shift_support_expansion_report(
    source_bound_plan_report: Path | str,
    *,
    output: Path | str,
) -> dict:
    """Replay the real support expansion and keep all execution gates closed."""
    source_bound_path = Path(source_bound_plan_report).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    if output_path == source_bound_path:
        raise P13StateShiftSupportExpansionReportError(
            "support expansion output must differ from source-bound plan")
    source_bound = _load(source_bound_path, "source-bound plan report")
    if source_bound.get("version") != SOURCE_BOUND_VERSION:
        raise P13StateShiftSupportExpansionReportError(
            "source-bound plan version mismatch")
    source_bound_digest = _content_digest(
        source_bound, "report_digest", "source-bound plan report")
    _authority_closed(source_bound, "source-bound plan report")
    if (source_bound.get("eligible_for_anti_forgetting") is not True or
            source_bound.get("anti_forgetting_present") is not False or
            source_bound.get("shadow_update_attempted") is not False or
            source_bound.get("shadow_execution_ready") is not False):
        raise P13StateShiftSupportExpansionReportError(
            "source-bound plan is not at the pre-expansion boundary")
    raw_plan = source_bound.get("source_bound_localized_update_plan")
    try:
        plan = LocalizedUpdatePlan.from_dict(raw_plan)
    except (TypeError, ValueError) as exc:
        raise P13StateShiftSupportExpansionReportError(
            f"source-bound localized plan is invalid: {exc}") from exc
    if (not isinstance(raw_plan, Mapping) or
            raw_plan.get("plan_digest") != plan.plan_digest or
            plan.operation != "REVISE" or
            plan.update_target != "UPDATE_CAUSAL_KNOWLEDGE" or
            plan.failure_type != "STATE_SHIFT" or
            len(plan.knowledge_refs) != 1):
        raise P13StateShiftSupportExpansionReportError(
            "source-bound plan is not a StateShift Knowledge revision")

    source_binding, source_path = _binding(
        source_bound.get("source_snapshot_report"),
        relative_to=source_bound_path, name="source snapshot report")
    source = _load(source_path, "source snapshot report")
    if source.get("version") != SOURCE_SNAPSHOT_VERSION:
        raise P13StateShiftSupportExpansionReportError(
            "source snapshot version mismatch")
    source_digest = _content_digest(
        source, "report_digest", "source snapshot report")
    if source_binding.get("report_digest") != source_digest:
        raise P13StateShiftSupportExpansionReportError(
            "source snapshot content binding mismatch")
    _authority_closed(source, "source snapshot report")
    parent, resolved_state, database_path = _load_parent(
        source, source_path, plan.knowledge_refs[0])
    source_database = source.get("source_database")
    source_bound_database = source_bound.get("source_database")
    if (not isinstance(source_database, Mapping) or
            not isinstance(source_bound_database, Mapping) or
            source_bound_database.get("sha256") !=
            source_database.get("sha256") or
            source_bound_database.get("logical_digest") !=
            source_database.get("logical_digest") or
            source_bound_database.get("opened_read_only") is not True or
            _path(source_bound_database.get("path"),
                  relative_to=source_bound_path,
                  name="source-bound database") != database_path):
        raise P13StateShiftSupportExpansionReportError(
            "source-bound database differs from source snapshot")

    plan_binding, plan_path = _binding(
        source_bound.get("admitted_plan_report"),
        relative_to=source_bound_path, name="admitted plan report")
    plan_report = _load(plan_path, "admitted plan report")
    if plan_report.get("version") != PLAN_REPORT_VERSION:
        raise P13StateShiftSupportExpansionReportError(
            "admitted plan report version mismatch")
    plan_report_digest = _content_digest(
        plan_report, "report_digest", "admitted plan report")
    if plan_binding.get("report_digest") != plan_report_digest:
        raise P13StateShiftSupportExpansionReportError(
            "admitted plan content binding mismatch")
    _authority_closed(plan_report, "admitted plan report")
    raw_admitted_plan = plan_report.get("localized_update_plan")
    try:
        admitted_plan = LocalizedUpdatePlan.from_dict(raw_admitted_plan)
        rebase = StateResolutionRebaseReceipt.from_dict(
            source_bound.get("resolution_rebase"))
    except (TypeError, ValueError) as exc:
        raise P13StateShiftSupportExpansionReportError(
            f"source rebase chain is invalid: {exc}") from exc
    if (not isinstance(raw_admitted_plan, Mapping) or
            raw_admitted_plan.get("plan_digest") != admitted_plan.plan_digest or
            plan_binding.get("plan_digest") != admitted_plan.plan_digest or
            rebase.admitted_plan_digest != admitted_plan.plan_digest or
            rebase.original_resolution_id != admitted_plan.state_resolution_id or
            rebase.source_resolution_id != plan.state_resolution_id or
            rebase.source_resolution_id != resolved_state["resolution_id"] or
            rebase.source_database_digest !=
            source_database["logical_digest"] or
            rebase.source_semantic_digest != state_semantic_digest(
                _resolved_state(resolved_state)) or
            rebase.receipt_digest not in plan.evidence_refs):
        raise P13StateShiftSupportExpansionReportError(
            "source-bound plan does not replay its admitted-plan rebase")

    proposal_binding, proposal_path = _binding(
        plan_report.get("proposal_report"), relative_to=plan_path,
        name="StateShift proposal report")
    proposal_report = _load(proposal_path, "StateShift proposal report")
    if proposal_report.get("version") != PROPOSAL_REPORT_VERSION:
        raise P13StateShiftSupportExpansionReportError(
            "StateShift proposal report version mismatch")
    proposal_report_digest = _content_digest(
        proposal_report, "report_digest", "StateShift proposal report")
    if proposal_binding.get("report_digest") != proposal_report_digest:
        raise P13StateShiftSupportExpansionReportError(
            "StateShift proposal content binding mismatch")
    try:
        proposal = StateShiftEvolutionProposal.from_dict(
            proposal_report.get("proposal"))
    except (TypeError, ValueError) as exc:
        raise P13StateShiftSupportExpansionReportError(
            f"StateShift proposal is invalid: {exc}") from exc
    if (proposal.operation != "REVISE" or
            proposal.evolution_reason != "SUPPORT_ENVELOPE_EXPANSION" or
            proposal.knowledge_object_id != parent.object_id or
            proposal.proposal_digest not in plan.evidence_refs):
        raise P13StateShiftSupportExpansionReportError(
            "StateShift proposal does not authorize support expansion")

    admission_binding, admission_path = _binding(
        plan_report.get("admission_report"), relative_to=plan_path,
        name="StateShift admission report")
    admission = _load(admission_path, "StateShift admission report")
    if admission.get("version") != ADMISSION_REPORT_VERSION:
        raise P13StateShiftSupportExpansionReportError(
            "StateShift admission report version mismatch")
    admission_digest = _content_digest(
        admission, "report_digest", "StateShift admission report")
    if admission_binding.get("report_digest") != admission_digest:
        raise P13StateShiftSupportExpansionReportError(
            "StateShift admission content binding mismatch")
    _authority_closed(admission, "StateShift admission report")
    (cohort_binding, cohort_path, partition_binding, partition_path,
     audit_binding, audit_path) = _p12_inputs(admission, admission_path)

    cohort_report = _load(cohort_path, "P12 cohort report")
    if cohort_report.get("report_version") != COHORT_REPORT_VERSION:
        raise P13StateShiftSupportExpansionReportError(
            "P12 cohort report version mismatch")
    try:
        cohort = OrfsPairedCohortReceipt.from_dict(
            cohort_report.get("cohort_receipt", cohort_report))
    except (TypeError, ValueError) as exc:
        raise P13StateShiftSupportExpansionReportError(
            f"P12 cohort receipt is invalid: {exc}") from exc
    if (cohort_report.get("cohort_receipt_digest") != cohort.receipt_digest or
            cohort_report.get("receipt_digest") != cohort.receipt_digest or
            admission.get("cohort_receipt_digest") != cohort.receipt_digest or
            cohort.receipt_digest not in plan.evidence_refs):
        raise P13StateShiftSupportExpansionReportError(
            "P12 cohort receipt binding mismatch")
    _authority_closed(cohort_report, "P12 cohort report")

    partition = _load(partition_path, "learner partition")
    if (partition.get("version") != PARTITION_VERSION or
            partition.get("campaign_id") != cohort.campaign_id or
            partition.get("learner_eligible") is not True):
        raise P13StateShiftSupportExpansionReportError(
            "learner partition campaign or authority mismatch")
    _authority_closed(partition, "learner partition")

    audit = _load(audit_path, "preregistration audit")
    audit_digest = _content_digest(
        audit, "audit_digest", "preregistration audit")
    if admission.get("preregistration_audit_digest") != audit_digest:
        raise P13StateShiftSupportExpansionReportError(
            "preregistration audit content binding mismatch")
    if (audit.get("execution_started") is not False or
            audit.get("production_database_writes") is not False or
            audit.get("promotion_attempted") is not False):
        raise P13StateShiftSupportExpansionReportError(
            "preregistration audit crossed an execution boundary")
    parent_replay = audit.get("parent_replay")
    try:
        parent_envelope = SupportEnvelope.from_dict(
            parent_replay.get("support_envelope") if isinstance(
                parent_replay, Mapping) else None)
    except (TypeError, ValueError) as exc:
        raise P13StateShiftSupportExpansionReportError(
            f"parent support envelope is invalid: {exc}") from exc
    if (parent_envelope.knowledge_object_id != parent.object_id or
            parent_envelope.envelope_digest != source.get("replay", {}).get(
                "support_envelope_digest")):
        raise P13StateShiftSupportExpansionReportError(
            "parent support envelope differs from source replay")

    raw_cases = audit.get("cases")
    if not isinstance(raw_cases, list):
        raise P13StateShiftSupportExpansionReportError(
            "preregistration StateShift cases are missing")
    contexts = {}
    shifts = {}
    for item in raw_cases:
        if not isinstance(item, Mapping):
            raise P13StateShiftSupportExpansionReportError(
                "preregistration StateShift case is malformed")
        case_id = item.get("case_id")
        registration = item.get("registration")
        if (type(case_id) is not str or case_id in contexts or
                not isinstance(registration, Mapping)):
            raise P13StateShiftSupportExpansionReportError(
                "preregistration StateShift case identity is invalid")
        try:
            shift = StateShiftReceipt.from_dict(item.get("state_shift"))
        except (TypeError, ValueError) as exc:
            raise P13StateShiftSupportExpansionReportError(
                f"StateShift receipt is invalid: {exc}") from exc
        context = registration.get("current_context")
        if (not isinstance(context, Mapping) or
                registration.get("current_context_digest") !=
                shift.current_context_digest):
            raise P13StateShiftSupportExpansionReportError(
                "StateShift context binding is invalid")
        contexts[case_id] = dict(context)
        shifts[case_id] = shift

    shifted_dimensions = sorted({
        dimension
        for shift in shifts.values()
        for dimension in shift.shifted_dimensions
    })
    if (set(contexts) != set(cohort.case_receipts) or
            set(proposal.trigger_receipt_ids) != {
                shift.receipt_id for shift in shifts.values()} or
            sorted(proposal.state_context_digests) != sorted(
                shift.current_context_digest for shift in shifts.values()) or
            sorted(proposal.shifted_dimensions) != shifted_dimensions):
        raise P13StateShiftSupportExpansionReportError(
            "StateShift proposal differs from preregistered target cases")

    try:
        child, child_envelope, receipt = derive_state_shift_support_expansion(
            campaign_id=cohort.campaign_id,
            proposal_digest=proposal.proposal_digest,
            parent_knowledge=parent, parent_envelope=parent_envelope,
            resolved_state=resolved_state, cohort=cohort,
            state_shifts=shifts, current_contexts=contexts,
            learner_partition=partition.get("cases"),
        )
    except (StateShiftSupportExpansionError, TypeError, ValueError) as exc:
        raise P13StateShiftSupportExpansionReportError(str(exc)) from exc

    report = {
        "version": REPORT_VERSION,
        "campaign_id": cohort.campaign_id,
        "source_bound_plan_report": {
            "path": str(source_bound_path), "sha256": _sha256(source_bound_path),
            "report_digest": source_bound_digest,
            "source_bound_plan_digest": plan.plan_digest,
        },
        "source_snapshot_report": {
            "path": str(source_path), "sha256": _sha256(source_path),
            "report_digest": source_digest,
        },
        "source_database": {
            "path": str(database_path),
            "sha256": source["source_database"]["sha256"],
            "logical_digest": source["source_database"]["logical_digest"],
            "opened_read_only": True,
        },
        "proposal_report": {
            "path": str(proposal_path), "sha256": _sha256(proposal_path),
            "report_digest": proposal_report_digest,
            "proposal_digest": proposal.proposal_digest,
        },
        "p12_cohort_report": {
            "path": str(cohort_path), "sha256": cohort_binding["sha256"],
            "cohort_receipt_digest": cohort.receipt_digest,
        },
        "learner_partition": {
            "path": str(partition_path), "sha256": partition_binding["sha256"],
            "case_count": len(partition["cases"]),
            "all_training_learner_eligible": True,
        },
        "preregistration_audit": {
            "path": str(audit_path), "sha256": audit_binding["sha256"],
            "audit_digest": audit_digest,
        },
        "parent_knowledge": parent.to_dict(),
        "child_knowledge": child.to_dict(),
        "parent_support_envelope": parent_envelope.to_dict(),
        "child_support_envelope": child_envelope.to_dict(),
        "support_expansion_receipt": receipt.to_dict(),
        "target_replay": {
            "case_ids": sorted(cohort.case_receipts),
            "execution_digests": receipt.target_execution_digests,
            "before_transferable": {
                case_id: shifts[case_id].transferable
                for case_id in sorted(shifts)
            },
            "after_transferable": {
                case_id: True for case_id in sorted(shifts)
            },
            "passed": True,
        },
        "support_expansion_derived": True,
        "eligible_for_anti_forgetting": True,
        "scoped_parent_transition_replay_pending": True,
        "anti_forgetting_present": False,
        "shadow_update_attempted": False,
        "shadow_execution_ready": False,
        "remaining_gates": [
            "scoped_parent_transition_replay",
            "non_target_regression",
            "heldout_audit",
            "rollback_verification",
            "anti_forgetting_witness",
        ],
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "production_integration": "not_attempted",
        "memory_docs_submitted": False,
    }
    report["report_digest"] = _digest(report)
    if output_path.exists():
        raise P13StateShiftSupportExpansionReportError(
            f"immutable support expansion report already exists: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x") as stream:
        stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-bound-plan-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_p13_state_shift_support_expansion_report(
            args.source_bound_plan_report, output=args.output)
    except (OSError, sqlite3.Error,
            P13StateShiftSupportExpansionReportError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "output": str(args.output.expanduser().resolve()),
        "campaign_id": report["campaign_id"],
        "child_knowledge_object_id": report[
            "child_knowledge"]["knowledge_id"] + "@" + str(
                report["child_knowledge"]["version"]),
        "child_support_envelope_digest": report[
            "child_support_envelope"]["envelope_digest"],
        "support_expansion_receipt_digest": report[
            "support_expansion_receipt"]["receipt_digest"],
        "target_replay_passed": report["target_replay"]["passed"],
        "shadow_update_attempted": report["shadow_update_attempted"],
        "report_digest": report["report_digest"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
