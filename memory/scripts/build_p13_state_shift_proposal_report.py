#!/usr/bin/env python3
"""Build a repeated-StateShift proposal from an admitted ORFS cohort.

The admission report is the input index.  This command rehashes every bound
file, replays typed cohort/shift/trigger/admission receipts, and maps each case
to the parent Knowledge treatment transition recorded before execution.  It
does not create a LocalizedUpdatePlan or open a memory database.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.evaluation import OrfsPairedCohortReceipt  # noqa: E402
from tehm.evolution import (  # noqa: E402
    EvolutionAdmissionReceipt,
    P12ShadowUpdateTriggerReceipt,
    propose_repeated_state_shift_from_paired_receipts,
)
from tehm.ids import stable_dumps  # noqa: E402
from tehm.state.shift_receipts import StateShiftReceipt  # noqa: E402


REPORT_VERSION = "p13-state-shift-proposal-report-v1"
ADMISSION_REPORT_VERSION = "p13-state-shift-admission-report-v1"


class P13StateShiftProposalReportError(ValueError):
    """The admitted evidence cannot safely produce a StateShift proposal."""


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
        raise P13StateShiftProposalReportError(
            f"cannot read {name}: {path}") from exc
    if not isinstance(payload, dict):
        raise P13StateShiftProposalReportError(f"{name} must be a JSON object")
    return payload


def _content_digest(payload: Mapping, field: str, name: str) -> str:
    supplied = payload.get(field)
    if type(supplied) is not str or not supplied.startswith("sha256:"):
        raise P13StateShiftProposalReportError(f"{name} requires {field}")
    replay = dict(payload)
    replay.pop(field, None)
    if supplied != _digest(replay):
        raise P13StateShiftProposalReportError(f"{name} {field} mismatch")
    return supplied


def _admission_inputs(payload: Mapping, admission_path: Path) -> dict[str, Path]:
    if payload.get("version") != ADMISSION_REPORT_VERSION:
        raise P13StateShiftProposalReportError("admission report version mismatch")
    _content_digest(payload, "report_digest", "admission report")
    if (payload.get("p13_admission_eligible") is not True or
            payload.get("admitted_count") != payload.get("case_count") or
            payload.get("mutation_plan_present") is not False or
            payload.get("anti_forgetting_present") is not False or
            payload.get("shadow_update_attempted") is not False):
        raise P13StateShiftProposalReportError(
            "admission report is not an eligible pre-proposal boundary")
    if (payload.get("evaluation_only") is not True or
            payload.get("canonical_memory_mutation") != "none" or
            payload.get("production_runtime_imported") is not False or
            payload.get("production_integration") != "not_attempted" or
            payload.get("memory_docs_submitted") is not False):
        raise P13StateShiftProposalReportError(
            "admission report crosses an authority boundary")
    required = {
        "cohort", "preregistration_audit", "typed_reason_bundle",
        "routing_decisions", "learner_partition", "trigger_report",
    }
    raw = payload.get("inputs")
    if not isinstance(raw, Mapping) or not required <= set(raw):
        raise P13StateShiftProposalReportError(
            "admission report input index is incomplete")
    result = {}
    for name in sorted(required):
        item = raw[name]
        if not isinstance(item, Mapping) or type(item.get("path")) is not str:
            raise P13StateShiftProposalReportError(
                f"admission input {name} is malformed")
        path = Path(item["path"]).expanduser()
        if not path.is_absolute():
            path = admission_path.parent / path
        path = path.resolve()
        if path == admission_path or not path.is_file():
            raise P13StateShiftProposalReportError(
                f"admission input {name} is not an independent file")
        if item.get("sha256") != _sha256(path):
            raise P13StateShiftProposalReportError(
                f"admission input {name} digest mismatch")
        result[name] = path
    return result


def _parent_transition_map(payload: Mapping, case_ids: set[str]):
    _content_digest(payload, "audit_digest", "preregistration audit")
    parent = payload.get("parent_replay")
    if not isinstance(parent, Mapping):
        raise P13StateShiftProposalReportError(
            "preregistration audit lacks parent replay")
    knowledge = parent.get("knowledge")
    pairs = parent.get("pairs")
    if not isinstance(knowledge, Mapping) or not isinstance(pairs, Sequence):
        raise P13StateShiftProposalReportError(
            "preregistration parent replay is malformed")
    knowledge_id = knowledge.get("knowledge_id")
    version = knowledge.get("version")
    if type(knowledge_id) is not str or type(version) is not int:
        raise P13StateShiftProposalReportError(
            "preregistration Knowledge identity is malformed")
    parent_object_id = f"{knowledge_id}@{version}"
    by_design = {}
    for pair in pairs:
        if not isinstance(pair, Mapping):
            raise P13StateShiftProposalReportError(
                "preregistration intervention pair is malformed")
        lineage = pair.get("lineage_id")
        treatment = pair.get("treatment_transition_id")
        if type(lineage) is not str or type(treatment) is not str:
            raise P13StateShiftProposalReportError(
                "preregistration intervention pair identity is malformed")
        design = lineage.rsplit(":", 1)[-1]
        if not design or design in by_design:
            raise P13StateShiftProposalReportError(
                "parent treatment mapping is ambiguous")
        treatment_outcome = (pair.get("outcome_delta") or {}).get("treatment") or {}
        if (pair.get("validity_status") != "VALID_CONTROLLED_PAIR" or
                pair.get("evidence_level") != "L2_CONTROLLED_INTERVENTION" or
                treatment_outcome.get("outcome") != "PASS" or
                treatment_outcome.get("verdict") != "PASS"):
            raise P13StateShiftProposalReportError(
                "parent treatment is not verified learner evidence")
        by_design[design] = treatment

    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, Sequence):
        raise P13StateShiftProposalReportError(
            "preregistration audit cases are malformed")
    audit_cases = {item.get("case_id"): item for item in raw_cases
                   if isinstance(item, Mapping)}
    if set(audit_cases) != case_ids:
        raise P13StateShiftProposalReportError(
            "preregistration audit cases do not match cohort")
    shifts = {}
    transitions = {}
    for case_id in sorted(case_ids):
        item = audit_cases[case_id]
        design = ((item.get("flow_config_observation") or {}).get("values") or {}).get(
            "DESIGN_NAME")
        if type(design) is not str or design not in by_design:
            raise P13StateShiftProposalReportError(
                f"case {case_id} cannot bind a parent treatment transition")
        try:
            shift = StateShiftReceipt.from_dict(item.get("state_shift"))
        except (TypeError, ValueError) as exc:
            raise P13StateShiftProposalReportError(
                f"case {case_id} StateShift receipt is invalid: {exc}") from exc
        if shift.knowledge_object_id != parent_object_id:
            raise P13StateShiftProposalReportError(
                f"case {case_id} does not share the parent Knowledge")
        shifts[case_id] = shift
        transitions[case_id] = by_design[design]
    if len(set(transitions.values())) != len(case_ids):
        raise P13StateShiftProposalReportError(
            "shifted cases do not map to distinct parent transitions")
    return parent_object_id, shifts, transitions


def build_p13_state_shift_proposal_report(
        admission_report: Path | str, *, output: Path | str) -> dict:
    """Build a typed repeated-StateShift proposal without a mutation plan."""
    admission_path = Path(admission_report).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    if output_path == admission_path:
        raise P13StateShiftProposalReportError(
            "proposal output must be separate from admission input")
    admission_payload = _load(admission_path, "admission report")
    inputs = _admission_inputs(admission_payload, admission_path)
    if output_path in set(inputs.values()):
        raise P13StateShiftProposalReportError(
            "proposal output must be separate from bound evidence")

    cohort_payload = _load(inputs["cohort"], "ORFS cohort")
    try:
        cohort = OrfsPairedCohortReceipt.from_dict(cohort_payload)
    except (TypeError, ValueError) as exc:
        raise P13StateShiftProposalReportError(
            f"ORFS cohort is invalid: {exc}") from exc
    case_ids = set(cohort.case_receipts)
    if (admission_payload.get("campaign_id") != cohort.campaign_id or
            admission_payload.get("cohort_receipt_digest") != cohort.receipt_digest or
            admission_payload.get("case_count") != len(case_ids)):
        raise P13StateShiftProposalReportError(
            "admission report does not bind the ORFS cohort")
    audit_payload = _load(
        inputs["preregistration_audit"], "preregistration audit")
    parent_object_id, shifts, transitions = _parent_transition_map(
        audit_payload, case_ids)
    raw_admissions = admission_payload.get("admissions")
    if not isinstance(raw_admissions, Mapping) or set(raw_admissions) != case_ids:
        raise P13StateShiftProposalReportError(
            "admission receipts must cover exactly all cohort cases")
    admissions = {}
    for case_id in sorted(case_ids):
        try:
            receipt = EvolutionAdmissionReceipt.from_dict(raw_admissions[case_id])
        except (TypeError, ValueError) as exc:
            raise P13StateShiftProposalReportError(
                f"admission receipt for {case_id} is invalid: {exc}") from exc
        if (receipt.case_id != case_id or
                receipt.campaign_id != cohort.campaign_id or
                receipt.reason != "STATE_SHIFT" or receipt.admitted is not True):
            raise P13StateShiftProposalReportError(
                f"admission receipt for {case_id} is not eligible")
        admissions[case_id] = receipt

    trigger_payload = _load(inputs["trigger_report"], "P13 trigger report")
    raw_triggers = trigger_payload.get("triggers")
    if not isinstance(raw_triggers, Sequence):
        raise P13StateShiftProposalReportError("P13 triggers are malformed")
    triggers = {}
    for item in raw_triggers:
        try:
            trigger = P12ShadowUpdateTriggerReceipt.from_dict(item)
        except (TypeError, ValueError) as exc:
            raise P13StateShiftProposalReportError(
                f"P13 trigger is invalid: {exc}") from exc
        if (trigger.case_id in triggers or trigger.case_id not in case_ids or
                trigger.campaign_id != cohort.campaign_id or
                trigger.triggered is not True or
                item.get("receipt_digest") != trigger.receipt_digest):
            raise P13StateShiftProposalReportError(
                "P13 trigger is not unique, eligible, and cohort-bound")
        triggers[trigger.case_id] = trigger
    if set(triggers) != case_ids:
        raise P13StateShiftProposalReportError(
            "P13 triggers must cover exactly all cohort cases")
    replayed = admission_payload.get("replayed_evidence")
    if not isinstance(replayed, Mapping) or set(replayed) != case_ids:
        raise P13StateShiftProposalReportError(
            "admission replay evidence does not cover the cohort")
    for case_id in sorted(case_ids):
        item = replayed[case_id]
        if (not isinstance(item, Mapping) or
                item.get("state_shift_receipt_id") != shifts[case_id].receipt_id or
                item.get("trigger_receipt_digest") != triggers[case_id].receipt_digest or
                admissions[case_id].receipt_id not in {
                    raw_admissions[case_id].get("receipt_id")
                }):
            raise P13StateShiftProposalReportError(
                f"admission replay evidence mismatch for {case_id}")

    ordered = sorted(case_ids)
    evidence_refs = {
        admission_payload["report_digest"], cohort.receipt_digest,
        audit_payload["audit_digest"],
        admission_payload["typed_reason_bundle_digest"],
        admission_payload["trigger_report_digest"],
    }
    observations = []
    for case_id in ordered:
        paired = cohort.case_receipts[case_id]
        trigger = triggers[case_id]
        admission = admissions[case_id]
        observations.append((shifts[case_id], paired))
        evidence_refs.update({
            shifts[case_id].receipt_id,
            paired.receipt_digest,
            paired.routing_receipt_id,
            paired.arm_receipts["NO_MEMORY"].execution_digest,
            paired.arm_receipts["ALWAYS_MEMORY"].execution_digest,
            trigger.receipt_digest,
            admission.receipt_id,
            admission.receipt_digest,
            transitions[case_id],
        })
    try:
        proposal = propose_repeated_state_shift_from_paired_receipts(
            observations, knowledge_object_id=parent_object_id,
            transition_ids=[transitions[case_id] for case_id in ordered],
            evidence_refs=tuple(sorted(evidence_refs)), learner_eligible=True,
            min_repeats=2, historical_memory_arm="ALWAYS_MEMORY")
    except (TypeError, ValueError) as exc:
        raise P13StateShiftProposalReportError(
            f"repeated StateShift proposal was rejected: {exc}") from exc

    report = {
        "version": REPORT_VERSION,
        "campaign_id": cohort.campaign_id,
        "knowledge_parent": parent_object_id,
        "case_count": len(case_ids),
        "lineage_count": cohort.lineage_count,
        "case_transition_bindings": {
            case_id: {
                "lineage_id": cohort.case_receipts[case_id].lineage_id,
                "parent_treatment_transition_id": transitions[case_id],
                "state_shift_receipt_id": shifts[case_id].receipt_id,
                "admission_receipt_id": admissions[case_id].receipt_id,
                "trigger_receipt_digest": triggers[case_id].receipt_digest,
                "no_memory_outcome": cohort.case_receipts[case_id].arm_receipts[
                    "NO_MEMORY"].outcome,
                "historical_memory_outcome": cohort.case_receipts[
                    case_id].arm_receipts["ALWAYS_MEMORY"].outcome,
            }
            for case_id in ordered
        },
        "proposal": {
            **proposal.to_dict(), "proposal_id": proposal.proposal_id,
            "proposal_digest": proposal.proposal_digest,
        },
        "proposal_eligible": proposal.operation != "RETAIN",
        "admission_report": {
            "path": str(admission_path), "sha256": _sha256(admission_path),
            "report_digest": admission_payload["report_digest"],
        },
        "localized_update_plan_present": False,
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
        raise P13StateShiftProposalReportError(
            f"immutable proposal report already exists: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x") as stream:
        stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admission-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_p13_state_shift_proposal_report(
            args.admission_report, output=args.output)
    except (OSError, P13StateShiftProposalReportError,
            TypeError, ValueError) as exc:
        parser.error(str(exc))
    proposal = report["proposal"]
    print(json.dumps({
        "output": str(args.output.expanduser().resolve()),
        "campaign_id": report["campaign_id"],
        "operation": proposal["operation"],
        "evolution_reason": proposal["evolution_reason"],
        "proposal_digest": proposal["proposal_digest"],
        "report_digest": report["report_digest"],
        "canonical_memory_mutation": report["canonical_memory_mutation"],
    }, indent=2, sort_keys=True))
    return 0 if report["proposal_eligible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
