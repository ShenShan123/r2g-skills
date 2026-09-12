#!/usr/bin/env python3
"""Freeze actual Mt / proposed evaluation view / remove-delta policy inputs.

No EDA, learning, anti-forgetting witness, or formal P13 update is performed.
The proposed child is activated only through the audited lifecycle in isolated
RAM. A gated fallback is emitted only from an actual non-memory routing result.
The existing P12 v1 authority is NOT forged to describe these new policy views.
"""
from __future__ import annotations

import argparse
import copy
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_p13_interference_shadow_view import (
    _activate_evaluation_child, _query, _route_candidate,
)
from contracts import MEMORY_ROUTING_DECISIONS
from scripts.build_p13_interference_source_bound_plan import (
    _digest, _load_json, _reference, _sha256, build_source_bound_plan,
)
from scripts.build_r3_orfs_interference_source_bound_inputs import _toolchain, _write
from scripts.run_orfs_p12_cohort import (
    _candidate_map, _manifest, _routing_map, _source_bound_authority,
    _verify_source_bound_bindings,
)
from tehm.evaluation import P12_ARMS
from tehm.evolution.anti_forgetting import raw_evidence_digest
from tehm.evolution.apply_update import _connection_digest
from tehm.evolution.interference_revision import MemoryInterferenceEvolutionProposal
from tehm.ids import stable_dumps
from tehm.knowledge import MechanismKnowledge
from tehm.verified_execution import scoped_learning_replay


VIEWS = ("Mt", "Mt_plus_delta", "Mt_plus_delta_minus_delta")
AUTHORITY_VERSION = "p13-interference-policy-view-input-authority-v1"


class InterferencePolicyViewError(ValueError):
    """Source, partition, actual policy generation, or rollback failed."""


def _self_digest(payload, field):
    unsigned = dict(payload)
    supplied = unsigned.pop(field, None)
    if supplied != _digest(unsigned):
        raise InterferencePolicyViewError(f"{field} mismatch")
    return supplied


def _audit_partition(authority, cases):
    partition = authority.get("learner_partition")
    if not isinstance(partition, dict) or partition.get("learner_eligible") is not False:
        raise InterferencePolicyViewError("policy replay requires explicit non-learner partition")
    if set(partition.get("cases", {})) != {case["case_id"] for case in cases}:
        raise InterferencePolicyViewError("policy replay partition coverage mismatch")
    for case in cases:
        row = partition["cases"][case["case_id"]]
        if (row.get("learner_eligible") is not False or
                row.get("dataset_split") not in {"validation", "held_out", "calibration"} or
                row.get("role") != row.get("dataset_split") or
                any(case.get(key) != row.get(key) for key in row)):
            raise InterferencePolicyViewError("policy replay cannot infer or rewrite learner roles")
    return partition


def _policy_candidates(route, current, historical):
    if historical is None:
        raise InterferencePolicyViewError("forced historical-memory candidate is required")
    if route.decision not in MEMORY_ROUTING_DECISIONS:
        raise InterferencePolicyViewError("unknown top-level routing decision")
    selected = route.decision in {"CONSIDER", "APPLY"}
    if selected != (current is not None):
        raise InterferencePolicyViewError("actual route/candidate contradiction")
    return {"NO_MEMORY": None, "ALWAYS_MEMORY": historical,
            "APPLICABILITY_GATED": current, "CAUSAL_NO_SKILL": current}


def _runtime_code_binding():
    # Bind the complete TEHM Python runtime corpus, not only the new compiler.
    # This is a separate generation pin; it does not rewrite the old EDA oracle.
    paths = {ROOT / "contracts.py", *ROOT.joinpath("tehm").rglob("*.py")}
    for name in (
            "build_r3_orfs_interference_source_bound_inputs.py",
            "build_p13_interference_reason_bundle.py", "build_p13_shadow_trigger_report.py",
            "build_p13_interference_source_bound_plan.py", "audit_p13_interference_shadow_view.py",
            "build_p13_interference_policy_views.py", "run_orfs_interference_policy_view.py",
            "run_orfs_p12_cohort.py"):
        paths.add(ROOT / "scripts" / name)
    binding = {"version": "p13-interference-runtime-generation-binding-v1",
               "files": [{"path": str(path), "sha256": _sha256(path)} for path in sorted(paths)]}
    binding["binding_digest"] = _digest(binding)
    return binding


