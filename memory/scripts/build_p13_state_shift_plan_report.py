#!/usr/bin/env python3
"""Build a shadow-only LocalizedUpdatePlan from an admitted StateShift proposal.

This command replays the proposal/admission/trigger chain and emits a plan
receipt only.  It does not accept an anti-forgetting witness, open SQLite, or
execute a shadow update.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.evolution import (  # noqa: E402
    EvolutionAdmissionReceipt,
    P12ShadowUpdateTriggerReceipt,
    StateShiftEvolutionProposal,
    state_shift_proposal_to_localized_plan,
)
from tehm.ids import stable_dumps  # noqa: E402


REPORT_VERSION = "p13-state-shift-plan-report-v1"
PROPOSAL_REPORT_VERSION = "p13-state-shift-proposal-report-v1"
ADMISSION_REPORT_VERSION = "p13-state-shift-admission-report-v1"
TRIGGER_REPORT_VERSION = "p13-shadow-trigger-report-v1"


class P13StateShiftPlanReportError(ValueError):
    """The admitted proposal chain cannot safely produce a localized plan."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _digest(payload: Mapping) -> str:
    return "sha256:" + hashlib.sha256(
        stable_dumps(dict(payload)).encode()).hexdigest()


def _load(path: Path, name: str) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise P13StateShiftPlanReportError(
            f"cannot read {name}: {path}") from exc
    if not isinstance(payload, dict):
        raise P13StateShiftPlanReportError(f"{name} must be a JSON object")
    return payload


def _content_digest(payload: Mapping, field: str, name: str) -> str:
    supplied = payload.get(field)
    if type(supplied) is not str or not supplied.startswith("sha256:"):
        raise P13StateShiftPlanReportError(f"{name} requires {field}")
    replay = dict(payload)
    replay.pop(field, None)
    if supplied != _digest(replay):
        raise P13StateShiftPlanReportError(f"{name} {field} mismatch")
    return supplied


def _bound_path(raw: object, *, relative_to: Path, name: str) -> Path:
    if type(raw) is not str or not raw.strip():
        raise P13StateShiftPlanReportError(f"{name} path is missing")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = relative_to.parent / path
    path = path.resolve()
    if not path.is_file():
        raise P13StateShiftPlanReportError(f"{name} is not a file")
    return path


def _proposal(payload: Mapping) -> StateShiftEvolutionProposal:
    raw = payload.get("proposal")
    try:
        proposal = StateShiftEvolutionProposal.from_dict(raw)
    except (TypeError, ValueError) as exc:
        raise P13StateShiftPlanReportError(
            f"StateShift proposal is invalid: {exc}") from exc
    if (not isinstance(raw, Mapping) or
            raw.get("proposal_id") != proposal.proposal_id or
            raw.get("proposal_digest") != proposal.proposal_digest):
        raise P13StateShiftPlanReportError(
            "StateShift proposal identity or digest mismatch")
    if (proposal.operation == "RETAIN" or
            proposal.learner_eligible is not True
            or proposal.shadow_only is not True
            or proposal.evaluation_only is not True):
        raise P13StateShiftPlanReportError(
            "StateShift proposal is not eligible for localized planning")
    return proposal


def _admission_report(
    proposal_payload: Mapping,
    proposal_path: Path,
) -> tuple[dict, Path]:
    raw = proposal_payload.get("admission_report")
    if not isinstance(raw, Mapping):
        raise P13StateShiftPlanReportError(
            "proposal report lacks admission binding")
    path = _bound_path(
        raw.get("path"), relative_to=proposal_path, name="admission report")
    if path == proposal_path or raw.get("sha256") != _sha256(path):
        raise P13StateShiftPlanReportError(
            "proposal admission file binding mismatch")
    payload = _load(path, "admission report")
    if payload.get("version") != ADMISSION_REPORT_VERSION:
        raise P13StateShiftPlanReportError("admission report version mismatch")
    digest = _content_digest(payload, "report_digest", "admission report")
    if raw.get("report_digest") != digest:
        raise P13StateShiftPlanReportError(
            "proposal admission content binding mismatch")
    if (payload.get("p13_admission_eligible") is not True or
            payload.get("admitted_count") != payload.get("case_count") or
            payload.get("mutation_plan_present") is not False or
            payload.get("anti_forgetting_present") is not False or
            payload.get("shadow_update_attempted") is not False):
        raise P13StateShiftPlanReportError(
            "admission report is not an eligible pre-plan boundary")
    if (payload.get("evaluation_only") is not True or
            payload.get("canonical_memory_mutation") != "none" or
            payload.get("production_runtime_imported") is not False or
            payload.get("production_integration") != "not_attempted" or
            payload.get("memory_docs_submitted") is not False):
        raise P13StateShiftPlanReportError(
            "admission report crosses an authority boundary")
    return payload, path


