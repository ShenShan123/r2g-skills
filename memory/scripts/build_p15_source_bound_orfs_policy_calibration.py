#!/usr/bin/env python3
"""Prospective, source-bound ORFS policy calibration with a utility oracle.

Freeze this oracle before ANY calibration execution. Actual typed outcomes
are never relabeled: physical utility harm can independently label an oracle
RISK even when narrow checks PASS. INAPPLICABLE remains outside the binary
contract, and absent router probabilities remain absent confidence. Neither
descriptive metrics nor an oracle label can grant production authority.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_p13_interference_policy_execution import _distinct_executions, _retained_arm
from scripts.build_p13_interference_policy_views import VIEWS, _self_digest
from scripts.build_p13_interference_source_bound_plan import _digest, _load_json, _reference, _sha256
from scripts.build_r3_orfs_interference_source_bound_inputs import _write
from scripts.run_orfs_interference_policy_view import validate_policy_inputs
from tehm.capability.delta import MemoryDeltaReceipt, memory_delta_from_shadow_update
from tehm.evaluation import OrfsPairedCohortReceipt, P12_ARMS
from tehm.evaluation.no_skill_calibration import NoSkillCalibrationSample, evaluate_no_skill_calibration
from tehm.evaluation.orfs_paired_utility import replay_orfs_paired_utility_receipt
from tehm.evolution import AppliedShadowUpdateReceipt


ORACLE_SPEC = {
    "version": "p15-source-bound-orfs-utility-oracle-v1",
    "baseline_positive": "observed narrow counterfactual PASS",
    "RISK": "baseline PASS and forced-memory FAIL or replayed frozen utility contract FAIL",
    "USE_MEMORY": "baseline and forced-memory PASS, utility PASS, some Pareto improvement and no Pareto harm",
    "other_pairs": "UNCLASSIFIABLE, never automatically NO_MATCH or STATE_SHIFT",
    "unknown": "fail closed, never count as a PASS or a calibration sample",
    "routing_prediction_used_to_derive_label": False,
    "utility_contract_selected_from_outcome": False,
    "binary_predictions": ["APPLY", "CONSIDER", "NO_SKILL"],
    "outside_binary": ["INAPPLICABLE", "ABSTAIN"],
    "router_confidence_policy": "ABSENT_NOT_IMPUTED",
    "minimum_sample_count": 20, "minimum_reason_cases": 1,
    "calibration_bins": 10, "confidence_interval": "Wilson 95 percent, descriptive only",
    "legacy_production_readiness_import": "not implemented for this distinct audit schema",
}


class SourceBoundPolicyCalibrationError(ValueError):
    """Source partition, pre-outcome oracle freeze or retained execution drift."""


def _pin(path):
    return {"path": str(path), "sha256": _sha256(path)}


def _partition(cases, role):
    if not cases or len({case["case_id"] for case in cases}) != len(cases):
        raise SourceBoundPolicyCalibrationError("partition case membership is empty or duplicated")
    for case in cases:
        if (case.get("dataset_split") != role or case.get("role") != role or
                case.get("learner_eligible") is not False or not case.get("lineage_id")):
            raise SourceBoundPolicyCalibrationError("execution-bound prospective partition is not " + role)


def _rtl_membership(authority):
    rows = authority["cases"]
    return ({row["lineage_id"] for row in rows.values()},
            {sha.removeprefix("sha256:") for row in rows.values() for sha in row["rtl_sha256"]})


def _disjoint(calibration, training, heldout):
    lineages, rtl = _rtl_membership(calibration)
    if len(lineages) < 2 or len(rtl) < 2:
        raise SourceBoundPolicyCalibrationError("calibration needs two actual distinct RTL lineages")
    refs = {}
    for role, authority in (("training", training), ("held_out", heldout)):
        other_lineages, other_rtl = _rtl_membership(authority)
        if not other_lineages or not other_rtl or lineages & other_lineages or rtl & other_rtl:
            raise SourceBoundPolicyCalibrationError("calibration overlaps " + role + " lineage or RTL content")
        refs[role] = {"lineages": sorted(other_lineages), "rtl_sha256": sorted(other_rtl)}
    return {"calibration": {"lineages": sorted(lineages), "rtl_sha256": sorted(rtl)},
            "references": refs, "disjoint": True, "iid_or_population_representativeness_claimed": False}


def _heldout_reference(p14, heldout_path, heldout):
    """The independent split must be the actual heldout bound by P14/AF."""
    _, af = _reference(p14["anti_forgetting_evidence"], "actual anti-forgetting evidence")
    _self_digest(af, "report_digest")
    if (af.get("eligible") is not True or af.get("actual_gate_oracles_cold_replayed") is not True or
            af.get("learner_support_imported") is not False or
            af.get("canonical_memory_mutation") != "none" or
            af.get("production_runtime_imported") is not False or af.get("promotion_attempted") is not False):
        raise SourceBoundPolicyCalibrationError("actual anti-forgetting authority boundary drift")
    _, audit = _reference(af["gate_evidence"]["heldout"], "actual heldout execution audit")
    _self_digest(audit, "report_digest")
    expected = {**_pin(heldout_path), "report_digest": heldout["report_digest"]}
    if (audit.get("purpose") != "heldout" or audit.get("passed") is not True or
            audit.get("policy_freeze") != expected or
            audit.get("source_bound_plan") != af.get("source_bound_plan") or
            heldout.get("source_bound_plan") != af.get("source_bound_plan") or
            heldout.get("child_content_digest") != af.get("child_content_digest")):
        raise SourceBoundPolicyCalibrationError("heldout split is not the actual P14 anti-forgetting cohort")
    return {"anti_forgetting_evidence": p14["anti_forgetting_evidence"],
            "heldout_execution_audit": af["gate_evidence"]["heldout"]}


def _comparison_inputs(inputs):
    """Source, utility, constraints and budget are not outcome-tuned by view."""
    before = inputs["Mt"]
    for view, current in inputs.items():
        for key in ("toolchain_digest", "oracle_digest", "platform_digest", "pdk_digest",
                    "candidate_budget", "utility_contract_digest", "utility_contract_id"):
            if current["manifest"][key] != before["manifest"][key]:
                raise SourceBoundPolicyCalibrationError("three policy views changed comparison " + key)
        baseline_cases = {case["case_id"]: case for case in before["cases"]}
        current_cases = {case["case_id"]: case for case in current["cases"]}
        if set(baseline_cases) != set(current_cases):
            raise SourceBoundPolicyCalibrationError("three policy views changed calibration membership")
        for cid, case in current_cases.items():
            for key in ("source_digest", "source_inputs", "flow_config_observation", "environment",
                        "lineage_id", "dataset_split", "role", "learner_eligible", "target_check"):
                if case[key] != baseline_cases[cid][key]:
                    raise SourceBoundPolicyCalibrationError("three policy views changed source or constraint " + key)


def _before_outcomes(cases):
    for case in cases:
        execution = Path(case["execution_artifacts_root"])
        if execution.exists() and (not execution.is_dir() or any(execution.iterdir())):
            raise SourceBoundPolicyCalibrationError("oracle must freeze before any calibration arm execution")


def _prepare_inputs(policy_freeze, p14_attribution, heldout_policy_freeze, *, before_outcomes):
    freeze_path, p14_path, heldout_path = (Path(p).resolve() for p in (
        policy_freeze, p14_attribution, heldout_policy_freeze))
    freeze = _load_json(freeze_path, "calibration policy freeze")
    _self_digest(freeze, "report_digest")
    if (freeze.get("version") != "p13-interference-policy-views-freeze-v1" or
            set(freeze.get("policy_manifests", {})) != set(VIEWS) or
            freeze.get("exact_remove_delta_policy_restored") is not True or
            freeze.get("canonical_memory_mutation") != "none" or
            freeze.get("production_runtime_imported") is not False or
            freeze.get("promotion_attempted") is not False):
        raise SourceBoundPolicyCalibrationError("calibration requires three actual compiled policy views")
    inputs = {}
    for view in VIEWS:
        path, _ = _reference(freeze["policy_manifests"][view], "calibration policy manifest")
        inputs[view] = validate_policy_inputs(path)
        _partition(inputs[view]["cases"], "calibration")
        if before_outcomes:
            _before_outcomes(inputs[view]["cases"])
    _comparison_inputs(inputs)
    p14 = _load_json(p14_path, "formal P14 attribution")
    _self_digest(p14, "report_digest")
    runner_pin = p14["runner_source_binding"]
    if (_sha256(Path(runner_pin["path"])) != runner_pin["sha256"] or
            p14.get("version") != "p14-interference-formal-attribution-report-v1" or
            p14.get("bounded_attribution_complete") is not True or
            p14.get("canonical_memory_mutation") != "none" or
            p14.get("production_runtime_imported") is not False or p14.get("promotion_attempted") is not False):
        raise SourceBoundPolicyCalibrationError("P14 mutation or evaluation boundary drift")
    _, p13 = _reference(p14["p13_shadow_update"], "formal P13 shadow receipt")
    _self_digest(p13, "report_digest")
    if (p13.get("eligible_for_p14_attribution") is not True or
            p13.get("evaluation_view_activation_performed_by_this_update") is not False or
            p13.get("canonical_memory_mutation") != "none" or
            p13.get("production_runtime_imported") is not False or p13.get("promotion_attempted") is not False or
            p13.get("anti_forgetting_evidence") != p14.get("anti_forgetting_evidence") or
            _sha256(Path(p13["runner_source_binding"]["path"])) != p13["runner_source_binding"]["sha256"]):
        raise SourceBoundPolicyCalibrationError("formal P13 mutation or source boundary drift")
    receipt = AppliedShadowUpdateReceipt.from_dict(p13["applied_shadow_update_receipt"])
    delta = memory_delta_from_shadow_update(receipt)
    if (MemoryDeltaReceipt.from_dict(p14["memory_delta"]).to_dict() != delta.to_dict() or
            freeze.get("source_bound_plan") != p13.get("source_bound_plan") or
            freeze.get("child_content_digest") != p13.get("child_content_digest")):
        raise SourceBoundPolicyCalibrationError("calibration evaluates a different formal mutation")
    heldout = _load_json(heldout_path, "independent heldout policy freeze")
    _self_digest(heldout, "report_digest")
    actual_heldout = _heldout_reference(p14, heldout_path, heldout)
    heldout_manifest_path, _ = _reference(heldout["policy_manifests"]["Mt"], "heldout policy manifest")
    heldout_inputs = validate_policy_inputs(heldout_manifest_path)
    _partition(heldout_inputs["cases"], "held_out")
    _, heldout_authority = _reference(heldout_inputs["authority"]["baseline_input_authority"], "heldout authority")
    _, calibration_authority = _reference(inputs["Mt"]["authority"]["baseline_input_authority"], "calibration authority")
    _, plan = _reference(freeze["source_bound_plan"], "source-bound training plan")
    _, training_authority = _reference(plan["input_authority"], "training authority")
    evidence = {"policy_freeze": {**_pin(freeze_path), "report_digest": freeze["report_digest"]},
        "p14_attribution": {**_pin(p14_path), "report_digest": p14["report_digest"]},
        "heldout_policy_freeze": {**_pin(heldout_path), "report_digest": heldout["report_digest"]},
        "actual_heldout_binding": actual_heldout,
        "source_bound_plan": freeze["source_bound_plan"], "child_content_digest": freeze["child_content_digest"],
        "disjoint_witness": _disjoint(calibration_authority, training_authority, heldout_authority)}
    return inputs, evidence


def prepare_oracle_binding(policy_freeze, p14_attribution, heldout_policy_freeze, *, output):
    output = Path(output).resolve()
    if output.exists():
        raise SourceBoundPolicyCalibrationError("oracle binding output must be new")
    _, evidence = _prepare_inputs(policy_freeze, p14_attribution, heldout_policy_freeze, before_outcomes=True)
    report = {"version": "p15-source-bound-orfs-calibration-oracle-binding-v1", **evidence,
        "oracle_spec": ORACLE_SPEC, "oracle_source_binding": _pin(Path(__file__).resolve()),
        "frozen_before_calibration_execution": True, "provider_calls": 0,
        "canonical_memory_mutation": "none", "production_runtime_imported": False,
        "promotion_attempted": False, "evaluation_only": True, "memory_docs_submitted": False}
    report["report_digest"] = _digest(report)
    _write(output, report)
    return report


def _oracle_label(pair):
    """NO routing argument. Do not mutate outcomes to encode physical utility."""
    baseline, memory = pair.arm_receipts["NO_MEMORY"], pair.arm_receipts["ALWAYS_MEMORY"]
    if baseline.outcome == "UNKNOWN" or memory.outcome == "UNKNOWN":
        raise SourceBoundPolicyCalibrationError("unknown execution is not an oracle label")
    utility = None
    decision, reason, unclassified = None, None, None
    if baseline.outcome == "PASS" and memory.outcome == "FAIL":
        decision, reason = "NO_SKILL", "RISK"
    elif baseline.outcome == memory.outcome == "PASS":
        utility = replay_orfs_paired_utility_receipt(baseline, memory)
        observation = utility["observation"]
        pareto = observation["raw_pareto"]
        if observation["status"] == "FAIL":
            decision, reason = "NO_SKILL", "RISK"
        elif (observation["status"] == "PASS" and pareto.get("improved_metrics") and
                not pareto.get("harmful_metrics")):
            decision = "USE_MEMORY"
        else:
            unclassified = "neutral_or_unestablished_utility_is_not_evidence_memory_safely_helps"
    else:
        unclassified = "this_physical_oracle_does_not_establish_NO_MATCH_or_STATE_SHIFT"
    report = {"version": "p15-source-bound-orfs-utility-oracle-label-v1", "case_id": pair.case_id,
        "paired_receipt_digest": pair.receipt_digest,
        "baseline_execution_digest": baseline.execution_digest, "forced_execution_digest": memory.execution_digest,
        "baseline_outcome": baseline.outcome, "forced_outcome": memory.outcome,
        "expected_decision": decision, "expected_reason": reason, "unclassifiable_reason": unclassified,
        "utility_receipt": utility, "oracle_spec_digest": _digest(ORACLE_SPEC),
        "router_prediction_used": False, "execution_outcomes_rewritten": False}
    report["receipt_digest"] = _digest(report)
    return report


def _prediction(route):
    if route.decision in {"APPLY", "CONSIDER"}:
        return "USE_MEMORY", None
    if route.decision == "NO_SKILL":
        return "NO_SKILL", route.no_skill_reason
    return None, None


def _binary_sample(cid, label, route, case):
    prediction, reason = _prediction(route)
    if prediction is None or label["expected_decision"] is None:
        return None
    return NoSkillCalibrationSample(case_id=cid, predicted_decision=prediction,
        predicted_reason=reason, expected_decision=label["expected_decision"],
        expected_reason=label["expected_reason"], confidence=None,
        strata={"mechanism_family": "DENSITY_RELIEF",
            "design": case["flow_config_observation"]["values"]["DESIGN_NAME"],
            "platform": case["platform"], "flow_regime": "ORFS_FIXED_COUNTERFACTUAL_WITH_PAIRED_UTILITY",
            "model_identity": "NONE_DETERMINISTIC_RUNTIME", "state_shift_dimension": "none"},
        routing_receipt_id=route.routing_receipt_id)


def validate_execution_freeze(execution_freeze, oracle_binding):
    """Check the source-pinned preflight driver and its oracle BEFORE flows."""
    freeze = _load_json(Path(execution_freeze).resolve(), "prospective calibration execution freeze")
    _self_digest(freeze, "report_digest")
    binding_path, binding = _reference(freeze["oracle_binding"], "pre-outcome utility oracle")
    _self_digest(binding, "report_digest")
    if (binding_path != Path(oracle_binding).resolve() or
            freeze.get("version") != "p15-source-bound-orfs-three-policy-execution-freeze-v1" or
            freeze.get("purpose") != "CALIBRATION" or freeze.get("learner_eligible") is not False or
            freeze.get("production_runtime_imported") is not False or freeze.get("promotion_attempted") is not False or
            freeze.get("canonical_memory_mutation") != "none" or
            binding.get("version") != "p15-source-bound-orfs-calibration-oracle-binding-v1" or
            binding.get("canonical_memory_mutation") != "none" or
            binding.get("production_runtime_imported") is not False or
            binding.get("promotion_attempted") is not False or binding.get("evaluation_only") is not True or
            binding.get("oracle_spec") != ORACLE_SPEC or
            binding.get("oracle_source_binding") != _pin(Path(__file__).resolve()) or
            binding.get("frozen_before_calibration_execution") is not True or
            freeze.get("policy_freeze") != binding.get("policy_freeze") or
            freeze.get("execution_order") != list(VIEWS) or
            freeze.get("seed_binding", {}).get("OR_SEED") != "UNSET_PINNED_OPENROAD_DEFAULT"):
        raise SourceBoundPolicyCalibrationError("pre-outcome calibration execution protocol or oracle drift")
    for key in ("execution_driver_binding", "gate_audit_binding", "layout_audit_binding", "source_layout_audit"):
        ref = freeze[key]
        if _sha256(Path(ref["path"])) != ref["sha256"]:
            raise SourceBoundPolicyCalibrationError("pre-outcome calibration source drift: " + key)
    _, policy_freeze = _reference(freeze["policy_freeze"], "calibration policy freeze")
    _self_digest(policy_freeze, "report_digest")
    if (set(freeze.get("views", {})) != set(VIEWS) or any(
            freeze["views"][view]["manifest"] != policy_freeze["policy_manifests"][view] for view in VIEWS)):
        raise SourceBoundPolicyCalibrationError("calibration execution changed policy manifests")
    return freeze


def _audit_execution(manifest_ref, inputs, report_path, view):
    manifest_path, manifest = _reference(manifest_ref, "executed calibration manifest")
    raw = _load_json(report_path, "actual calibration policy execution")
    if (raw.get("report_version") != "p13-interference-policy-view-execution-report-v1" or
            raw.get("policy_view") != view or raw.get("manifest") != str(manifest_path) or
            raw.get("manifest_sha256") != _sha256(manifest_path) or raw.get("manifest_digest") != _digest(manifest) or
            raw.get("input_authority_ref") != inputs["authority_ref"] or
            raw.get("candidate_refs") != inputs["candidate_refs"] or
            raw.get("learner_eligible") is not False or raw.get("evaluation_only") is not True or
            raw.get("production_runtime_imported") is not False or
            raw.get("canonical_memory_mutation") != "none" or raw.get("promotion_attempted") is not False):
        raise SourceBoundPolicyCalibrationError("actual calibration execution binding mismatch")
    cohort = OrfsPairedCohortReceipt.from_dict(raw)
    normalized = {**cohort.to_dict(), "receipt_digest": cohort.receipt_digest}
    if (raw.get("cohort_receipt") != normalized or raw.get("cohort_receipt_digest") != cohort.receipt_digest or
            cohort.campaign_manifest_digest != _digest(manifest)):
        raise SourceBoundPolicyCalibrationError("typed calibration cohort binding mismatch")
    _, request = _reference(raw["preexecution_request"], "prospective execution request")
    _self_digest(request, "request_digest")
    if (request.get("manifest") != {"path": str(manifest_path), "sha256": _sha256(manifest_path), "digest": _digest(manifest)} or
            request.get("input_authority") != inputs["authority_ref"] or request.get("policy_view") != view or
            _sha256(Path(request["execution_runner_binding"]["path"])) != request["execution_runner_binding"]["sha256"]):
        raise SourceBoundPolicyCalibrationError("prospective calibration execution request drift")
    cases = {case["case_id"]: case for case in inputs["cases"]}
    if set(cases) != set(cohort.case_receipts):
        raise SourceBoundPolicyCalibrationError("calibration execution omitted a declared case")
    retained, paths = {}, []
    for cid, pair in cohort.case_receipts.items():
        if pair.routing_receipt_id != inputs["routing"][cid].routing_receipt_id:
            raise SourceBoundPolicyCalibrationError("executed calibration route mismatch")
        retained[cid] = {}
        for arm in P12_ARMS:
            observed = _retained_arm(pair.arm_receipts[arm], inputs["candidates"][cid][arm], cases[cid], arm)
            retained[cid][arm] = observed
            paths.append(observed["execution_project_dir"])
    return cohort, retained, paths


def _executed_comparison(cohorts):
    before = cohorts["Mt"]
    if before.lineage_count < 2 or any(
            current.source_digests != before.source_digests or
            current.source_content_digests != before.source_content_digests or
            any(getattr(current, key) != getattr(before, key) for key in (
                "toolchain_digest", "oracle_digest", "platform_digest", "pdk_digest", "candidate_budget"))
            for current in cohorts.values()):
        raise SourceBoundPolicyCalibrationError("three executed cohorts changed frozen comparison inputs")


def build_policy_calibration(oracle_binding, reports_dir, *, output_dir, execution_freeze=None):
    binding_path, report_dir, output = (Path(p).resolve() for p in (oracle_binding, reports_dir, output_dir))
    if output.exists() or binding_path.is_relative_to(output) or report_dir.is_relative_to(output):
        raise SourceBoundPolicyCalibrationError("calibration output must be new and separate")
    binding = _load_json(binding_path, "prospective calibration oracle")
    _self_digest(binding, "report_digest")
    if (binding.get("version") != "p15-source-bound-orfs-calibration-oracle-binding-v1" or
            binding.get("frozen_before_calibration_execution") is not True or
            binding.get("oracle_spec") != ORACLE_SPEC or binding.get("oracle_source_binding") != _pin(Path(__file__).resolve())):
        raise SourceBoundPolicyCalibrationError("frozen calibration oracle definition or source drift")
    execution_freeze = Path(execution_freeze).resolve() if execution_freeze else report_dir.parent / "three-policy-execution-freeze-r1.json"
    validate_execution_freeze(execution_freeze, binding_path)
    refs = [binding[key] for key in ("policy_freeze", "p14_attribution", "heldout_policy_freeze")]
    paths = [_reference(ref, "calibration context")[0] for ref in refs]
    inputs, evidence = _prepare_inputs(*paths, before_outcomes=False)
    if any(binding[key] != value for key, value in evidence.items()):
        raise SourceBoundPolicyCalibrationError("prospective calibration context does not replay")
    _, freeze = _reference(binding["policy_freeze"], "calibration policy freeze")
    views, executed_paths, cohorts = {}, [], {}
    for view in VIEWS:
        report_path = report_dir / (view + "-report.json")
        cohort, retained, current_paths = _audit_execution(freeze["policy_manifests"][view], inputs[view], report_path, view)
        cohorts[view] = cohort
        executed_paths.extend(current_paths)
        labels, samples, excluded = {}, [], {}
        by_id = {case["case_id"]: case for case in inputs[view]["cases"]}
        for cid, pair in sorted(cohort.case_receipts.items()):
            label = _oracle_label(pair)
            labels[cid] = label
            route = inputs[view]["routing"][cid]
            prediction, reason = _prediction(route)
            if prediction is None or label["expected_decision"] is None:
                excluded[cid] = {"route": route.to_dict(), "routing_receipt_id": route.routing_receipt_id,
                    "oracle_label_digest": label["receipt_digest"], "reason":
                    "OUTSIDE_P15_BINARY_CONTRACT" if prediction is None else "ORACLE_UNCLASSIFIABLE"}
                continue
            samples.append(_binary_sample(cid, label, route, by_id[cid]))
        receipt = evaluate_no_skill_calibration(samples, minimum_sample_count=ORACLE_SPEC["minimum_sample_count"],
            minimum_reason_cases=ORACLE_SPEC["minimum_reason_cases"], calibration_bins=ORACLE_SPEC["calibration_bins"]) if samples else None
        coverage = len(samples) / len(cohort.case_receipts)
        views[view] = {"executed_cohort": {**_pin(report_path), "cohort_receipt_digest": cohort.receipt_digest},
            "retained_execution_audit": retained, "oracle_labels": labels,
            "binary_samples": [sample.to_dict() for sample in samples], "excluded_cases_retained": excluded,
            "declared_case_count": len(cohort.case_receipts), "binary_sample_coverage": coverage,
            "no_skill_calibration": None if receipt is None else {**receipt.to_dict(), "receipt_digest": receipt.receipt_digest},
            "eligible": coverage == 1 and receipt is not None and receipt.eligible,
            "assigned_router_probabilities": False, "source_disjoint_is_not_iid": True}
    _distinct_executions(executed_paths)
    _executed_comparison(cohorts)
    report = {"version": "p15-source-bound-orfs-policy-calibration-audit-v1",
        "oracle_binding": {**_pin(binding_path), "report_digest": binding["report_digest"]}, **evidence,
        "prospective_execution_freeze": _pin(execution_freeze),
        "views": views, "independent_executed_workspaces": len(executed_paths),
        "eligible": all(view["eligible"] for view in views.values()),
        "status": "PASS" if all(view["eligible"] for view in views.values()) else "NOT_ESTABLISHED",
        "production_promotion_eligible": False, "legacy_production_readiness_imported": False,
        "canonical_memory_mutation": "none", "production_runtime_imported": False,
        "learner_support_imported": False, "promotion_attempted": False, "evaluation_only": True,
        "strict_signoff_claimed": False, "statistical_generalization_claimed": False,
        "runner_source_binding": _pin(Path(__file__).resolve()), "memory_docs_submitted": False}
    report["report_digest"] = _digest(report)
    _write(output / "policy-calibration-audit.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    for name in ("policy-freeze", "p14-attribution", "heldout-policy-freeze", "output", "oracle-binding", "reports-dir", "output-dir", "execution-freeze"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args(argv)
    names = ("policy_freeze", "p14_attribution", "heldout_policy_freeze", "output") if args.prepare else ("oracle_binding", "reports_dir", "output_dir")
    if any(getattr(args, name) is None for name in names):
        parser.error("missing required arguments for selected prepare/audit mode")
    try:
        report = prepare_oracle_binding(args.policy_freeze, args.p14_attribution, args.heldout_policy_freeze,
            output=args.output) if args.prepare else build_policy_calibration(args.oracle_binding, args.reports_dir,
                output_dir=args.output_dir, execution_freeze=args.execution_freeze)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"report_digest": report["report_digest"], "status": report.get("status", "ORACLE_FROZEN")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
