#!/usr/bin/env python3
"""Derive replay gates from three independently executed policy cohorts.

This is an execution-evidence oracle, not formal P13/P14 attribution. It
replays source-bound inputs and fixed-constraint/utility receipts against the
retained files. Missing evidence fails closed; aliased pre-update baselines
cannot stand in for a real remove-delta execution. No memory is modified.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_p13_interference_policy_views import VIEWS, _self_digest
from scripts.build_p13_interference_source_bound_plan import _digest, _load_json, _reference, _sha256
from scripts.build_r3_orfs_interference_source_bound_inputs import _write
from scripts.run_orfs_interference_policy_view import validate_policy_inputs
from tehm.evaluation import P12_ARMS, OrfsPairedCohortReceipt
from tehm.evaluation.counterfactual_oracle import (
    build_counterfactual_oracle_receipt, fixed_constraint_check_verdicts,
)
from tehm.evaluation.orfs_paired_utility import _physical, replay_orfs_paired_utility_receipt
from tehm.evaluation.orfs_candidate_oracle import _source_content_binding, _source_inputs
from tehm.ids import stable_dumps
from tehm.lifecycle.orfs_trial import _load_reports, _parse_config
from tehm.physical.effects import _metric
from tehm.physical.utility_contracts import _baseline_metric


class InterferencePolicyExecutionAuditError(ValueError):
    """A purported policy execution is unbound, incomplete, or aliased."""


def _metrics(ppa):
    values = {"area_um2": _baseline_metric(ppa, "area_um2"),
              "power_w": _baseline_metric(ppa, "power_w"),
              "wns_ns": _metric(ppa, "wns_ns"), "tns_ns": _metric(ppa, "tns_ns")}
    if any(value is None or not math.isfinite(value) for value in values.values()):
        raise InterferencePolicyExecutionAuditError("physical policy observation is incomplete")
    return {key: Decimal(str(value)) for key, value in values.items()}


def _zero_regression(before, after):
    # No extra epsilon and no six-decimal rounding that masks small power harm.
    before, after = _metrics(before), _metrics(after)
    deltas = {key: after[key] - before[key] for key in before}
    failures = [key for key, delta in deltas.items()
                if (delta > 0 if key in {"area_um2", "power_w"} else delta < 0)]
    return {"non_regressing": not failures, "failures": sorted(failures),
            "deltas_after_minus_before": {key: str(value) for key, value in deltas.items()},
            "comparison": "reported PPA precision; zero added tolerance"}


def _distinct_executions(paths):
    normalized = [Path(path).resolve() for path in paths]
    if len(set(normalized)) != len(normalized):
        raise InterferencePolicyExecutionAuditError("policy executions alias the same retained workspace")


def _retained_arm(receipt, candidate, case, arm):
    metadata = receipt.metadata.get("oracle_metadata", {})
    project = Path(metadata.get("execution_project_dir", "")).resolve()
    expected = Path(case["execution_artifacts_root"]) / arm.lower()
    if (metadata.get("execution_artifacts_retained") is not True or not project.is_dir() or
            not project.is_relative_to(expected.resolve()) or
            metadata.get("source_digest") != case["source_digest"] or
            receipt.metadata.get("arm") != arm):
        raise InterferencePolicyExecutionAuditError("actual retained execution/source/arm binding mismatch")
    selected = candidate is not None
    if (receipt.source != ("structured_memory" if selected else "no_memory") or
            metadata.get("action_applied") is not selected or
            receipt.action_digest != _digest(candidate.concrete_action if selected else {}) or
            receipt.candidate_digest != (candidate.candidate_digest if selected else _digest({})) or
            (selected and receipt.candidate_id != candidate.candidate_id)):
        raise InterferencePolicyExecutionAuditError("executed candidate/fallback attribution mismatch")
    if receipt.outcome == "UNKNOWN" or metadata.get("infrastructure_failure") is not False:
        raise InterferencePolicyExecutionAuditError("unknown/infrastructure execution is not audit evidence")
    reports = _load_reports(project)
    rebuilt = build_counterfactual_oracle_receipt(
        fixed_constraint_check_verdicts(reports), evidence_digest=_digest(reports))
    if metadata.get("counterfactual_oracle") != rebuilt:
        raise InterferencePolicyExecutionAuditError("fixed-constraint receipt does not replay retained reports")
    if receipt.outcome == "PASS" and (rebuilt.get("complete") is not True or
                                       set(rebuilt["checks"].values()) != {"PASS"}):
        raise InterferencePolicyExecutionAuditError("PASS contradicts retained fixed-constraint oracles")
    if reports.get("ppa") and stable_dumps(_physical(receipt)) != stable_dumps(reports["ppa"]):
        raise InterferencePolicyExecutionAuditError("physical receipt does not replay retained PPA")
    if _source_content_binding(project, _source_inputs(case["source_inputs"])) != metadata.get("source_content_digest"):
        raise InterferencePolicyExecutionAuditError("retained design source content drift")
    observed_config = _parse_config(project / "constraints" / "config.mk")
    if metadata.get("config_after_digest") != _digest(observed_config):
        raise InterferencePolicyExecutionAuditError("executed config digest mismatch")
    expected_edits = candidate.concrete_action["payload"]["config_edits"] if selected else {}
    expected_core = expected_edits.get("CORE_UTILIZATION",
                                       case["flow_config_observation"]["values"]["CORE_UTILIZATION"])
    if metadata.get("config_edits") != expected_edits or observed_config.get("CORE_UTILIZATION") != expected_core:
        raise InterferencePolicyExecutionAuditError("retained effective action differs from actual policy")
    files = [{"path": str(path), "sha256": _sha256(path)}
             for path in sorted((project / "reports").glob("*.json")) if path.is_file()]
    return {"execution_project_dir": str(project), "execution_digest": receipt.execution_digest,
            "source": receipt.source, "effective_core_utilization": expected_core,
            "fixed_constraint_complete": rebuilt["complete"], "report_files": files}


def _memory_harm(pair, arm):
    memory = pair.arm_receipts[arm]
    if memory.source != "structured_memory":
        raise InterferencePolicyExecutionAuditError("harm witness must name executed memory")
    if memory.outcome == "FAIL":
        return {"harm": True, "authority": "observed fixed-constraint failure", "utility_receipt": None}
    utility = replay_orfs_paired_utility_receipt(pair.arm_receipts["NO_MEMORY"], memory)
    return {"harm": utility["observation"]["status"] == "FAIL",
            "authority": "replayed prospective paired utility contract", "utility_receipt": utility}


def _case_gate(pairs, *, purpose):
    before, after, removed = [pairs[view] for view in VIEWS]
    arms = {}
    for arm in ("APPLICABILITY_GATED", "CAUSAL_NO_SKILL"):
        mt, post, ablated = [pair.arm_receipts[arm] for pair in (before, after, removed)]
        if purpose == "non_target":
            physical = _zero_regression(_physical(mt), _physical(post))
            physical_remove = _zero_regression(_physical(mt), _physical(ablated))
            gate = (mt.source == post.source == ablated.source == "structured_memory" and
                    mt.action_digest == post.action_digest == ablated.action_digest and
                    mt.outcome == post.outcome == ablated.outcome == "PASS" and
                    set(post.created_regressions) <= set(mt.created_regressions) and
                    set(ablated.created_regressions) <= set(mt.created_regressions) and
                    physical["non_regressing"] and physical_remove["non_regressing"])
            arms[arm] = {"passed": gate, "relative_physical_audit": physical,
                         "remove_delta_physical_audit": physical_remove,
                         "existing_mt_regressions_retained": list(mt.created_regressions),
                         "absolute_utility_safety_claimed": False}
        else:
            before_harm, removed_harm = _memory_harm(before, arm), _memory_harm(removed, arm)
            physical = _zero_regression(_physical(after.arm_receipts["NO_MEMORY"]), _physical(post))
            gate = (before_harm["harm"] and removed_harm["harm"] and
                    before.routing_decision in {"CONSIDER", "APPLY"} and
                    after.routing_decision == "INAPPLICABLE" and
                    removed.routing_decision == before.routing_decision and
                    post.source == "no_memory" and post.outcome == "PASS" and not post.created_regressions and
                    mt.action_digest == ablated.action_digest and mt.candidate_digest == ablated.candidate_digest and
                    physical["non_regressing"])
            arms[arm] = {"passed": gate, "Mt_harm": before_harm, "remove_delta_harm": removed_harm,
                         "post_fallback_vs_executed_baseline": physical,
                         "post_fallback_attributed_to_memory_action": False}
    return {"passed": all(row["passed"] for row in arms.values()), "arms": arms}


def audit_policy_execution(policy_freeze, reports_dir, *, purpose, output):
    freeze_path, report_dir, output_path = (Path(path).expanduser().resolve() for path in (
        policy_freeze, reports_dir, output))
    if purpose not in {"target_replay", "non_target", "heldout"}:
        raise InterferencePolicyExecutionAuditError("unknown replay purpose")
    if output_path.exists():
        raise InterferencePolicyExecutionAuditError("audit output must be new")
    freeze = _load_json(freeze_path, "policy freeze")
    _self_digest(freeze, "report_digest")
    if (freeze.get("version") != "p13-interference-policy-views-freeze-v1" or
            set(freeze.get("policy_manifests", {})) != set(VIEWS) or
            freeze.get("eda_executed") is not False or
            freeze.get("exact_remove_delta_policy_restored") is not True or
            freeze.get("canonical_memory_mutation") != "none" or
            freeze.get("production_runtime_imported") is not False):
        raise InterferencePolicyExecutionAuditError("policy freeze boundary mismatch")
    cohorts, report_refs, inputs_by_view, retained, paths = {}, {}, {}, {}, []
    for view in VIEWS:
        manifest_path, manifest = _reference(freeze["policy_manifests"][view], "policy manifest")
        inputs = validate_policy_inputs(manifest_path)
        inputs_by_view[view] = inputs
        report_path = report_dir / (view + "-report.json")
        report = _load_json(report_path, "actual policy execution report")
        if (report.get("report_version") != "p13-interference-policy-view-execution-report-v1" or
                report.get("policy_view") != view or report.get("manifest") != str(manifest_path) or
                report.get("manifest_sha256") != _sha256(manifest_path) or
                report.get("manifest_digest") != _digest(manifest) or
                report.get("input_authority_ref") != inputs["authority_ref"] or
                report.get("candidate_refs") != inputs["candidate_refs"] or
                report.get("learner_eligible") is not False or report.get("evaluation_only") is not True or
                report.get("canonical_memory_mutation") != "none" or
                report.get("production_runtime_imported") is not False or report.get("promotion_attempted") is not False):
            raise InterferencePolicyExecutionAuditError("executed policy report binding mismatch")
        cohort = OrfsPairedCohortReceipt.from_dict(report)
        normalized = {**cohort.to_dict(), "receipt_digest": cohort.receipt_digest}
        if (report.get("cohort_receipt") != normalized or report.get("cohort_receipt_digest") != cohort.receipt_digest or
                cohort.campaign_manifest_digest != _digest(manifest)):
            raise InterferencePolicyExecutionAuditError("typed policy cohort report mismatch")
        _, request = _reference(report["preexecution_request"], "preexecution request")
        _self_digest(request, "request_digest")
        if (request.get("manifest") != {"path": str(manifest_path), "sha256": _sha256(manifest_path),
                                       "digest": _digest(manifest)} or
                request.get("input_authority") != inputs["authority_ref"] or request.get("policy_view") != view):
            raise InterferencePolicyExecutionAuditError("prospective execution request mismatch")
        runner_pin = request.get("execution_runner_binding", {})
        if _sha256(Path(runner_pin["path"])) != runner_pin.get("sha256"):
            raise InterferencePolicyExecutionAuditError("executed wrapper source drift")
        retained[view] = {}
        by_id = {case["case_id"]: case for case in inputs["cases"]}
        if set(cohort.case_receipts) != set(by_id):
            raise InterferencePolicyExecutionAuditError("executed cohort case coverage mismatch")
        for cid, pair in cohort.case_receipts.items():
            case = by_id[cid]
            role = "held_out" if purpose == "heldout" else "validation"
            if case.get("dataset_split") != role or case.get("role") != role or case.get("learner_eligible") is not False:
                raise InterferencePolicyExecutionAuditError("replay purpose disagrees with prospective audit role")
            if pair.routing_receipt_id != inputs["routing"][cid].routing_receipt_id:
                raise InterferencePolicyExecutionAuditError("executed routing receipt mismatch")
            retained[view][cid] = {}
            for arm in P12_ARMS:
                receipt = pair.arm_receipts[arm]
                observed = _retained_arm(receipt, inputs["candidates"][cid][arm], case, arm)
                retained[view][cid][arm] = observed
                paths.append(observed["execution_project_dir"])
                if receipt.source == "structured_memory" and receipt.outcome == "PASS":
                    replay_orfs_paired_utility_receipt(pair.arm_receipts["NO_MEMORY"], receipt)
        cohorts[view] = cohort
        report_refs[view] = {"path": str(report_path), "sha256": _sha256(report_path),
                             "cohort_receipt_digest": cohort.receipt_digest}
    _distinct_executions(paths)
    before = cohorts["Mt"]
    if before.lineage_count < 2:
        raise InterferencePolicyExecutionAuditError("replay requires two declared design lineages")
    if any(cohort.source_digests != before.source_digests or
           cohort.source_content_digests != before.source_content_digests or
           any(getattr(cohort, key) != getattr(before, key) for key in (
               "toolchain_digest", "oracle_digest", "platform_digest", "pdk_digest", "candidate_budget"))
           for cohort in cohorts.values()):
        raise InterferencePolicyExecutionAuditError("three executed cohorts changed frozen comparison inputs")
    _, plan = _reference(freeze["source_bound_plan"], "source-bound training plan")
    _, training_authority = _reference(plan["input_authority"], "training authority")
    training_hashes = {sha for row in training_authority["cases"].values() for sha in row["rtl_sha256"]}
    _, baseline_authority = _reference(inputs_by_view["Mt"]["authority"]["baseline_input_authority"],
                                       "baseline source authority")
    replay_hashes = {sha for row in baseline_authority["cases"].values() for sha in row["rtl_sha256"]}
    if purpose == "heldout" and replay_hashes & training_hashes:
        raise InterferencePolicyExecutionAuditError("held-out RTL content overlaps evolution training")
    if purpose == "target_replay" and replay_hashes != training_hashes:
        raise InterferencePolicyExecutionAuditError("target replay does not cover the declared original training RTL")
    gates = {cid: _case_gate({view: cohorts[view].case_receipts[cid] for view in VIEWS}, purpose=purpose)
             for cid in sorted(before.case_receipts)}
    payload = {"version": "p13-interference-policy-execution-audit-v1", "purpose": purpose,
               "campaign_id": freeze["campaign_id"], "passed": all(row["passed"] for row in gates.values()),
               "policy_freeze": {"path": str(freeze_path), "sha256": _sha256(freeze_path),
                                  "report_digest": freeze["report_digest"]},
               "source_bound_plan": freeze["source_bound_plan"], "executed_cohorts": report_refs,
               "case_gates": gates, "retained_execution_audit": retained,
               "independent_executed_workspaces": len(paths), "audit_source_binding": {
                   "path": str(Path(__file__).resolve()), "sha256": _sha256(Path(__file__).resolve())},
               "evidence_claim": "contract-scoped strategy safety, not new action-space capability",
               "formal_p13_receipt_bound": False, "formal_p14_attribution_created": False,
               "anti_forgetting_witness_created": False, "learner_support_imported": False,
               "strict_signoff_claimed": False, "statistical_generalization_claimed": False,
               "evaluation_only": True, "canonical_memory_mutation": "none",
               "production_runtime_imported": False, "promotion_attempted": False, "memory_docs_submitted": False}
    payload["receipt_id"] = "p13_interference_" + purpose + "_" + _digest(payload).split(":")[1][:24]
    payload["report_digest"] = _digest(payload)
    _write(output_path, payload)
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-freeze", type=Path, required=True)
    parser.add_argument("--reports-dir", type=Path, required=True)
    parser.add_argument("--purpose", choices=("target_replay", "non_target", "heldout"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = audit_policy_execution(args.policy_freeze, args.reports_dir, purpose=args.purpose, output=args.output)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"passed": report["passed"], "report_digest": report["report_digest"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
