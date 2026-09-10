#!/usr/bin/env python3
"""Replay an eligible ORFS StateShift trigger through reason-specific admission.

This command emits audit evidence only.  It never accepts a mutation plan,
opens SQLite, or changes canonical and production authority.
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

from contracts import MemoryRoutingDecision  # noqa: E402
from tehm.evaluation import OrfsPairedCohortReceipt  # noqa: E402
from tehm.evolution import (  # noqa: E402
    EvolutionReasonDerivationReceipt,
    P12ShadowUpdateTriggerReceipt,
    admit_evolution_reason,
    derive_state_shift_reason,
)
from tehm.ids import stable_dumps  # noqa: E402
from tehm.state.shift_receipts import StateShiftReceipt  # noqa: E402


REPORT_VERSION = "p13-state-shift-admission-report-v1"
TYPED_REASON_BUNDLE_VERSION = "p13-typed-reason-bundle-v1"
TRIGGER_REPORT_VERSION = "p13-shadow-trigger-report-v1"
LEARNER_PARTITION_VERSION = "p13-learner-partition-v1"


class P13StateShiftAdmissionReportError(ValueError):
    """The reason-specific admission evidence is malformed or inconsistent."""


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
        raise P13StateShiftAdmissionReportError(
            f"cannot read {name}: {path}") from exc
    if not isinstance(payload, dict):
        raise P13StateShiftAdmissionReportError(f"{name} must be a JSON object")
    return payload


def _replay_digest(payload: Mapping, field: str, name: str) -> str:
    supplied = payload.get(field)
    if type(supplied) is not str or not supplied.startswith("sha256:"):
        raise P13StateShiftAdmissionReportError(f"{name} requires {field}")
    replay = dict(payload)
    replay.pop(field, None)
    if supplied != _digest(replay):
        raise P13StateShiftAdmissionReportError(f"{name} {field} mismatch")
    return supplied


def _audit_cases(payload: Mapping, case_ids: set[str]) -> dict[str, Mapping]:
    if (payload.get("execution_started") is not False or
            payload.get("production_database_writes") is not False or
            payload.get("promotion_attempted") is not False):
        raise P13StateShiftAdmissionReportError(
            "preregistration audit crossed an execution or authority boundary")
    raw = payload.get("cases")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise P13StateShiftAdmissionReportError(
            "preregistration audit cases must be a sequence")
    result: dict[str, Mapping] = {}
    for item in raw:
        if not isinstance(item, Mapping) or type(item.get("case_id")) is not str:
            raise P13StateShiftAdmissionReportError(
                "preregistration audit case requires case_id")
        case_id = item["case_id"].strip()
        if not case_id or case_id in result:
            raise P13StateShiftAdmissionReportError(
                "preregistration audit case IDs must be unique")
        if item.get("admitted_to_state_shift_bucket") is not True:
            raise P13StateShiftAdmissionReportError(
                f"preregistration audit case {case_id} was not admitted")
        result[case_id] = item
    if set(result) != case_ids:
        raise P13StateShiftAdmissionReportError(
            "preregistration audit must cover exactly all cohort cases")
    return result


def _triggers(payload: Mapping, *, cohort_path: Path, route_path: Path,
              partition_path: Path, bundle_path: Path,
              cohort: OrfsPairedCohortReceipt, case_ids: set[str],
              ) -> dict[str, P12ShadowUpdateTriggerReceipt]:
    if payload.get("version") != TRIGGER_REPORT_VERSION:
        raise P13StateShiftAdmissionReportError("P13 trigger report version mismatch")
    _replay_digest(payload, "report_digest", "P13 trigger report")
    if (payload.get("campaign_id") != cohort.campaign_id or
            payload.get("cohort_receipt_digest") != cohort.receipt_digest or
            payload.get("p13_eligible") is not True or
            payload.get("trigger_count") != len(case_ids) or
            payload.get("triggered_count") != len(case_ids)):
        raise P13StateShiftAdmissionReportError(
            "P13 trigger report is not fully eligible for the cohort")

    bindings = (
        (payload.get("cohort_receipt"), payload.get("cohort_receipt_sha256"),
         cohort_path),
        (payload.get("campaign_manifest"), payload.get("campaign_manifest_sha256"),
         partition_path),
        ((payload.get("routing_decisions") or {}).get("path"),
         (payload.get("routing_decisions") or {}).get("sha256"), route_path),
        ((payload.get("evolution_reasons") or {}).get("path"),
         (payload.get("evolution_reasons") or {}).get("sha256"), bundle_path),
    )
    for declared_path, declared_sha, expected_path in bindings:
        if (Path(str(declared_path)).resolve() != expected_path or
                declared_sha != _sha256(expected_path)):
            raise P13StateShiftAdmissionReportError(
                "P13 trigger report input file binding mismatch")
    if (payload.get("canonical_memory_mutation") != "none" or
            payload.get("production_runtime_imported") is not False or
            payload.get("production_integration") != "not_attempted" or
            payload.get("memory_docs_submitted") is not False or
            payload.get("shadow_update_policy") != "isolated_staging_only"):
        raise P13StateShiftAdmissionReportError(
            "P13 trigger report crosses an authority boundary")
    raw = payload.get("triggers")
    if (not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or
            len(raw) != len(case_ids)):
        raise P13StateShiftAdmissionReportError(
            "P13 trigger report does not cover all cohort cases")
    result = {}
    for item in raw:
        try:
            trigger = P12ShadowUpdateTriggerReceipt.from_dict(item)
        except (TypeError, ValueError) as exc:
            raise P13StateShiftAdmissionReportError(
                f"P13 trigger entry is invalid: {exc}") from exc
        if (item.get("receipt_digest") != trigger.receipt_digest or
                trigger.triggered is not True or trigger.case_id in result):
            raise P13StateShiftAdmissionReportError(
                "P13 trigger entry is not replayable and unique")
        result[trigger.case_id] = trigger
    if set(result) != case_ids:
        raise P13StateShiftAdmissionReportError(
            "P13 trigger cases do not match cohort")
    return result

def _typed_bundle(payload: Mapping, *, cohort_path: Path, audit_path: Path,
                  cohort: OrfsPairedCohortReceipt, case_ids: set[str],
                  ) -> dict[str, EvolutionReasonDerivationReceipt]:
    if payload.get("version") != TYPED_REASON_BUNDLE_VERSION:
        raise P13StateShiftAdmissionReportError("typed reason bundle version mismatch")
    _replay_digest(payload, "bundle_digest", "typed reason bundle")
    if (payload.get("campaign_id") != cohort.campaign_id or
            payload.get("cohort_receipt_digest") != cohort.receipt_digest):
        raise P13StateShiftAdmissionReportError(
            "typed reason bundle does not bind the cohort")
    if (Path(str(payload.get("cohort_receipt"))).resolve() != cohort_path or
            payload.get("cohort_receipt_sha256") != _sha256(cohort_path) or
            Path(str(payload.get("preregistration_audit"))).resolve() != audit_path or
            payload.get("preregistration_audit_sha256") != _sha256(audit_path)):
        raise P13StateShiftAdmissionReportError(
            "typed reason bundle input file binding mismatch")
    if (payload.get("evaluation_only") is not True or
            payload.get("canonical_memory_mutation") != "none" or
            payload.get("production_runtime_imported") is not False or
            payload.get("production_integration") != "not_attempted" or
            payload.get("memory_docs_submitted") is not False):
        raise P13StateShiftAdmissionReportError(
            "typed reason bundle crosses an authority boundary")
    raw = payload.get("derivation_receipts")
    if not isinstance(raw, Mapping) or set(raw) != case_ids:
        raise P13StateShiftAdmissionReportError(
            "typed reason derivations must cover exactly all cohort cases")
    result = {}
    for case_id in sorted(case_ids):
        entries = raw[case_id]
        if (not isinstance(entries, Sequence) or isinstance(entries, (str, bytes))
                or len(entries) != 1):
            raise P13StateShiftAdmissionReportError(
                f"typed reason bundle requires one derivation for {case_id}")
        try:
            derivation = EvolutionReasonDerivationReceipt.from_dict(entries[0])
        except (TypeError, ValueError) as exc:
            raise P13StateShiftAdmissionReportError(
                f"typed reason derivation for {case_id} is invalid: {exc}") from exc
        if derivation.reason != "STATE_SHIFT":
            raise P13StateShiftAdmissionReportError(
                f"typed reason for {case_id} is not STATE_SHIFT")
        result[case_id] = derivation
    return result


def _routes(payload: Mapping, case_ids: set[str], campaign_id: str,
            ) -> dict[str, MemoryRoutingDecision]:
    if payload.get("campaign_id") not in {None, campaign_id}:
        raise P13StateShiftAdmissionReportError(
            "routing decisions campaign_id does not match cohort")
    raw = payload.get("routes", payload.get("routing_decisions", payload))
    if not isinstance(raw, Mapping) or set(raw) != case_ids:
        raise P13StateShiftAdmissionReportError(
            "routing decisions must cover exactly all cohort cases")
    result = {}
    for case_id in sorted(case_ids):
        try:
            route = MemoryRoutingDecision.from_dict(raw[case_id])
        except (TypeError, ValueError) as exc:
            raise P13StateShiftAdmissionReportError(
                f"routing decision for {case_id} is invalid: {exc}") from exc
        if (raw[case_id].get("decision_digest") is not None and
                raw[case_id]["decision_digest"] != route.decision_digest):
            raise P13StateShiftAdmissionReportError(
                f"routing decision digest mismatch for {case_id}")
        result[case_id] = route
    return result


def _learner_partition(payload: Mapping, case_ids: set[str], campaign_id: str,
                       ) -> dict[str, bool]:
    if payload.get("version") != LEARNER_PARTITION_VERSION:
        raise P13StateShiftAdmissionReportError("learner partition version mismatch")
    if payload.get("campaign_id") != campaign_id:
        raise P13StateShiftAdmissionReportError(
            "learner partition campaign_id does not match cohort")
    if (payload.get("evaluation_only") is not True or
            payload.get("canonical_memory_mutation") != "none" or
            payload.get("production_runtime_imported") is not False or
            payload.get("memory_docs_submitted") is not False):
        raise P13StateShiftAdmissionReportError(
            "learner partition crosses an evaluation boundary")
    campaign_eligible = payload.get("learner_eligible")
    if type(campaign_eligible) is not bool:
        raise P13StateShiftAdmissionReportError(
            "learner partition learner_eligible must be boolean")
    raw = payload.get("cases")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise P13StateShiftAdmissionReportError(
            "learner partition cases must be a sequence")
    result = {}
    for item in raw:
        if not isinstance(item, Mapping) or type(item.get("case_id")) is not str:
            raise P13StateShiftAdmissionReportError(
                "learner partition case requires case_id")
        case_id = item["case_id"].strip()
        if not case_id or case_id in result:
            raise P13StateShiftAdmissionReportError(
                "learner partition case IDs must be unique")
        eligible = item.get("learner_eligible")
        if type(eligible) is not bool:
            raise P13StateShiftAdmissionReportError(
                f"learner eligibility for {case_id} must be boolean")
        if eligible and (item.get("dataset_split") != "training" or
                         item.get("role") != "training"):
            raise P13StateShiftAdmissionReportError(
                f"learner-eligible case {case_id} must be training")
        result[case_id] = bool(campaign_eligible and eligible)
    if set(result) != case_ids:
        raise P13StateShiftAdmissionReportError(
            "learner partition must cover exactly all cohort cases")
    return result


def build_p13_state_shift_admission_report(
        cohort: Path | str, preregistration_audit: Path | str,
        typed_reason_bundle: Path | str, routing_decisions: Path | str,
        learner_partition: Path | str, trigger_report: Path | str, *,
        output: Path | str, memory_arm: str = "ALWAYS_MEMORY") -> dict:
    """Replay every reason-specific STATE_SHIFT admission gate."""
    paths = [Path(value).expanduser().resolve() for value in (
        cohort, preregistration_audit, typed_reason_bundle, routing_decisions,
        learner_partition, trigger_report, output)]
    (cohort_path, audit_path, bundle_path, route_path, partition_path,
     trigger_path, output_path) = paths
    if len(set(paths)) != len(paths):
        raise P13StateShiftAdmissionReportError(
            "admission output and all input files must be separate")
    cohort_payload = _load(cohort_path, "ORFS cohort")
    try:
        cohort_receipt = OrfsPairedCohortReceipt.from_dict(cohort_payload)
    except (TypeError, ValueError) as exc:
        raise P13StateShiftAdmissionReportError(
            f"ORFS cohort is invalid: {exc}") from exc
    supplied_cohort_digests = {
        cohort_payload[name] for name in ("cohort_receipt_digest", "receipt_digest")
        if name in cohort_payload
    }
    if (not supplied_cohort_digests or
            supplied_cohort_digests != {cohort_receipt.receipt_digest}):
        raise P13StateShiftAdmissionReportError("ORFS cohort digest mismatch")
    case_ids = set(cohort_receipt.case_receipts)
    audit_payload = _load(audit_path, "preregistration audit")
    audit_digest = _replay_digest(
        audit_payload, "audit_digest", "preregistration audit")
    audit_cases = _audit_cases(audit_payload, case_ids)
    route_payload = _load(route_path, "routing decisions")
    routes = _routes(route_payload, case_ids, cohort_receipt.campaign_id)
    partition_payload = _load(partition_path, "learner partition")
    learner = _learner_partition(
        partition_payload, case_ids, cohort_receipt.campaign_id)
    bundle_payload = _load(bundle_path, "typed reason bundle")
    derivations = _typed_bundle(
        bundle_payload, cohort_path=cohort_path, audit_path=audit_path,
        cohort=cohort_receipt, case_ids=case_ids)
    if bundle_payload.get("preregistration_audit_digest") != audit_digest:
        raise P13StateShiftAdmissionReportError(
            "typed reason bundle audit digest mismatch")
    trigger_payload = _load(trigger_path, "P13 trigger report")
    triggers = _triggers(
        trigger_payload, cohort_path=cohort_path, route_path=route_path,
        partition_path=partition_path, bundle_path=bundle_path,
        cohort=cohort_receipt, case_ids=case_ids)

    admissions = {}
    replayed = {}
    for case_id in sorted(case_ids):
        audit_case = audit_cases[case_id]
        try:
            shift = StateShiftReceipt.from_dict(audit_case.get("state_shift"))
            audit_route = MemoryRoutingDecision.from_dict(audit_case.get("routing"))
        except (TypeError, ValueError) as exc:
            raise P13StateShiftAdmissionReportError(
                f"preregistered evidence for {case_id} is invalid: {exc}") from exc
        route = routes[case_id]
        if (route.routing_receipt_id != audit_route.routing_receipt_id or
                route.decision_digest != audit_route.decision_digest or
                route.state_shift_receipt_id != shift.receipt_id):
            raise P13StateShiftAdmissionReportError(
                f"preregistered route binding mismatch for {case_id}")
        paired = cohort_receipt.case_receipts[case_id]
        if (paired.routing_receipt_id != route.routing_receipt_id or
                paired.routing_decision != route.decision or
                paired.no_skill_reason != route.no_skill_reason or
                paired.state_shift_receipt_id != shift.receipt_id):
            raise P13StateShiftAdmissionReportError(
                f"cohort route binding mismatch for {case_id}")
        derived = derive_state_shift_reason(
            shift, campaign_id=cohort_receipt.campaign_id, case_id=case_id,
            routing=route, lineage_id=paired.lineage_id)
        if (derived is None or
                derived.receipt_digest != derivations[case_id].receipt_digest):
            raise P13StateShiftAdmissionReportError(
                f"typed detector replay mismatch for {case_id}")
        trigger = triggers[case_id]
        if (trigger.campaign_id != cohort_receipt.campaign_id or
                trigger.routing_receipt_id != route.routing_receipt_id or
                trigger.state_shift_receipt_id != shift.receipt_id or
                "STATE_SHIFT" not in trigger.evolution_reasons or
                trigger.learner_eligible != learner[case_id] or
                trigger.memory_arm != memory_arm):
            raise P13StateShiftAdmissionReportError(
                f"P13 trigger semantic binding mismatch for {case_id}")
        admission = admit_evolution_reason(
            derived, campaign_id=cohort_receipt.campaign_id,
            learner_eligible=learner[case_id], paired=paired,
            state_shift=shift, routing=route, memory_arm=memory_arm)
        admissions[case_id] = admission
        replayed[case_id] = {
            "state_shift_receipt_id": shift.receipt_id,
            "routing_receipt_id": route.routing_receipt_id,
            "derivation_receipt_id": derived.receipt_id,
            "derivation_receipt_digest": derived.receipt_digest,
            "trigger_receipt_digest": trigger.receipt_digest,
        }

    admitted_count = sum(item.admitted for item in admissions.values())
    eligible = admitted_count == len(case_ids)
    input_paths = {
        "cohort": cohort_path,
        "preregistration_audit": audit_path,
        "typed_reason_bundle": bundle_path,
        "routing_decisions": route_path,
        "learner_partition": partition_path,
        "trigger_report": trigger_path,
    }
    report = {
        "version": REPORT_VERSION,
        "campaign_id": cohort_receipt.campaign_id,
        "cohort_receipt_digest": cohort_receipt.receipt_digest,
        "preregistration_audit_digest": audit_digest,
        "typed_reason_bundle_digest": bundle_payload["bundle_digest"],
        "trigger_report_digest": trigger_payload["report_digest"],
        "memory_arm": memory_arm,
        "case_count": len(case_ids),
        "admitted_count": admitted_count,
        "p13_admission_eligible": eligible,
        "blocked_reasons": sorted({
            item.blocked_reason for item in admissions.values()
            if item.blocked_reason is not None
        }),
        "replayed_evidence": replayed,
        "admissions": {
            case_id: {
                **item.to_dict(), "receipt_id": item.receipt_id,
                "receipt_digest": item.receipt_digest,
            }
            for case_id, item in sorted(admissions.items())
        },
        "inputs": {
            name: {"path": str(path), "sha256": _sha256(path)}
            for name, path in input_paths.items()
        },
        "admission_policy": "reason_specific_state_shift",
        "mutation_plan_present": False,
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
        raise P13StateShiftAdmissionReportError(
            f"immutable admission report already exists: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x") as stream:
        stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--preregistration-audit", type=Path, required=True)
    parser.add_argument("--typed-reason-bundle", type=Path, required=True)
    parser.add_argument("--routing-decisions", type=Path, required=True)
    parser.add_argument("--learner-partition", type=Path, required=True)
    parser.add_argument("--trigger-report", type=Path, required=True)
    parser.add_argument("--memory-arm", default="ALWAYS_MEMORY")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_p13_state_shift_admission_report(
            args.cohort, args.preregistration_audit,
            args.typed_reason_bundle, args.routing_decisions,
            args.learner_partition, args.trigger_report,
            output=args.output, memory_arm=args.memory_arm)
    except (OSError, P13StateShiftAdmissionReportError,
            TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "output": str(args.output.expanduser().resolve()),
        "campaign_id": report["campaign_id"],
        "admitted_count": report["admitted_count"],
        "case_count": report["case_count"],
        "p13_admission_eligible": report["p13_admission_eligible"],
        "report_digest": report["report_digest"],
        "canonical_memory_mutation": report["canonical_memory_mutation"],
    }, indent=2, sort_keys=True))
    return 0 if report["p13_admission_eligible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
