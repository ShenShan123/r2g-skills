#!/usr/bin/env python3
"""Audit every local gate required before an Experiment 3 formal start."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
EXPECTED_SPLITS = {
    "a_propose": {"DRC": 3, "SETUP_TIMING": 3},
    "a_validation": {"DRC": 3, "SETUP_TIMING": 3},
    "b_heldout": {"DRC": 7, "SETUP_TIMING": 6},
}
EXPECTED_MODELS = {"gpt", "claude", "qwen"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _manifest_record_path(record: dict[str, Any], campaign: Path, repo: Path) -> Path:
    scope = record.get("scope")
    if scope == "campaign":
        return campaign / str(record["relative_path"])
    if scope == "repo":
        return repo / str(record["relative_path"])
    return Path(str(record.get("path_at_freeze") or record.get("path") or ""))


def _record(checks: list[dict[str, Any]], name: str, passed: bool, detail: Any) -> None:
    checks.append({"name": name, "passed": bool(passed), "detail": detail})


def _audit_split(campaign: Path, checks: list[dict[str, Any]]) -> dict[str, str]:
    split = read_json(campaign / "data/split_manifest.json")
    covariates = read_json(campaign / "data/baseline_covariates.json")
    rows = {row["task_id"]: row for row in covariates["tasks"]}
    assignments = split.get("assignments") or []
    assignment = {row["task_id"]: row["split"] for row in assignments}
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    source_splits: dict[str, set[str]] = defaultdict(set)
    for task_id, split_name in assignment.items():
        row = rows.get(task_id) or {}
        counts[split_name][str(row.get("failure_family"))] += 1
        source_splits[str(row.get("source_group"))].add(split_name)
    actual = {name: dict(counts[name]) for name in EXPECTED_SPLITS}
    _record(
        checks,
        "data_split_counts",
        len(rows) == 25
        and len(assignments) == 25
        and len(assignment) == 25
        and actual == EXPECTED_SPLITS,
        {"task_count": len(rows), "assignment_count": len(assignments), "counts": actual},
    )
    leaked = {key: sorted(value) for key, value in source_splits.items() if len(value) > 1}
    _record(checks, "source_group_isolation", not leaked, leaked)
    _record(
        checks,
        "split_covariate_binding",
        split.get("baseline_covariates_sha256") == sha256_file(campaign / "data/baseline_covariates.json")
        and split.get("selection_policy") == "baseline_only_group_aware_minimum_balance_error",
        {
            "selection_policy": split.get("selection_policy"),
            "baseline_covariates_sha256": split.get("baseline_covariates_sha256"),
        },
    )
    return assignment


def _audit_bundle(campaign: Path, checks: list[dict[str, Any]]) -> None:
    bundle = read_json(campaign / "inputs/bundle_manifest.json")
    failures: list[str] = []
    records = bundle.get("records") or []
    for record in records:
        baseline = (
            campaign
            / "inputs"
            / record["kind"]
            / record["task_id"]
            / "baseline"
        )
        input_path = baseline / "repair_family_probe_input.json"
        result_path = baseline / "repair_family_probe_result.json"
        metadata_path = baseline / "metadata.json"
        if not all(path.is_file() for path in (input_path, result_path, metadata_path)):
            failures.append(f"{record['task_id']}:missing_required_file")
            continue
        frozen = read_json(input_path)
        relative_sources = [
            *list(frozen.get("compilation_units") or []),
            *list(frozen.get("dependency_inputs") or []),
        ]
        portable = baseline / "rtl"
        missing_sources = [
            value
            for value in relative_sources
            if not (portable / Path(*Path(value).parts[1:])).is_file()
        ]
        if sha256_file(input_path) != record.get("input_sha256"):
            failures.append(f"{record['task_id']}:input_hash")
        if sha256_file(result_path) != record.get("result_sha256"):
            failures.append(f"{record['task_id']}:result_hash")
        if frozen.get("protected_task_digest") != record.get("protected_task_digest"):
            failures.append(f"{record['task_id']}:protected_digest")
        if missing_sources:
            failures.append(f"{record['task_id']}:source_closure")
    _record(
        checks,
        "portable_input_bundle",
        bundle.get("task_count") == 29 and len(records) == 29 and not failures,
        {"task_count": bundle.get("task_count"), "record_count": len(records), "failures": failures},
    )


def _audit_contexts(campaign: Path, assignment: dict[str, str], checks: list[dict[str, Any]]) -> None:
    root = campaign / "model_contexts"
    seen_a: set[str] = set()
    failures: list[str] = []
    for domain in ("drc_edge_pin", "setup_timing"):
        payload = read_json(root / "a_propose" / f"{domain}.json")
        if payload.get("split") != "a_propose" or payload.get("repair_outcomes_included") is not False:
            failures.append(f"a_propose/{domain}:metadata")
        for task in payload.get("tasks") or []:
            seen_a.add(task["task_id"])
            if assignment.get(task["task_id"]) != "a_propose" or task.get("failure_domain") != domain:
                failures.append(f"a_propose/{domain}:{task.get('task_id')}")
    expected_a = {task_id for task_id, split in assignment.items() if split == "a_propose"}
    if seen_a != expected_a:
        failures.append("a_propose:task_set")
    expected_b = {task_id for task_id, split in assignment.items() if split == "b_heldout"}
    seen_b: set[str] = set()
    for path in sorted((root / "b_heldout").glob("*.json")):
        payload = read_json(path)
        tasks = payload.get("tasks") or []
        if (
            payload.get("split") != "b_heldout"
            or payload.get("task_isolation") != "single_task_only"
            or payload.get("repair_outcomes_included") is not False
            or len(tasks) != 1
        ):
            failures.append(f"b_heldout/{path.name}:metadata")
            continue
        task_id = tasks[0].get("task_id")
        seen_b.add(task_id)
        if assignment.get(task_id) != "b_heldout":
            failures.append(f"b_heldout/{path.name}:cross_split")
    if seen_b != expected_b:
        failures.append("b_heldout:task_set")
    validation_contexts = list((root / "a_validation").glob("*.json")) if (root / "a_validation").exists() else []
    if validation_contexts:
        failures.append("a_validation_context_exists")
    _record(
        checks,
        "model_context_isolation",
        not failures,
        {"a_propose_tasks": len(seen_a), "b_heldout_tasks": len(seen_b), "failures": failures},
    )


def _audit_routes(campaign: Path, checks: list[dict[str, Any]]) -> None:
    path = campaign / "canary/routes/route_canary_summary.json"
    if not path.is_file():
        _record(checks, "model_route_canaries", False, "missing summary")
        return
    payload = read_json(path)
    results = payload.get("results") or []
    models = {row.get("model_key") for row in results if row.get("status") == "passed"}
    passed = (
        payload.get("status") == "passed"
        and payload.get("scoring") is False
        and models == EXPECTED_MODELS
        and (payload.get("data_disclosure") or {}).get("explicitly_authorized_by_user") is True
    )
    _record(checks, "model_route_canaries", passed, {"passing_models": sorted(models)})
    resolved_path = campaign / "resolved_model_routes.json"
    if not resolved_path.is_file():
        _record(checks, "resolved_model_routes", False, "missing resolved route binding")
        return
    resolved = read_json(resolved_path)
    rows = resolved.get("resolved_routes") or []
    resolved_models = {row.get("model_key") for row in rows}
    route_source = REPO / "docs/experiments/experiment3/experiment3_model_routes.json"
    resolved_passed = (
        resolved.get("credentials_persisted") is False
        and resolved_models == EXPECTED_MODELS
        and all(row.get("credential_present") is True for row in rows)
        and all(str(row.get("resolved_endpoint") or "").startswith("https://") for row in rows)
        and resolved.get("source_routes_sha256") == sha256_file(route_source)
    )
    _record(
        checks,
        "resolved_model_routes",
        resolved_passed,
        {
            "models": sorted(resolved_models),
            "credentials_present": {
                row.get("model_key"): row.get("credential_present") for row in rows
            },
        },
    )


def _audit_sentinels(campaign: Path, checks: list[dict[str, Any]]) -> None:
    path = campaign / "state/m0_clean_sentinel_execution.json"
    if not path.is_file():
        _record(checks, "clean_sentinel_replay", False, "missing execution state")
        return
    payload = read_json(path)
    results = payload.get("results") or []
    failures = []
    for result in results:
        attempts = result.get("attempts") or []
        if (
            result.get("status") != "completed"
            or result.get("strict_clean") is not True
            or len(attempts) != 1
            or attempts[0].get("verdict") != "sentinel_pass"
            or attempts[0].get("provenance_complete") is not True
        ):
            failures.append(result.get("task_id"))
    _record(
        checks,
        "clean_sentinel_replay",
        payload.get("planned_tasks") == 4
        and payload.get("completed_records") == 4
        and len(results) == 4
        and not failures,
        {"planned": payload.get("planned_tasks"), "completed": len(results), "failures": failures},
    )


def _audit_resources(repo: Path, checks: list[dict[str, Any]]) -> None:
    path = repo / "docs/experiments/experiment3/experiment3_resource_limits.json"
    payload = read_json(path)
    a = payload.get("a_propose") or {}
    pure = payload.get("pure_llm") or {}
    eda = payload.get("eda") or {}
    passed = (
        payload.get("status") == "frozen"
        and payload.get("confirmation") == "user_confirmed"
        and bool(payload.get("frozen_at"))
        and a.get("rounds") == 3
        and a.get("api_calls_per_model_per_domain") == 3
        and a.get("model_turns_per_model_total") == 6
        and a.get("raw_proposals_per_call") == 2
        and a.get("pooled_executions_per_domain_per_round") == 4
        and a.get("active_candidates_per_domain") == 8
        and a.get("max_output_tokens_per_call") == 2048
        and a.get("total_tokens_per_model") == 60000
        and pure.get("api_calls_per_task") == 3
        and pure.get("model_turns_per_model_per_task") == 3
        and pure.get("executed_attempts_per_task") == 3
        and pure.get("max_output_tokens_per_call") == 2048
        and pure.get("total_tokens_per_model_per_task") == 20000
        and eda.get("cores_per_trial") == 4
        and eda.get("timeout_seconds_per_trial") == 7200
        and eda.get("concurrent_trials_initial") == 2
        and eda.get("concurrent_trials_conditional_max") == 3
        and eda.get("minimum_free_disk_gib") == 30
        and eda.get("maximum_live_iowait_percent_for_scale_up") == 15
        and eda.get("compact_after_trial") is True
    )
    _record(checks, "resource_limits_frozen", passed, payload)


def _audit_deployment(campaign: Path, checks: list[dict[str, Any]]) -> None:
    payload = read_json(campaign / "deployment_203.json")
    canary = payload.get("canary") or {}
    tools = payload.get("toolchain") or {}
    passed = (
        payload.get("host") == "memlab203"
        and payload.get("hostname") == "memlab-gpu"
        and canary.get("result") == "strict_clean"
        and canary.get("environment_failure") is False
        and canary.get("execution_interrupted") is False
        and bool(tools.get("orfs_commit"))
        and bool(tools.get("openroad"))
        and bool(tools.get("yosys"))
    )
    _record(checks, "host_toolchain_canary", passed, {"host": payload.get("host"), "toolchain": tools})


def _audit_manifest(campaign: Path, repo: Path, checks: list[dict[str, Any]]) -> None:
    path = campaign / "execution_manifest.json"
    payload = read_json(path)
    stale = []
    for name, record in (payload.get("source_artifacts") or {}).items():
        source = _manifest_record_path(record, campaign, repo)
        if not source.is_file() or sha256_file(source) != record.get("sha256"):
            stale.append(name)
    runners = payload.get("runners") or {}
    if not runners:
        legacy = payload.get("runner") or {}
        runners = {"legacy_runner": legacy} if legacy else {}
    if not runners:
        stale.append("runners_missing")
    for name, runner in runners.items():
        runner_path = _manifest_record_path(runner, campaign, repo)
        if not runner_path.is_file() or sha256_file(runner_path) != runner.get("sha256"):
            stale.append(f"runner:{name}")
    passed = payload.get("status") == "frozen" and not payload.get("unresolved_freeze_items") and not stale
    _record(
        checks,
        "execution_manifest_frozen",
        passed,
        {"status": payload.get("status"), "unresolved": payload.get("unresolved_freeze_items"), "stale": stale},
    )


def audit_campaign(campaign: Path, repo: Path = REPO) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        assignment = _audit_split(campaign, checks)
        _audit_bundle(campaign, checks)
        _audit_contexts(campaign, assignment, checks)
        _audit_routes(campaign, checks)
        _audit_sentinels(campaign, checks)
        _audit_resources(repo, checks)
        _audit_deployment(campaign, checks)
        _audit_manifest(campaign, repo, checks)
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        _record(checks, "audit_execution", False, f"{type(exc).__name__}: {exc}")
    failed = [row["name"] for row in checks if not row["passed"]]
    return {
        "schema_version": "experiment3-formal-start-audit-1.0",
        "created_at": now(),
        "campaign_root": str(campaign.resolve()),
        "ready_for_formal_start": not failed,
        "failed_checks": failed,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=REPO)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit_campaign(args.campaign_root, args.repo_root)
    output = args.output or args.campaign_root / "formal_start_audit.json"
    write_json(output, report)
    print(output)
    print("ready_for_formal_start=" + str(report["ready_for_formal_start"]).lower())
    if report["failed_checks"]:
        print("failed_checks=" + ",".join(report["failed_checks"]))
    return 0 if report["ready_for_formal_start"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
