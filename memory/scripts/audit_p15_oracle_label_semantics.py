#!/usr/bin/env python3
"""Replay P15 label semantics against retained source-bound ORFS receipts.

This is a retrospective engineering audit, not a new calibration campaign.
The original pre-execution physical utility oracle remains authoritative for
its scope. A generic execution-only oracle cannot infer utility, NO_MATCH or
router prediction probabilities from narrow PASS/PASS outcomes.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_p15_source_bound_orfs_policy_calibration as physical
from scripts.run_orfs_p12_cohort import _manifest, _routing_map, _candidate_map
from tehm.evaluation.no_skill_calibration import (
    NoSkillCalibrationError, derive_no_skill_oracle_label,
)


def retained_inputs(manifest_ref):
    """Decode historical artifacts only; do not replay current routing code.

    The full runtime generation is frozen to its original epoch. This narrow
    label audit is not an authority replay of that generation and does not
    substitute for an origin-backed P16 replay.
    """
    path, _ = physical._reference(manifest_ref, "original policy manifest")
    manifest, cases, budget, minimum = _manifest(path)
    physical._partition(cases, "calibration")
    authority_path, authority = physical._reference(
        manifest["input_authority"], "original input authority")
    authority_digest = physical._self_digest(authority, "authority_digest")
    if manifest["input_authority"].get("authority_digest") != authority_digest:
        raise ValueError("original authority digest mismatch")
    source = authority["source_database"]
    if physical._sha256(Path(source["path"])) != source["sha256"]:
        raise ValueError("original memory source drift")
    routing, routing_ref = _routing_map(Path(authority["routing_decisions"]["path"]), cases)
    if routing_ref != authority["routing_decisions"]:
        raise ValueError("original route artifact drift")
    candidates, refs = {}, {}
    for case in cases:
        candidates[case["case_id"]], refs[case["case_id"]] = _candidate_map(case, path)
    return {"manifest": manifest, "cases": cases, "budget": budget,
        "min_lineages": minimum, "authority": authority,
        "authority_ref": {**physical._pin(authority_path), "authority_digest": authority_digest},
        "routing": routing, "candidates": candidates, "candidate_refs": refs}


def audit(oracle_binding, execution_freeze, execution_root, *, output):
    output = Path(output).resolve()
    if output.exists() or output.is_relative_to(ROOT):
        raise ValueError("audit output must be new and outside memory source")
    binding_path = Path(oracle_binding).resolve()
    binding = physical._load_json(binding_path, "original physical oracle binding")
    physical._self_digest(binding, "report_digest")
    protocol = physical.validate_execution_freeze(execution_freeze, binding_path)
    inputs = {view: retained_inputs(protocol["views"][view]["manifest"])
              for view in physical.VIEWS}
    physical._comparison_inputs(inputs)
    paths = [binding_path, Path(execution_freeze).resolve(), Path(__file__).resolve(),
             ROOT / "tehm/evaluation/no_skill_calibration.py",
             Path(physical.__file__).resolve()]
    cohorts, views, executions = {}, {}, []
    for view in physical.VIEWS:
        report_path = (Path(execution_root) / (view + "-report.json")).resolve()
        paths.append(report_path)
        cohort, retained, workdirs = physical._audit_execution(
            protocol["views"][view]["manifest"], inputs[view], report_path, view)
        cohorts[view] = cohort
        executions.extend(workdirs)
        rows = {}
        for case_id, pair in sorted(cohort.case_receipts.items()):
            try:
                generic = derive_no_skill_oracle_label(pair)
            except NoSkillCalibrationError as exc:
                if str(exc) not in {"NO_MEMORY oracle receipt is incomplete",
                                    "ALWAYS_MEMORY oracle receipt is incomplete"}:
                    raise
                generic = {"status": "NOT_ESTABLISHED", "classifiable": False,
                    "expected_decision": None, "expected_reason": None,
                    "confidence": None, "unclassifiable_reason": str(exc)}
            utility = physical._oracle_label(pair)
            if generic["confidence"] is not None or generic["expected_reason"] == "NO_MATCH":
                raise ValueError("execution-only oracle fabricated confidence or NO_MATCH")
            rows[case_id] = {"paired_receipt_digest": pair.receipt_digest,
                "execution_only_label": generic, "original_physical_utility_label": utility,
                "retained_execution": retained[case_id]}
        views[view] = rows
    physical._executed_comparison(cohorts)
    physical._distinct_executions(executions)
    pins = [physical._pin(path) for path in paths]
    report = {"version": "p15-oracle-label-semantics-audit-v1", "status": "PASS",
        "original_oracle_binding_digest": binding["report_digest"], "inputs": pins,
        "independent_original_case_count": cohorts["Mt"].lineage_count,
        "retained_paired_receipt_count": sum(len(c.case_receipts) for c in cohorts.values()),
        "views": views, "confidence_policy": "ABSENT_NOT_IMPUTED",
        "original_oracle_replaced": False, "new_independent_calibration_samples": 0,
        "fresh_hardware_executions": 0, "retrospective_engineering_audit_only": True,
        "runtime_generation_replayed": False, "p14_authority_replayed": False,
        "p16_origin_replay_established": False,
        "evaluation_only": True, "provider_calls": 0, "learner_support_imported": False,
        "canonical_memory_mutation": "none", "promotion_attempted": False,
        "production_runtime_imported": False, "production_promotion_eligible": False}
    for ref in pins:
        if physical._pin(Path(ref["path"])) != ref:
            raise ValueError("audit source changed during replay")
    report["report_digest"] = physical._digest(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle-binding", required=True)
    parser.add_argument("--execution-freeze", required=True)
    parser.add_argument("--execution-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    result = audit(args.oracle_binding, args.execution_freeze, args.execution_root,
                   output=args.output)
    print(json.dumps({"status": result["status"], "report_digest": result["report_digest"],
                      "new_independent_calibration_samples": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
