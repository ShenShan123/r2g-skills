#!/usr/bin/env python3
"""Derive interference from a frozen, executed source-bound ORFS cohort.

No labels, replacement Knowledge, or mutation plans are accepted. A complete
cohort with no harm is a negative control, not a reason to revise memory. Mixed
cohorts are reported without silently dropping cases to obtain admission.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_p13_shadow_trigger_report import _digest, _load_json, _routes, _sha256
from tehm.evaluation import OrfsPairedCohortReceipt
from tehm.evolution.admission import admit_evolution_reason
from tehm.evolution.reason_derivation import (
    derive_memory_interference_reason, p13_reason_receipt_from_derivations,
)
from tehm.retrieval.structured_candidate import StructuredRepairCandidate


class InterferenceReasonBundleError(ValueError):
    """Executed evidence does not match its prospectively frozen inputs."""


def _reference(ref: Mapping, name: str) -> tuple[Path, dict]:
    if not isinstance(ref, Mapping) or not isinstance(ref.get("path"), str):
        raise InterferenceReasonBundleError(f"{name} reference is missing")
    path = Path(ref["path"]).expanduser().resolve()
    if _sha256(path) != ref.get("sha256"):
        raise InterferenceReasonBundleError(f"{name} file digest mismatch")
    return path, _load_json(path, name)


def build_interference_reason_bundle(cohort: Path | str, manifest: Path | str,
                                    *, output: Path | str) -> dict:
    cohort_path, manifest_path, output_path = (
        Path(value).expanduser().resolve() for value in (cohort, manifest, output))
    raw = _load_json(cohort_path, "executed cohort")
    receipt = OrfsPairedCohortReceipt.from_dict(raw)
    frozen = _load_json(manifest_path, "P12 manifest")
    if (receipt.campaign_id != frozen.get("campaign_id") or
            receipt.campaign_manifest_digest != _digest(frozen)):
        raise InterferenceReasonBundleError("cohort manifest binding mismatch")
    authority_path, authority = _reference(frozen.get("input_authority"),
                                            "input authority")
    unsigned = dict(authority)
    supplied = unsigned.pop("authority_digest", None)
    if (supplied != _digest(unsigned) or supplied !=
            frozen["input_authority"].get("authority_digest") or
            authority.get("campaign_id") != receipt.campaign_id):
        raise InterferenceReasonBundleError("input authority content binding mismatch")
    if (authority.get("version") != "r3-8-source-bound-orfs-input-authority-v1" or
            authority.get("evaluation_only") is not True or
            authority.get("canonical_memory_mutation") != "none" or
            authority.get("production_runtime_imported") is not False or
            authority.get("eda_executed") is not False or
            any(authority.get(field) is not True for field in (
                "actual_router_used", "actual_selector_used",
                "actual_runtime_binding_used", "actual_candidate_builder_used"))):
        raise InterferenceReasonBundleError("input authority boundary mismatch")
    route_path, route_payload = _reference(authority.get("routing_decisions"),
                                           "routing decisions")
    cases = {case["case_id"]: case for case in frozen["cases"]}
    case_ids = set(receipt.case_receipts)
    routes = _routes(route_payload, case_ids)
    if (len(cases) != len(frozen["cases"]) or set(cases) != case_ids or
            set(routes) != case_ids or set(authority.get("cases", {})) != case_ids or
            set(authority.get("candidate_freeze", {})) != case_ids):
        raise InterferenceReasonBundleError("case coverage mismatch")
    forbidden = {cohort_path, manifest_path, authority_path, route_path}
    derivations, errors, routing_bindings = {}, {}, {}
    for case_id in sorted(case_ids):
        paired = receipt.case_receipts[case_id]
        case = cases[case_id]
        if (paired.lineage_id != case.get("lineage_id") or
                receipt.source_digests[case_id] != case.get("source_digest") or
                receipt.source_digests[case_id] !=
                authority["cases"][case_id].get("source_digest")):
            raise InterferenceReasonBundleError(f"{case_id} source/lineage mismatch")
        route = routes[case_id]
        if (paired.routing_receipt_id != route.routing_receipt_id or
                paired.routing_decision != route.decision or
                paired.no_skill_reason != route.no_skill_reason or
                authority["cases"][case_id].get("route") != {
                    **route.to_dict(), "decision_digest": route.decision_digest}):
            raise InterferenceReasonBundleError(f"{case_id} routing binding mismatch")
        candidate_path, candidate_raw = _reference(
            authority["candidate_freeze"][case_id], f"{case_id} candidate")
        forbidden.add(candidate_path)
        candidate = StructuredRepairCandidate.from_dict(candidate_raw)
        forced = paired.arm_receipts["ALWAYS_MEMORY"]
        frozen_path = Path(case.get("candidate_paths", {}).get(
            "ALWAYS_MEMORY", "")).expanduser()
        if not frozen_path.is_absolute():
            frozen_path = manifest_path.parent / frozen_path
        if (frozen_path.resolve() != candidate_path or
                forced.candidate_digest != candidate.candidate_digest or
                forced.candidate_id != candidate.candidate_id or
                candidate.candidate_digest !=
                authority["candidate_freeze"][case_id].get("candidate_digest")):
            raise InterferenceReasonBundleError(f"{case_id} forced candidate mismatch")
        routing_bindings[case_id] = {
            "routing_receipt_id": route.routing_receipt_id,
            "decision_digest": route.decision_digest,
        }
        try:
            reason = derive_memory_interference_reason(
                paired, campaign_id=receipt.campaign_id)
        except ValueError as exc:
            errors[case_id] = str(exc)
            continue
        if reason is not None:
            derivations[case_id] = (reason,)
    if output_path in forbidden or output_path.exists():
        raise InterferenceReasonBundleError("output must be new and separate from evidence")
    complete = set(derivations) == case_ids and not errors
    partition = authority.get("learner_partition")
    partition_bound = isinstance(partition, Mapping)
    if partition_bound:
        if (type(partition.get("learner_eligible")) is not bool or
                partition["learner_eligible"] != frozen.get("learner_eligible") or
                set(partition.get("cases", {})) != case_ids or
                any(partition["cases"][cid] != {
                    key: cases[cid].get(key) for key in (
                        "dataset_split", "role", "learner_eligible")}
                    for cid in case_ids)):
            raise InterferenceReasonBundleError("prospective learner partition mismatch")
    admissions = {
        cid: admit_evolution_reason(
            items[0], campaign_id=receipt.campaign_id, paired=receipt.case_receipts[cid],
            learner_eligible=(partition_bound and
                              partition["learner_eligible"] is True and
                              partition["cases"][cid].get("learner_eligible") is True and
                              partition["cases"][cid].get("dataset_split") == "training" and
                              partition["cases"][cid].get("role") == "training"))
        for cid, items in derivations.items()}
    payload = {
        "version": ("p13-typed-reason-bundle-v1" if complete else
                    "p13-interference-detector-report-v1"),
        "status": ("ALL_CASES_DERIVED" if complete else
                   "DETECTOR_REJECTED" if errors else
                   "MIXED_SIGNALS" if derivations else "NO_EVOLUTION_SIGNAL"),
        "campaign_id": receipt.campaign_id,
        "cohort_receipt_digest": receipt.receipt_digest,
        "cohort_receipt": str(cohort_path),
        "cohort_receipt_sha256": _sha256(cohort_path),
        "manifest": str(manifest_path), "manifest_sha256": _sha256(manifest_path),
        "input_authority": dict(frozen["input_authority"]),
        # The manifest is the prospective authority reference. Retain the
        # runner annotation as well: old v2 runners accidentally returned the
        # last oracle file path here, although preflight checked the authority.
        # Do not rewrite the original execution artifact to hide that defect.
        "runner_input_authority_ref": raw.get("input_authority_ref"),
        "runner_input_authority_ref_matches_manifest": (
            raw.get("input_authority_ref") == frozen["input_authority"]),
        "memory_arm": "ALWAYS_MEMORY",
        "case_count": len(case_ids), "derived_count": len(derivations),
        "no_signal_cases": sorted(case_ids - set(derivations) - set(errors)),
        "derivation_errors": errors,
        "prospective_learner_partition_bound": partition_bound,
        "shadow_mutation_eligible": (complete and partition_bound and
                                      all(item.admitted for item in admissions.values())),
        "admission_receipts": {
            cid: {**item.to_dict(), "receipt_id": item.receipt_id,
                  "receipt_digest": item.receipt_digest}
            for cid, item in sorted(admissions.items())},
        "derivation_receipts": {
            case_id: [{**item.to_dict(), "receipt_id": item.receipt_id,
                       "receipt_digest": item.receipt_digest} for item in items]
            for case_id, items in sorted(derivations.items())},
        "routing_bindings": routing_bindings,
        "evaluation_only": True, "canonical_memory_mutation": "none",
        "production_runtime_imported": False, "memory_docs_submitted": False,
        "mutation_authority_granted": False,
    }
    if complete:
        reason = p13_reason_receipt_from_derivations(
            derivations, campaign_id=receipt.campaign_id,
            cohort_receipt_digest=receipt.receipt_digest)
        payload["reason_receipt"] = {
            **reason.to_dict(), "receipt_id": reason.receipt_id,
            "receipt_digest": reason.receipt_digest}
    payload["bundle_digest" if complete else "report_digest"] = _digest(payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        payload = build_interference_reason_bundle(
            args.cohort, args.manifest, output=args.output)
    except (OSError, TypeError, KeyError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({key: payload[key] for key in (
        "status", "case_count", "derived_count", "cohort_receipt_digest")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