def build_policy_views(source_bound_plan, shadow_view_preflight, baseline_manifest, *, output_dir):
    plan_path, preflight_path, manifest_path, output = (
        Path(path).expanduser().resolve() for path in (
            source_bound_plan, shadow_view_preflight, baseline_manifest, output_dir))
    if output.exists():
        raise InterferencePolicyViewError("policy output directory must be new")
    plan = _load_json(plan_path, "source-bound plan")
    plan_digest = _self_digest(plan, "report_digest")
    if plan.get("physical_utility_contract_bound") is not True:
        raise InterferencePolicyViewError("plan lacks physical utility contract binding")
    _reference(plan["reason_bundle"], "reason bundle")
    with tempfile.TemporaryDirectory(prefix="tehm-policy-plan-replay-") as tmp:
        replay = build_source_bound_plan(plan["reason_bundle"]["path"], output=Path(tmp) / "plan.json")
    if stable_dumps(replay) != stable_dumps(plan):
        raise InterferencePolicyViewError("source-bound plan does not cold-replay")
    preflight = _load_json(preflight_path, "shadow-view preflight")
    _self_digest(preflight, "report_digest")
    plan_ref = {"path": str(plan_path), "sha256": _sha256(plan_path), "report_digest": plan_digest}
    if (preflight.get("source_bound_plan") != plan_ref or
            any(preflight.get(key) is not True for key in (
                "preflight_passed", "raw_evidence_preserved", "source_database_unchanged", "staging_discarded")) or
            preflight.get("applied_shadow_update_receipt_created") is not False or
            preflight.get("canonical_memory_mutation") != "none" or
            preflight.get("production_runtime_imported") is not False):
        raise InterferencePolicyViewError("preflight source or boundary mismatch")
    manifest, cases, budget, min_lineages = _manifest(manifest_path)
    authority, authority_ref = _source_bound_authority(
        manifest_path, manifest, {case["case_id"] for case in cases})
    if authority is None:
        raise InterferencePolicyViewError("actual baseline generation authority is required")
    partition = _audit_partition(authority, cases)
    routes_path = Path(authority["routing_decisions"]["path"])
    baseline_routes, routing_meta = _routing_map(routes_path, cases)
    loaded = {case["case_id"]: _candidate_map(case, manifest_path) for case in cases}
    _verify_source_bound_bindings(authority, authority_ref, cases,
                                  {cid: pair[1] for cid, pair in loaded.items()}, baseline_routes, routing_meta)
    _, preregistration = _reference(authority["preregistration"], "audit preregistration")
    _toolchain(preregistration, require_oracle_binding=True)
    if (authority.get("source_database") != plan.get("source_database") or
            authority.get("parent_acquisitions") != plan.get("parent_acquisitions")):
        raise InterferencePolicyViewError("policy views must use the exact plan parent source")
    utility_facts = plan["utility_context_adapter"]["facts"]
    if any(manifest.get(key) != utility_facts.get(key) for key in (
            "utility_contract_id", "utility_contract_digest")):
        raise InterferencePolicyViewError("policy utility adapter differs from frozen audit objective")
    _, training_authority = _reference(plan["input_authority"], "training input authority")
    _, acquisition = _reference(plan["parent_acquisitions"], "parent acquisitions")
    parent = MechanismKnowledge.from_dict(plan["parent_knowledge"])
    proposal = MemoryInterferenceEvolutionProposal.from_dict(plan["proposal"])
    source = Path(plan["source_database"]["path"])
    if _sha256(source) != plan["source_database"]["sha256"]:
        raise InterferencePolicyViewError("source database changed")
    frozen = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    ram = sqlite3.connect(":memory:")
    ram.row_factory = sqlite3.Row
    try:
        frozen.backup(ram)
    finally:
        frozen.close()
    logical_before, raw_before = _connection_digest(ram), raw_evidence_digest(ram)
    if logical_before != plan["source_database"]["logical_digest"]:
        ram.close()
        raise InterferencePolicyViewError("source logical digest mismatch")
    generated = {view: {} for view in VIEWS}
    try:
        with scoped_learning_replay(
                ram, campaign_id=plan["parent_training_campaign"], acquisitions=acquisition["acquisitions"],
                expected_digest=acquisition["digest"]):
            queries = {cid: _query(row["query"], utility_facts) for cid, row in authority["cases"].items()}
            for cid, query in sorted(queries.items()):
                route, candidate = _route_candidate(ram, query)
                if (candidate is None or
                        candidate.candidate_digest != loaded[cid][0]["ALWAYS_MEMORY"].candidate_digest):
                    raise InterferencePolicyViewError(f"{cid}: utility adapter changed frozen Mt candidate")
                generated["Mt"][cid] = (route, _policy_candidates(route, candidate, candidate))
            ram.execute("SAVEPOINT policy_delta")
            child, revision, ledger, shadow_state, evaluation_state = _activate_evaluation_child(
                ram, parent, proposal, plan["resolved_source_state"]["scope"], training_authority)
            if (child.to_dict() != preflight["child_knowledge"] or
                    ledger.to_dict() != preflight["evaluation_view_authority"] or
                    revision.to_dict() != preflight["revision"]):
                raise InterferencePolicyViewError("evaluation-view child/lifecycle differs from audited preflight")
            for cid, query in sorted(queries.items()):
                route, candidate = _route_candidate(ram, query)
                generated["Mt_plus_delta"][cid] = (
                    route, _policy_candidates(route, candidate, generated["Mt"][cid][1]["ALWAYS_MEMORY"]))
            if raw_evidence_digest(ram) != raw_before:
                raise InterferencePolicyViewError("evaluation view changed canonical raw evidence")
            ram.execute("ROLLBACK TO policy_delta")
            ram.execute("RELEASE policy_delta")
            if _connection_digest(ram) != logical_before:
                raise InterferencePolicyViewError("remove-delta did not restore exact logical source")
            for cid, query in sorted(queries.items()):
                route, candidate = _route_candidate(ram, query)
                before_route, before_candidates = generated["Mt"][cid]
                if (route.to_dict() != before_route.to_dict() or candidate is None or
                        candidate.to_dict() != before_candidates["ALWAYS_MEMORY"].to_dict()):
                    raise InterferencePolicyViewError(f"{cid}: remove-delta did not restore exact Mt policy")
                generated["Mt_plus_delta_minus_delta"][cid] = (route, _policy_candidates(route, candidate, candidate))
    finally:
        ram.close()
    if _sha256(source) != plan["source_database"]["sha256"]:
        raise InterferencePolicyViewError("source database changed during compilation")
    code_binding = _runtime_code_binding()
    boundary = {"evaluation_only": True, "canonical_memory_mutation": "none",
                "production_runtime_imported": False, "promotion_attempted": False,
                "memory_docs_submitted": False, "eda_executed": False}
    refs, view_routes = {}, {}
    for view in VIEWS:
        view_dir = output / view
        runtime_cases, freezes, audit_cases, routes = [], {}, {}, {}
        for index, case in enumerate(cases):
            cid = case["case_id"]
            route, candidates = generated[view][cid]
            paths, arm_refs = {}, {}
            for arm in P12_ARMS:
                candidate = candidates[arm]
                if candidate is None:
                    paths[arm], arm_refs[arm] = None, None
                else:
                    path = view_dir / "candidates" / f"{index:02d}-{arm}.json"
                    _write(path, candidate.to_dict())
                    paths[arm] = str(path)
                    arm_refs[arm] = {"path": str(path), "sha256": _sha256(path),
                                     "candidate_id": candidate.candidate_id, "candidate_digest": candidate.candidate_digest}
            runtime_case = copy.deepcopy(case)
            runtime_case.update(candidate_paths=paths, routing_decision=route.decision,
                                routing_receipt_id=route.routing_receipt_id,
                                no_skill_reason=route.no_skill_reason,
                                state_shift_receipt_id=route.state_shift_receipt_id,
                                risk_receipt_id=route.risk_receipt_id, risk_receipt=route.risk_receipt,
                                execution_artifacts_root=str(view_dir / "execution" / f"{index:02d}"))
            runtime_cases.append(runtime_case)
            freezes[cid] = arm_refs
            routes[cid] = {**route.to_dict(), "decision_digest": route.decision_digest}
            audit_cases[cid] = {"source_digest": case["source_digest"], "lineage_id": case["lineage_id"],
                               "query": queries[cid].to_dict(), "route": routes[cid], "candidate_freeze": arm_refs}
        route_path = view_dir / "routing-decisions.json"
        _write(route_path, {"routes": routes})
        route_ref = {"path": str(route_path), "sha256": _sha256(route_path)}
        campaign_id = manifest["campaign_id"] + ":" + view
        view_authority = {
            "version": AUTHORITY_VERSION, "campaign_id": campaign_id, "policy_view": view,
            "baseline_input_authority": authority_ref,
            "baseline_manifest": {"path": str(manifest_path), "sha256": _sha256(manifest_path)},
            "source_bound_plan": plan_ref,
            "shadow_view_preflight": {"path": str(preflight_path), "sha256": _sha256(preflight_path)},
            "runtime_generation_binding": code_binding,
            "utility_context_adapter": plan["utility_context_adapter"],
            "source_database": plan["source_database"], "parent_acquisitions": plan["parent_acquisitions"],
            "utility_contract_id": manifest["utility_contract_id"],
            "utility_contract_digest": manifest["utility_contract_digest"],
            "oracle_binding": authority["oracle_binding"], "learner_partition": partition,
            "candidate_freeze": freezes, "cases": audit_cases, "routing_decisions": route_ref,
            "child_content_digest": child.content_digest,
            "actual_router_used": True, "actual_selector_used": True,
            "actual_runtime_binding_used": True, "actual_candidate_builder_used": True,
            "staging_discarded": True, "applied_shadow_update_receipt_created": False, **boundary,
        }
        view_authority["authority_digest"] = _digest(view_authority)
        authority_path = view_dir / "input-authority.json"
        _write(authority_path, view_authority)
        view_manifest = copy.deepcopy(manifest)
        view_manifest.update(campaign_id=campaign_id, cases=runtime_cases, learner_eligible=False,
                             policy_view=view, candidate_budget=budget, min_lineages=min_lineages,
                             input_authority={"path": str(authority_path), "sha256": _sha256(authority_path),
                                              "authority_digest": view_authority["authority_digest"]}, **boundary)
        view_path = view_dir / "p12-manifest.json"
        _write(view_path, view_manifest)
        refs[view] = {"path": str(view_path), "sha256": _sha256(view_path), "digest": _digest(view_manifest)}
        view_routes[view] = {cid: {"decision": route.decision,
                                 "candidate_digests": {arm: None if cand is None else cand.candidate_digest
                                                       for arm, cand in arms.items()}}
                             for cid, (route, arms) in generated[view].items()}
    report = {
        "version": "p13-interference-policy-views-freeze-v1", "campaign_id": manifest["campaign_id"],
        "source_bound_plan": plan_ref, "policy_manifests": refs, "policy_routes": view_routes,
        "runtime_generation_binding": code_binding, "learner_partition": partition,
        "child_content_digest": child.content_digest, "source_database_unchanged": True,
        "raw_evidence_preserved": True, "exact_remove_delta_policy_restored": True,
        "staging_discarded": True, "anti_forgetting_witness_created": False,
        "applied_shadow_update_receipt_created": False, **boundary,
    }
    report["report_digest"] = _digest(report)
    _write(output / "policy-views-freeze-report.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-bound-plan", type=Path, required=True)
    parser.add_argument("--shadow-view-preflight", type=Path, required=True)
    parser.add_argument("--baseline-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_policy_views(args.source_bound_plan, args.shadow_view_preflight,
                                    args.baseline_manifest, output_dir=args.output_dir)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"report_digest": report["report_digest"], "policy_routes": report["policy_routes"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