def _admissions(
    payload: Mapping,
    case_ids: set[str],
    campaign_id: str,
) -> dict[str, EvolutionAdmissionReceipt]:
    raw = payload.get("admissions")
    if not isinstance(raw, Mapping) or set(raw) != case_ids:
        raise P13StateShiftPlanReportError(
            "admission receipts do not cover proposal cases")
    result = {}
    for case_id in sorted(case_ids):
        try:
            receipt = EvolutionAdmissionReceipt.from_dict(raw[case_id])
        except (TypeError, ValueError) as exc:
            raise P13StateShiftPlanReportError(
                f"admission receipt for {case_id} is invalid: {exc}") from exc
        if (receipt.case_id != case_id or receipt.campaign_id != campaign_id or
                receipt.reason != "STATE_SHIFT" or receipt.admitted is not True):
            raise P13StateShiftPlanReportError(
                f"admission receipt for {case_id} is not eligible")
        result[case_id] = receipt
    return result


def _triggers(
    admission_payload: Mapping,
    admission_path: Path,
    case_ids: set[str],
    campaign_id: str,
) -> tuple[dict[str, P12ShadowUpdateTriggerReceipt], Path, str]:
    inputs = admission_payload.get("inputs")
    if not isinstance(inputs, Mapping):
        raise P13StateShiftPlanReportError("admission input index is missing")
    binding = inputs.get("trigger_report")
    if not isinstance(binding, Mapping):
        raise P13StateShiftPlanReportError(
            "admission trigger-report binding is missing")
    path = _bound_path(
        binding.get("path"), relative_to=admission_path,
        name="P13 trigger report")
    if (path in {admission_path} or binding.get("sha256") != _sha256(path)):
        raise P13StateShiftPlanReportError(
            "admission trigger-report file binding mismatch")
    payload = _load(path, "P13 trigger report")
    if payload.get("version") != TRIGGER_REPORT_VERSION:
        raise P13StateShiftPlanReportError("P13 trigger report version mismatch")
    report_digest = _content_digest(
        payload, "report_digest", "P13 trigger report")
    if (report_digest != admission_payload.get("trigger_report_digest") or
            payload.get("campaign_id") != campaign_id or
            payload.get("p13_eligible") is not True or
            payload.get("triggered_count") != len(case_ids)):
        raise P13StateShiftPlanReportError(
            "P13 trigger report does not bind the admitted campaign")
    raw = payload.get("triggers")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise P13StateShiftPlanReportError("P13 triggers are malformed")
    result = {}
    for item in raw:
        try:
            trigger = P12ShadowUpdateTriggerReceipt.from_dict(item)
        except (TypeError, ValueError) as exc:
            raise P13StateShiftPlanReportError(
                f"P13 trigger is invalid: {exc}") from exc
        if (not isinstance(item, Mapping) or
                item.get("receipt_digest") != trigger.receipt_digest or
                trigger.case_id in result or trigger.case_id not in case_ids or
                trigger.campaign_id != campaign_id or trigger.triggered is not True):
            raise P13StateShiftPlanReportError(
                "P13 trigger is not unique, admitted, and replayable")
        result[trigger.case_id] = trigger
    if set(result) != case_ids:
        raise P13StateShiftPlanReportError(
            "P13 triggers do not cover proposal cases")
    return result, path, report_digest


