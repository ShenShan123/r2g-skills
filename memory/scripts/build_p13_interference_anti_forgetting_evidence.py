#!/usr/bin/env python3
"""Bind actual interference execution gates and a cold rollback replay.

Unlike the generic witness binder, this entry point derives gate statements
by replaying the three policy executions against retained source/checker
files. It replays the same child activation/rollback in RAM and binds every
gate to the same training plan and child. Failed gates yield an ineligible
witness, not permission to mutate memory. Missing executions fail closed.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_p13_interference_policy_execution import audit_policy_execution
from scripts.audit_p13_interference_shadow_view import audit_shadow_view
from scripts.build_p13_anti_forgetting_witness import build_p13_anti_forgetting_witness
from scripts.build_p13_interference_policy_views import VIEWS, _self_digest
from scripts.build_p13_interference_source_bound_plan import _digest, _load_json, _reference, _sha256
from scripts.build_r3_orfs_interference_source_bound_inputs import _write
from tehm.ids import stable_dumps


class InterferenceAntiForgettingError(ValueError):
    """Actual gate executions, child, plan, or rollback do not match."""


_COMPARISON_FIELDS = ("toolchain_digest", "oracle_digest", "platform_digest", "pdk_digest",
                      "candidate_budget", "utility_contract_id", "utility_contract_digest")


def _pin(path):
    return {"path": str(path), "sha256": _sha256(path)}


def _gate_evidence(path, *, purpose, plan_ref, child_digest, replay_dir, comparison_pins=None):
    path = Path(path).resolve()
    report = _load_json(path, "actual execution gate")
    _self_digest(report, "report_digest")
    if (report.get("version") != "p13-interference-policy-execution-audit-v1" or
            report.get("purpose") != purpose or report.get("source_bound_plan") != plan_ref or
            type(report.get("passed")) is not bool):
        raise InterferenceAntiForgettingError("gate purpose/plan/status mismatch")
    freeze_path, freeze = _reference(report["policy_freeze"], "policy freeze")
    if (_self_digest(freeze, "report_digest") != report["policy_freeze"].get("report_digest") or
            freeze.get("source_bound_plan") != plan_ref or
            freeze.get("child_content_digest") != child_digest):
        raise InterferenceAntiForgettingError("execution gate audits a different child or plan")
    if comparison_pins is not None:
        manifests = freeze.get("policy_manifests", {})
        if set(manifests) != set(VIEWS):
            raise InterferenceAntiForgettingError("gate lacks three frozen comparison manifests")
        for view in VIEWS:
            _, manifest = _reference(manifests[view], "policy comparison manifest")
            if {key: manifest.get(key) for key in _COMPARISON_FIELDS} != comparison_pins:
                raise InterferenceAntiForgettingError("gate changed training toolchain/oracle/budget/objective")
    refs = report.get("executed_cohorts", {})
    if set(refs) != set(VIEWS):
        raise InterferenceAntiForgettingError("execution gate lacks three real policy cohorts")
    report_paths = {view: _reference(refs[view], "executed cohort")[0] for view in VIEWS}
    reports_dir = report_paths["Mt"].parent
    if any(path != reports_dir / (view + "-report.json") for view, path in report_paths.items()):
        raise InterferenceAntiForgettingError("executed cohort directory/view coverage mismatch")
    replay = audit_policy_execution(freeze_path, reports_dir, purpose=purpose,
                                   output=Path(replay_dir) / (purpose + ".json"))
    if stable_dumps(replay) != stable_dumps(report):
        raise InterferenceAntiForgettingError("execution gate does not replay retained physical evidence")
    return report, {**_pin(path), "receipt_id": report["receipt_id"],
                    "report_digest": report["report_digest"]}


def build_interference_anti_forgetting_evidence(source_bound_plan, shadow_view_preflight,
        target_audit, non_target_audit, heldout_audit, *, output_dir):
    plan_path, preflight_path = (Path(p).resolve() for p in (source_bound_plan, shadow_view_preflight))
    gate_paths = [Path(p).resolve() for p in (target_audit, non_target_audit, heldout_audit)]
    output = Path(output_dir).resolve()
    if output.exists():
        raise InterferenceAntiForgettingError("anti-forgetting output directory must be new")
    if len(set(gate_paths)) != 3:
        raise InterferenceAntiForgettingError("execution gate files must be distinct")
    if any(path.is_relative_to(output) for path in (plan_path, preflight_path, *gate_paths)):
        raise InterferenceAntiForgettingError("output directory cannot contain input evidence")
    plan = _load_json(plan_path, "source-bound plan")
    plan_ref = {**_pin(plan_path), "report_digest": _self_digest(plan, "report_digest")}
    preflight = _load_json(preflight_path, "shadow-view preflight")
    _self_digest(preflight, "report_digest")
    if (preflight.get("source_bound_plan") != plan_ref or
            preflight.get("preflight_passed") is not True or
            plan.get("physical_utility_contract_bound") is not True):
        raise InterferenceAntiForgettingError("shadow preflight differs from the frozen utility plan")
    _, bundle = _reference(plan["reason_bundle"], "admitted training reason bundle")
    _, training_manifest = _reference({"path": bundle["manifest"], "sha256": bundle["manifest_sha256"]},
                                      "admitted training manifest")
    comparison_pins = {key: training_manifest[key] for key in _COMPARISON_FIELDS}
    pins = {str(path): _sha256(path) for path in (plan_path, preflight_path, *gate_paths)}
    child_digest = preflight["child_content_digest"]
    gates, refs = {}, {}
    with tempfile.TemporaryDirectory(prefix="tehm-interference-anti-replay-") as tmp:
        for purpose, path in zip(("target_replay", "non_target", "heldout"), gate_paths, strict=True):
            gates[purpose], refs[purpose] = _gate_evidence(
                path, purpose=purpose, plan_ref=plan_ref, child_digest=child_digest, replay_dir=tmp,
                comparison_pins=comparison_pins)
        # A fresh activation/rollback, not an unchanged pre-update NO_MEMORY receipt.
        replay = audit_shadow_view(plan_path, output=Path(tmp) / "rollback-preflight.json")
        if stable_dumps(replay) != stable_dumps(preflight):
            raise InterferenceAntiForgettingError("exact child activation/rollback no longer replays")
    exact_routes = all(replay["rollback_routes"][cid] == case["before_route"]
                       for cid, case in replay["case_routes"].items())
    verified = (exact_routes and replay["raw_evidence_preserved"] is True and
                replay["source_database_unchanged"] is True and replay["staging_discarded"] is True)
    if not verified:
        raise InterferenceAntiForgettingError("raw evidence, exact route or rollback verification failed")
    source = Path(plan["source_database"]["path"])
    if _sha256(source) != plan["source_database"]["sha256"]:
        raise InterferenceAntiForgettingError("rollback source database drift")
    if any(_sha256(Path(path)) != sha for path, sha in pins.items()):
        raise InterferenceAntiForgettingError("gate input drift during cold replay")
    rollback = {"version": "p13-interference-actual-rollback-gate-v1", "gate": "rollback",
                "source_bound_plan": plan_ref, "source_database": plan["source_database"],
                "child_content_digest": child_digest,
                "shadow_view_preflight": {**_pin(preflight_path), "report_digest": replay["report_digest"]},
                "actual_activation_and_rollback_cold_replayed": True,
                "exact_routes_and_full_candidates_restored": True, "raw_evidence_preserved": True,
                "verified": verified, "staging_discarded": True, "canonical_memory_mutation": "none",
                "production_runtime_imported": False}
    rollback["receipt_id"] = "p13_interference_rollback_" + _digest(rollback).split(":")[1][:24]
    rollback["report_digest"] = _digest(rollback)
    rollback_path = output / "rollback-gate.json"
    _write(rollback_path, rollback)
    manifest = {"version": "p13-anti-forgetting-manifest-v1", "campaign_id": plan["campaign_id"],
                "case_id": "memory-interference-specialization:u50",
                "target_replay": {**refs["target_replay"], "passed": gates["target_replay"]["passed"]},
                "non_target_regression": {**refs["non_target"], "regression_free": gates["non_target"]["passed"]},
                "heldout_audit": {**refs["heldout"], "passed": gates["heldout"]["passed"]},
                "rollback": {**_pin(rollback_path), "receipt_id": rollback["receipt_id"],
                             "verified": verified, "pointer": str(source) + "#" + plan["source_database"]["logical_digest"]}}
    manifest_path = output / "anti-forgetting-manifest.json"
    _write(manifest_path, manifest)
    witness_path = output / "anti-forgetting-witness.json"
    witness = build_p13_anti_forgetting_witness(manifest_path, output=witness_path)
    report = {"version": "p13-interference-actual-anti-forgetting-evidence-v1",
              "campaign_id": plan["campaign_id"], "source_bound_plan": plan_ref,
              "shadow_view_preflight": _pin(preflight_path), "child_content_digest": child_digest,
              "frozen_training_comparison_pins": comparison_pins,
              "gate_evidence": refs, "gate_passed": {key: value["passed"] for key, value in gates.items()},
              "actual_gate_oracles_cold_replayed": True, "rollback_gate": _pin(rollback_path),
              "anti_forgetting_manifest": _pin(manifest_path), "anti_forgetting_witness": _pin(witness_path),
              "eligible": witness["eligible"], "witness_digest": witness["witness"]["receipt_digest"],
              "audit_source_binding": _pin(Path(__file__).resolve()),
              "applied_shadow_update_receipt_created": False, "formal_p14_attribution_created": False,
              "canonical_memory_mutation": "none", "production_runtime_imported": False,
              "evaluation_only": True, "learner_support_imported": False, "promotion_attempted": False,
              "statistical_generalization_claimed": False, "memory_docs_submitted": False}
    report["report_digest"] = _digest(report)
    _write(output / "anti-forgetting-evidence-report.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-bound-plan", type=Path, required=True)
    parser.add_argument("--shadow-view-preflight", type=Path, required=True)
    parser.add_argument("--target-audit", type=Path, required=True)
    parser.add_argument("--non-target-audit", type=Path, required=True)
    parser.add_argument("--heldout-audit", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_interference_anti_forgetting_evidence(
            args.source_bound_plan, args.shadow_view_preflight, args.target_audit,
            args.non_target_audit, args.heldout_audit, output_dir=args.output_dir)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"eligible": report["eligible"], "report_digest": report["report_digest"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
