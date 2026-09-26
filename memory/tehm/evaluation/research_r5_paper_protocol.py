"""R5 experiment-layer denominator/split checks, NOT oracle or launch authority.

Inputs must come from independently audited, frozen campaigns. This module
does arithmetic and consistency checks; it never grants Memory promotion,
source independence, FINAL_TEST admission or model-call authorization.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections.abc import Mapping
from pathlib import Path

VIEWS = ("m-minus", "m-plus", "mremove", "transform-only")
VERDICTS = {"PASS", "FAIL", "UNKNOWN"}
ROLES = {"PILOT_TRANSFER", "FINAL_TEST"}


class ProtocolError(ValueError):
    pass


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise ProtocolError(reason)


def repository_id(value: str) -> str:
    require(isinstance(value, str) and bool(value.strip()), "repository identity missing")
    value = value.strip().lower()
    for prefix in ("https://github.com/", "http://github.com/", "git@github.com:"):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    value = value.rstrip("/").removesuffix(".git")
    require(len(value.split("/")) == 2 and all(value.split("/")), "invalid repository identity")
    require(not any(x in value for x in ("..", "?", "#", "\\", ":")), "unsafe repository identity")
    return value


def validate_plan(plan: Mapping) -> dict:
    require(plan.get("schema") == "tehm-r5-paper-measurement-plan-v1", "plan schema")
    require(plan.get("role") == "METHODS_FIXED_FINAL_DATA_UNFROZEN", "plan role")
    require(plan.get("frozen_method_software") == "2ce921a599c406ab63331561a182d6f4f1bef8cb", "frozen software identity")
    require(plan.get("frozen_memory_report_digest") == "sha256:f31072bf2c233974c9fbbf4bb4c30539942f07a57a5b6b7e9022611e90f3206b", "frozen Memory identity")
    require(plan.get("views") == list(VIEWS), "paired views/catalog mismatch")
    require(plan.get("primary_metric") == "VerifiedRepair@B_all_registered_tasks", "success denominator")
    require(plan.get("statistical_unit") == "audited_source_group", "independence unit")
    require(plan.get("unknown_counts_as_success") is False, "UNKNOWN cannot be success")
    require(plan.get("drop_abstentions") is False, "abstentions must stay in denominator")
    require(plan.get("online_target_learning") is False, "target learning forbidden")
    require(type(plan.get("model_calls_authorized")) is int and plan.get("model_calls_authorized") == 0 and
            type(plan.get("model_tokens_authorized")) is int and plan.get("model_tokens_authorized") == 0,
            "this protocol does not authorize providers")
    require(plan.get("positive_effect_required_to_report") is False, "outcome-driven stopping")
    require(plan.get("controller_kind") == "deterministic_controlled_action_not_agent", "controller claim")
    require(plan.get("native_subtests_are_tasks") is False, "subtest inflation")
    require(plan.get("final_tasks") == [], "FINAL tasks require a separate audited freeze, not edits to this plan")
    require(type(plan.get("next_scope_screen_cap")) is int and plan.get("next_scope_screen_cap") == 2, "bounded next screen")
    require(type(plan.get("candidate_budget")) is int and plan.get("candidate_budget") == 1, "fixed action budget")
    require(plan.get("effect_target") is None and plan.get("power_claim") is None, "unsupported target/power claim")
    repos = [repository_id(x) for x in plan.get("prior_repositories_requiring_scope_review", [])]
    require(len(repos) == len(set(repos)) and len(repos) > 0, "prior-source ledger missing/duplicated")
    return {"manifest_consistent": True, "final_test_ready": False,
            "execution_authorized": False, "model_calls_authorized": 0,
            "blockers": ["final_source_scopes_not_admitted", "final_manifest_not_frozen",
                         "source_group_sample_size_not_established"],
            "prior_repositories_requiring_scope_review": len(repos)}


def screen_source(plan: Mapping, candidate: Mapping) -> dict:
    """A negative/review screen only: new owner names cannot grant independence."""
    validate_plan(plan)
    repo = repository_id(candidate.get("repository"))
    known = {repository_id(x) for x in plan["prior_repositories_requiring_scope_review"]}
    roles = candidate.get("observed_roles", [])
    require(isinstance(roles, list) and all(isinstance(x, str) for x in roles), "observed role list")
    prior_roles = set(roles)
    reasons = []
    if prior_roles & {"QUALIFICATION", "DEV", "TRAIN", "PILOT_TRANSFER", "FINAL_TEST", "HISTORICAL"}:
        reasons.append("observed_scope_cannot_be_unseen_final")
    if repo in known:
        reasons.append("prior_repository_requires_scope_level_exposure_review")
    if any(repository_id(x) in known for x in candidate.get("related_repositories", [])):
        reasons.append("known_related_source_requires_lineage_review")
    if not reasons:
        reasons.append("new_repository_still_requires_raw_lineage_and_oracle_review")
    return {"repository": repo, "final_test_ready": False, "execution_authorized": False,
            "disposition": "REJECT_OBSERVED_SCOPE" if "observed_scope_cannot_be_unseen_final" in reasons else "REVIEW_REQUIRED",
            "reasons": reasons}


def summarize(manifest: Mapping, results: list[Mapping]) -> dict:
    """Aggregate reviewed method rows at task/view and source-group grain.

    Missing rows remain UNKNOWN non-successes. Duplicate/foreign arms, code
    drift, changed budgets, incomplete required verdicts and extra oracle
    keys are rejected. No native test count, repetition or control inflates N.
    """
    require(manifest.get("role") in ROLES, "not a method-evaluation manifest")
    tasks = manifest.get("tasks")
    require(isinstance(tasks, list) and bool(tasks), "empty method task denominator")
    require(manifest.get("views") == list(VIEWS), "comparison views mismatch")
    shared = manifest.get("shared_contract_digest")
    require(isinstance(shared, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", shared) is not None,
            "shared contract digest missing")
    budget = manifest.get("candidate_budget")
    require(type(budget) is int and budget > 0, "invalid candidate budget")
    by_task = {}
    for task in tasks:
        require(isinstance(task, Mapping), "task record")
        task_id = task.get("task_id")
        require(isinstance(task_id, str) and bool(task_id) and task_id not in by_task, "duplicate/invalid task")
        require(task.get("role") == manifest["role"], "mixed task roles")
        require(isinstance(task.get("source_group"), str) and bool(task["source_group"]), "missing source group")
        required = task.get("required_oracles")
        require(isinstance(required, list) and bool(required) and len(required) == len(set(required)), "oracle obligation set")
        require({"target", "preservation", "native"} <= set(required), "missing required R5 oracle")
        by_task[task_id] = task
    rows = {}
    for row in results:
        require(isinstance(row, Mapping), "method result row")
        key = (row.get("task_id"), row.get("view"))
        require(key[0] in by_task and key[1] in VIEWS, "foreign task/view (controls are separate)")
        require(key not in rows, "duplicate task/view is not an independent sample")
        require(row.get("shared_contract_digest") == shared, "software/oracle/visibility drift")
        require(type(row.get("candidate_budget")) is int and row.get("candidate_budget") == budget, "unequal candidate budget")
        attempts = row.get("candidates_attempted")
        require(type(attempts) is int and 0 <= attempts <= budget, "candidate budget exceeded")
        verdicts = row.get("verdicts")
        require(isinstance(verdicts, Mapping) and set(verdicts) == set(by_task[key[0]]["required_oracles"]),
                "required oracle missing/extra")
        require(all(type(v) is str and v in VERDICTS for v in verdicts.values()), "invalid verdict")
        require(type(row.get("source_changed")) is bool, "missing action identity")
        require(not row["source_changed"] or attempts > 0, "changed source without an attempt")
        seconds = row.get("oracle_wall_seconds")
        require(seconds is None or (type(seconds) in (int, float) and math.isfinite(seconds) and seconds >= 0),
                "invalid cost; unavailable cost must be null")
        rows[key] = row
    per_view = {}; groups = sorted({t["source_group"] for t in tasks})
    outcomes = {}
    for view in VIEWS:
        successes = unknown = missing = harmful = 0
        group_values = {g: [] for g in groups}
        costs = []
        for task_id, task in by_task.items():
            row = rows.get((task_id, view))
            if row is None:
                passed = False; unknown += 1; missing += 1
            else:
                passed = all(v == "PASS" for v in row["verdicts"].values())
                unknown += int(any(v == "UNKNOWN" for v in row["verdicts"].values()))
                harmful += int(row["source_changed"] and row["verdicts"]["preservation"] == "FAIL")
                costs.append(row["oracle_wall_seconds"])
            successes += int(passed)
            group_values[task["source_group"]].append(int(passed))
            outcomes[(task_id, view)] = int(passed)
        group_rates = {g: sum(v)/len(v) for g,v in group_values.items()}
        per_view[view] = {"successes": successes, "registered_tasks": len(tasks),
                          "VerifiedRepair@B": successes/len(tasks), "unknown_tasks": unknown,
                          "missing_arms": missing, "harmful_changed_tasks": harmful,
                          "source_group_rates": group_rates, "macro_source_group_rate": sum(group_rates.values())/len(groups),
                          "recorded_oracle_wall_seconds": sum(v for v in costs if v is not None),
                          "oracle_cost_complete": len(costs)==len(tasks) and all(v is not None for v in costs)}
    delta = {other: {g: sum(outcomes[(t["task_id"],"m-plus")]-outcomes[(t["task_id"],other)]
                             for t in tasks if t["source_group"]==g)/sum(t["source_group"]==g for t in tasks)
                    for g in groups} for other in ("m-minus","mremove","transform-only")}
    return {"schema":"tehm-r5-reviewed-method-summary-v1", "role":manifest["role"],
            "registered_tasks":len(tasks),"declared_source_groups":len(groups),"per_view":per_view,
            "paired_group_delta_mplus_minus":delta, "raw_semantic_audit_required":True,
            "source_independence_granted":False,"statistical_power_established":False,
            "online_evolution_established":False,"model_comparison_established":False}


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--candidate',type=Path)
    args=parser.parse_args()
    plan=json.loads(args.plan.read_text())
    result=screen_source(plan,json.loads(args.candidate.read_text())) if args.candidate else validate_plan(plan)
    print(json.dumps(result,indent=2,sort_keys=True))
    return 0

if __name__=='__main__': raise SystemExit(main())
