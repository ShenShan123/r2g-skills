#!/usr/bin/env python3
"""Execute one prospective, source-bound interference policy view.

Accept only the runtime compiler's distinct authority schema. Preserve all
four real ORFS arms, including true no-memory fallbacks. This never calls a
model, imports learner support, mutates memory, or attempts promotion.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_p13_interference_policy_views import (
    AUTHORITY_VERSION, VIEWS, _audit_partition, _runtime_code_binding, _self_digest,
)
from scripts.build_p13_interference_source_bound_plan import _digest, _load_json, _reference, _sha256
from scripts.build_r3_orfs_interference_source_bound_inputs import _write
from scripts.run_orfs_p12_cohort import (
    _candidate_map, _manifest, _routing_map, _routing_report, _source_bound_authority, _terminal_report,
)
from tehm.evaluation import P12_ARMS, OrfsCandidateOracle, execute_orfs_paired_cohort
from tehm.ids import stable_dumps
from tehm.physical.utility_contracts import known_utility_contracts


class InterferencePolicyRunError(ValueError):
    """A post-policy input binding or executed source drifted."""


_CASE_POLICY_FIELDS = frozenset({
    "candidate_paths", "routing_decision", "routing_receipt_id", "no_skill_reason",
    "state_shift_receipt_id", "risk_receipt_id", "risk_receipt", "execution_artifacts_root",
})


def _verify_candidate_freeze(case_ids, frozen, refs, routing):
    if not isinstance(frozen, dict) or set(frozen) != case_ids:
        raise InterferencePolicyRunError("policy candidate coverage mismatch")
    for cid in sorted(case_ids):
        if set(frozen[cid]) != set(P12_ARMS) or frozen[cid]["NO_MEMORY"] is not None:
            raise InterferencePolicyRunError("policy candidate freeze must cover exactly four arms")
        selected = routing[cid].decision in {"CONSIDER", "APPLY"}
        for arm in P12_ARMS:
            current = refs[cid].get(arm)
            if current != frozen[cid][arm]:
                raise InterferencePolicyRunError(f"{cid}/{arm}: candidate drifted from actual policy freeze")
            if arm == "ALWAYS_MEMORY" and current is None:
                raise InterferencePolicyRunError("forced memory candidate is required")
            if arm in {"APPLICABILITY_GATED", "CAUSAL_NO_SKILL"} and selected != (current is not None):
                raise InterferencePolicyRunError("gated fallback contradicts actual route")


def _verify_unchanged_case(runtime, baseline):
    def invariant(row):
        return {key: value for key, value in row.items() if key not in _CASE_POLICY_FIELDS}
    if stable_dumps(invariant(runtime)) != stable_dumps(invariant(baseline)):
        raise InterferencePolicyRunError("policy comparison changed source, environment, or audit role")


def validate_policy_inputs(manifest_path):
    manifest_path = Path(manifest_path).expanduser().resolve()
    manifest, cases, budget, min_lineages = _manifest(manifest_path)
    authority_path, authority = _reference(manifest.get("input_authority"), "policy input authority")
    digest = _self_digest(authority, "authority_digest")
    if (manifest["input_authority"].get("authority_digest") != digest or
            authority.get("version") != AUTHORITY_VERSION or
            authority.get("policy_view") not in VIEWS or
            manifest.get("policy_view") != authority["policy_view"] or
            manifest.get("campaign_id") != authority.get("campaign_id")):
        raise InterferencePolicyRunError("policy authority identity mismatch")
    if (any(authority.get(key) is not True for key in (
            "actual_router_used", "actual_selector_used", "actual_runtime_binding_used",
            "actual_candidate_builder_used", "staging_discarded", "evaluation_only")) or
            any(authority.get(key) is not False for key in (
                "eda_executed", "production_runtime_imported", "promotion_attempted",
                "applied_shadow_update_receipt_created", "memory_docs_submitted")) or
            authority.get("canonical_memory_mutation") != "none"):
        raise InterferencePolicyRunError("policy authority generation/boundary mismatch")
    runtime_binding = authority.get("runtime_generation_binding")
    if not isinstance(runtime_binding, dict):
        raise InterferencePolicyRunError("runtime generation source binding is required")
    _self_digest(runtime_binding, "binding_digest")
    if runtime_binding != _runtime_code_binding():
        raise InterferencePolicyRunError("runtime generation source drift")
    baseline_path, baseline = _reference(authority["baseline_manifest"], "baseline manifest")
    baseline, baseline_cases, baseline_budget, baseline_min = _manifest(baseline_path)
    case_ids = {case["case_id"] for case in cases}
    if case_ids != {case["case_id"] for case in baseline_cases}:
        raise InterferencePolicyRunError("baseline policy case coverage mismatch")
    baseline_authority, baseline_ref = _source_bound_authority(baseline_path, baseline, case_ids)
    if baseline_ref != authority["baseline_input_authority"]:
        raise InterferencePolicyRunError("baseline generation authority mismatch")
    partition = _audit_partition(baseline_authority, baseline_cases)
    if authority.get("learner_partition") != partition or manifest.get("learner_eligible") is not False:
        raise InterferencePolicyRunError("policy partition mismatch")
    if (budget != baseline_budget or min_lineages != baseline_min or
            any(manifest.get(key) != baseline.get(key) for key in (
                "toolchain_digest", "oracle_digest", "platform_digest", "pdk_digest",
                "utility_contract_id", "utility_contract_digest")) or
            authority.get("oracle_binding") != baseline_authority.get("oracle_binding")):
        raise InterferencePolicyRunError("policy comparison changed budget/toolchain/oracle/objective")
    _, plan = _reference(authority["source_bound_plan"], "source-bound plan")
    if (_self_digest(plan, "report_digest") != authority["source_bound_plan"].get("report_digest") or
            authority.get("source_database") != plan.get("source_database") or
            authority.get("parent_acquisitions") != plan.get("parent_acquisitions") or
            authority.get("source_database") != baseline_authority.get("source_database")):
        raise InterferencePolicyRunError("policy parent source mismatch")
    _, preflight = _reference(authority["shadow_view_preflight"], "shadow-view preflight")
    _self_digest(preflight, "report_digest")
    if (preflight.get("source_bound_plan") != authority["source_bound_plan"] or
            preflight.get("preflight_passed") is not True or
            preflight.get("child_content_digest") != authority.get("child_content_digest") or
            authority.get("utility_context_adapter") != plan.get("utility_context_adapter")):
        raise InterferencePolicyRunError("policy child/evaluation-view binding mismatch")
    source = Path(authority["source_database"]["path"])
    if _sha256(source) != authority["source_database"]["sha256"]:
        raise InterferencePolicyRunError("policy source database drift")
    routing_path = Path(authority["routing_decisions"]["path"])
    routing, routing_ref = _routing_map(routing_path, cases)
    if routing_ref != authority["routing_decisions"]:
        raise InterferencePolicyRunError("policy routing source drift")
    candidates, candidate_refs = {}, {}
    baseline_by_id = {case["case_id"]: case for case in baseline_cases}
    if set(authority.get("cases", {})) != case_ids:
        raise InterferencePolicyRunError("policy audit case coverage mismatch")
    for case in cases:
        cid = case["case_id"]
        _verify_unchanged_case(case, baseline_by_id[cid])
        candidates[cid], candidate_refs[cid] = _candidate_map(case, manifest_path)
        audit = authority["cases"][cid]
        route = {**routing[cid].to_dict(), "decision_digest": routing[cid].decision_digest}
        query = json.loads(stable_dumps(baseline_authority["cases"][cid]["query"]))
        query["query_plan"].update(plan["utility_context_adapter"]["facts"])
        forced = candidate_refs[cid].get("ALWAYS_MEMORY")
        historical = baseline_authority["candidate_freeze"][cid]
        if (audit.get("route") != route or audit.get("query") != query or
                audit.get("source_digest") != case.get("source_digest") or
                audit.get("lineage_id") != case.get("lineage_id") or
                forced is None or forced["candidate_digest"] != historical["candidate_digest"] or
                audit.get("candidate_freeze") != authority["candidate_freeze"][cid]):
            raise InterferencePolicyRunError("actual query/route/forced-history binding mismatch")
    _verify_candidate_freeze(case_ids, authority["candidate_freeze"], candidate_refs, routing)
    return {"manifest": manifest, "cases": cases, "budget": budget, "min_lineages": min_lineages,
            "authority": authority, "authority_ref": {"path": str(authority_path), "sha256": _sha256(authority_path),
                                                      "authority_digest": digest},
            "candidates": candidates, "candidate_refs": candidate_refs,
            "routing": routing, "routing_ref": routing_ref}


def run_policy_view(manifest, *, output):
    manifest_path, output_path = (Path(path).expanduser().resolve() for path in (manifest, output))
    request_path = output_path.with_name(output_path.name + ".preexecution.json")
    if output_path.exists() or request_path.exists():
        raise InterferencePolicyRunError("execution output and preexecution request must be new")
    inputs = validate_policy_inputs(manifest_path)
    payload = inputs["manifest"]
    execution_binding = {"path": str(Path(__file__).resolve()), "sha256": _sha256(Path(__file__).resolve())}
    request = {"version": "p13-interference-policy-execution-request-v1",
               "manifest": {"path": str(manifest_path), "sha256": _sha256(manifest_path), "digest": _digest(payload)},
               "input_authority": inputs["authority_ref"], "policy_view": payload["policy_view"],
               "execution_runner_binding": execution_binding, "provider_calls": 0,
               "evaluation_only": True, "canonical_memory_mutation": "none",
               "production_runtime_imported": False, "promotion_attempted": False, "memory_docs_submitted": False}
    request["request_digest"] = _digest(request)
    _write(request_path, request)  # Durable prospective pin, BEFORE the first flow.
    cohort = None
    try:
        cohort = execute_orfs_paired_cohort(
            inputs["cases"], inputs["candidates"], campaign_id=payload["campaign_id"],
            campaign_manifest_digest=_digest(payload), platform_digest=payload["platform_digest"],
            pdk_digest=payload["pdk_digest"], oracle=OrfsCandidateOracle(), budget=inputs["budget"],
            toolchain_digest=payload["toolchain_digest"], oracle_digest=payload["oracle_digest"],
            min_lineages=inputs["min_lineages"], utility_contract=known_utility_contracts()[payload["utility_contract_id"]]())
        validate_policy_inputs(manifest_path)
        if _sha256(Path(__file__).resolve()) != execution_binding["sha256"]:
            raise InterferencePolicyRunError("execution runner changed during EDA")
    except (OSError, TypeError, ValueError) as exc:
        terminal = _terminal_report(
            output_path=output_path, campaign_id=payload["campaign_id"], manifest_path=manifest_path,
            manifest_digest=_digest(payload), candidate_refs=inputs["candidate_refs"],
            routing=inputs["routing"], routing_meta=inputs["routing_ref"], input_authority_ref=inputs["authority_ref"],
            utility_contract_id=payload["utility_contract_id"], utility_contract_digest=payload["utility_contract_digest"], error=exc)
        if cohort is not None:
            # A failed post-execution binding audit must not erase flows that
            # really completed. Preserve them, but grant no audit/admission pass.
            terminal.update(status="EXECUTION_BINDING_FAILED", cohort_receipt_available=True,
                            cohort_receipt={**cohort.to_dict(), "receipt_digest": cohort.receipt_digest},
                            cohort_receipt_digest=cohort.receipt_digest, policy_view=payload["policy_view"],
                            post_execution_binding_passed=False,
                            preexecution_request={"path": str(request_path), "sha256": _sha256(request_path)})
            terminal.pop("terminal_report_digest", None)
            terminal["terminal_report_digest"] = _digest(terminal)
            _write(output_path, terminal)
        raise
    receipt = {**cohort.to_dict(), "receipt_digest": cohort.receipt_digest}
    report = {**receipt, "report_version": "p13-interference-policy-view-execution-report-v1",
              "policy_view": payload["policy_view"], "cohort_receipt": receipt,
              "cohort_receipt_digest": cohort.receipt_digest, "outcome_counts": cohort.outcome_counts,
              "manifest": str(manifest_path), "manifest_sha256": _sha256(manifest_path),
              "manifest_digest": _digest(payload), "input_authority_ref": inputs["authority_ref"],
              "candidate_refs": inputs["candidate_refs"], "routing_decisions": _routing_report(inputs["routing"]),
              "preexecution_request": {"path": str(request_path), "sha256": _sha256(request_path),
                                       "request_digest": request["request_digest"]},
              "learner_eligible": False, "evaluation_only": True, "canonical_memory_mutation": "none",
              "production_runtime_imported": False, "promotion_attempted": False, "memory_docs_submitted": False}
    _write(output_path, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = run_policy_view(args.manifest, output=args.output)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"cohort_receipt_digest": report["cohort_receipt_digest"],
                      "policy_view": report["policy_view"], "outcome_counts": report["outcome_counts"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