def build_p13_state_shift_plan_report(
    proposal_report: Path | str,
    *,
    output: Path | str,
) -> dict:
    """Build a localized plan while preserving the pre-execution boundary."""
    proposal_path = Path(proposal_report).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    if output_path == proposal_path:
        raise P13StateShiftPlanReportError(
            "plan output must be separate from proposal input")
    proposal_payload = _load(proposal_path, "proposal report")
    if proposal_payload.get("version") != PROPOSAL_REPORT_VERSION:
        raise P13StateShiftPlanReportError("proposal report version mismatch")
    proposal_report_digest = _content_digest(
        proposal_payload, "report_digest", "proposal report")
    if (proposal_payload.get("proposal_eligible") is not True or
            proposal_payload.get("localized_update_plan_present") is not False or
            proposal_payload.get("anti_forgetting_present") is not False or
            proposal_payload.get("shadow_update_attempted") is not False):
        raise P13StateShiftPlanReportError(
            "proposal report is not an eligible pre-plan boundary")
    if (proposal_payload.get("evaluation_only") is not True or
            proposal_payload.get("canonical_memory_mutation") != "none" or
            proposal_payload.get("production_runtime_imported") is not False or
            proposal_payload.get("production_integration") != "not_attempted" or
            proposal_payload.get("memory_docs_submitted") is not False):
        raise P13StateShiftPlanReportError(
            "proposal report crosses an authority boundary")

    proposal = _proposal(proposal_payload)
    campaign_id = proposal_payload.get("campaign_id")
    bindings = proposal_payload.get("case_transition_bindings")
    if (type(campaign_id) is not str or not campaign_id or
            proposal_payload.get("knowledge_parent") != proposal.knowledge_object_id or
            not isinstance(bindings, Mapping) or
            proposal_payload.get("case_count") != len(bindings)):
        raise P13StateShiftPlanReportError(
            "proposal report campaign or case bindings are malformed")
    case_ids = set(bindings)
    bound_transitions = {
        item.get("parent_treatment_transition_id")
        for item in bindings.values() if isinstance(item, Mapping)
    }
    if (len(bound_transitions) != len(case_ids) or
            bound_transitions != set(proposal.transition_ids)):
        raise P13StateShiftPlanReportError(
            "proposal transitions do not match case bindings")

    admission_payload, admission_path = _admission_report(
        proposal_payload, proposal_path)
    if (admission_payload.get("campaign_id") != campaign_id or
            admission_payload.get("case_count") != len(case_ids)):
        raise P13StateShiftPlanReportError(
            "admission report does not match proposal cases")
    admissions = _admissions(admission_payload, case_ids, campaign_id)
    triggers, trigger_path, trigger_report_digest = _triggers(
        admission_payload, admission_path, case_ids, campaign_id)

    ordered_cases = sorted(case_ids)
    primary_trigger = triggers[ordered_cases[0]].receipt_digest
    try:
        plan = state_shift_proposal_to_localized_plan(
            proposal, campaign_id=campaign_id,
            p12_trigger_digest=primary_trigger)
    except (TypeError, ValueError) as exc:
        raise P13StateShiftPlanReportError(
            f"StateShift localized plan was rejected: {exc}") from exc
    required_refs = {
        proposal.proposal_digest, proposal_report_digest,
        admission_payload["report_digest"], trigger_report_digest,
        *(trigger.receipt_digest for trigger in triggers.values()),
        *(receipt.receipt_id for receipt in admissions.values()),
        *(receipt.receipt_digest for receipt in admissions.values()),
    }
    plan = replace(
        plan,
        evidence_refs=tuple(sorted({*plan.evidence_refs, *required_refs})),
    )
    if (plan.operation != proposal.operation or
            plan.update_target != "UPDATE_CAUSAL_KNOWLEDGE" or
            plan.knowledge_refs != (proposal.knowledge_object_id,) or
            not required_refs <= set(plan.evidence_refs)):
        raise P13StateShiftPlanReportError(
            "localized plan does not preserve proposal authority")

    report = {
        "version": REPORT_VERSION,
        "campaign_id": campaign_id,
        "case_count": len(case_ids),
        "knowledge_parent": proposal.knowledge_object_id,
        "proposal_report": {
            "path": str(proposal_path), "sha256": _sha256(proposal_path),
            "report_digest": proposal_report_digest,
        },
        "admission_report": {
            "path": str(admission_path), "sha256": _sha256(admission_path),
            "report_digest": admission_payload["report_digest"],
        },
        "trigger_report": {
            "path": str(trigger_path), "sha256": _sha256(trigger_path),
            "report_digest": trigger_report_digest,
        },
        "trigger_receipt_digests": {
            case_id: triggers[case_id].receipt_digest
            for case_id in ordered_cases
        },
        "admission_receipt_digests": {
            case_id: admissions[case_id].receipt_digest
            for case_id in ordered_cases
        },
        "proposal_state_resolution_ids": list(proposal.state_resolution_ids),
        "proposal_state_context_digests": list(
            proposal.state_context_digests),
        "proposal_transition_ids": list(proposal.transition_ids),
        "localized_update_plan": {
            **plan.to_dict(), "plan_digest": plan.plan_digest,
        },
        "plan_eligible_for_anti_forgetting": True,
        "source_database_present": False,
        "anti_forgetting_present": False,
        "shadow_update_attempted": False,
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "production_integration": "not_attempted",
        "memory_docs_submitted": False,
    }
    report["report_digest"] = _digest(report)
    if output_path.exists():
        raise P13StateShiftPlanReportError(
            f"immutable plan report already exists: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x") as stream:
        stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposal-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_p13_state_shift_plan_report(
            args.proposal_report, output=args.output)
    except (OSError, P13StateShiftPlanReportError,
            TypeError, ValueError) as exc:
        parser.error(str(exc))
    plan = report["localized_update_plan"]
    print(json.dumps({
        "output": str(args.output.expanduser().resolve()),
        "campaign_id": report["campaign_id"],
        "operation": plan["operation"],
        "update_target": plan["update_target"],
        "plan_digest": plan["plan_digest"],
        "report_digest": report["report_digest"],
        "source_database_present": report["source_database_present"],
        "anti_forgetting_present": report["anti_forgetting_present"],
        "canonical_memory_mutation": report["canonical_memory_mutation"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
