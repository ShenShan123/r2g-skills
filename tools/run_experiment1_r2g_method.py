#!/usr/bin/env python3
"""Run one R2G-Expander checkpoint in a four-batch Experiment 1 method."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
from typing import Any


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.build_experiment1_r2g_submission import candidate_from_expander_record  # noqa: E402
from tools.run_experiment1_rtl_acquisition import (  # noqa: E402
    R2G_METHOD_IDS,
    TASK_SPEC,
    ExperimentError,
    evaluate_candidate,
    git_text,
    normalized_repo_url,
    read_json,
    manifest_toolchain_env,
    sha256_file,
    verify_bound_campaign,
    write_json_atomic,
)


FORMAL_FAMILY_TARGET = 50
FORMAL_REVISION_BATCH = 100
FORMAL_MAX_REVISION_BATCH = 200


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def classify_stop_reason(returncode: int, selected: int, target: int) -> str:
    """Map a bounded method run to the common, auditable stop taxonomy."""
    if returncode in {124, 137}:
        return "wall_time_limit"
    if returncode != 0:
        return "provider_failure"
    if selected >= target:
        return "target_reached"
    return "search_exhausted"


def classify_expander_stop_reason(
    returncode: int,
    selected: int,
    target: int,
    controller: dict[str, Any] | None,
) -> str:
    """Separate Agent finalization failures from external provider failures."""
    if returncode in {124, 137}:
        return "wall_time_limit"
    state = str((controller or {}).get("state") or "")
    if returncode != 0 and state in {"FAILED_CHILD_ROUND", "FAILED_FINALIZATION"}:
        return "finalization_failure"
    return classify_stop_reason(returncode, selected, target)


def load_controller_state(corpus: Path, objective_id: str) -> dict[str, Any] | None:
    path = corpus / "state/controllers" / objective_id / "controller.json"
    return read_json(path) if path.is_file() else None


def install_benchmark_registry(source: Path, corpus: Path) -> None:
    """Install the digest-bound immutable contamination profile into a cold corpus."""
    source = source.resolve()
    catalog = source / "registry_catalog.json"
    if not catalog.is_file():
        raise ExperimentError(f"benchmark registry catalog is missing: {catalog}")
    payload = read_json(catalog)
    profile_id = str(payload.get("active_profile") or "")
    profile = source / "profiles" / f"{profile_id}.json"
    if (
        not profile_id
        or not profile.is_file()
        or read_json(profile).get("ready") is not True
    ):
        raise ExperimentError("benchmark registry active profile is not audit-ready")
    target = corpus / "benchmark_registry"
    if target.exists():
        raise ExperimentError(f"cold corpus benchmark registry already exists: {target}")
    shutil.copytree(source, target)


def validate_run_mode(
    manifest: dict[str, Any],
    *,
    family_target: int,
    non_scoring_target: int | None,
    revision_batch: int,
    max_revision_batch: int,
    certified_corpus: Path | None,
) -> None:
    diagnostic_override = (
        non_scoring_target is not None
        or family_target != FORMAL_FAMILY_TARGET
        or revision_batch != FORMAL_REVISION_BATCH
        or max_revision_batch != FORMAL_MAX_REVISION_BATCH
        or certified_corpus is not None
    )
    if diagnostic_override and manifest.get("campaign_mode") != "non_scoring_canary":
        raise ExperimentError(
            "diagnostic R2G controls require a non_scoring_canary campaign"
        )


def run_logged(
    command: list[str],
    log: Path,
    *,
    env: dict[str, str],
    cwd: Path,
    append: bool = False,
) -> subprocess.CompletedProcess[Any]:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a" if append else "w", encoding="utf-8") as stream:
        stream.write("COMMAND: " + json.dumps(command) + "\n")
        stream.flush()
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            text=True,
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=False,
        )


def frontier_counts(corpus: Path) -> dict[str, int]:
    path = corpus / "state/frontier.sqlite"
    if not path.is_file():
        return {
            "query_definitions": 0,
            "search_requests": 0,
            "repository_revisions": 0,
            "acquisition_attempts": 0,
        }
    connection = sqlite3.connect(path)
    try:
        return {
            "query_definitions": int(
                connection.execute("SELECT COUNT(*) FROM queries").fetchone()[0]
            ),
            "search_requests": int(
                connection.execute("SELECT COALESCE(SUM(attempts),0) FROM queries").fetchone()[0]
            ),
            "repository_revisions": int(
                connection.execute("SELECT COUNT(*) FROM repository_revisions").fetchone()[0]
            ),
            "acquisition_attempts": int(
                connection.execute("SELECT COUNT(*) FROM acquisition_attempts").fetchone()[0]
            ),
        }
    finally:
        connection.close()


def query_attempts_by_id(corpus: Path) -> dict[str, int]:
    path = corpus / "state/frontier.sqlite"
    if not path.is_file():
        return {}
    connection = sqlite3.connect(path)
    try:
        return {
            str(row[0]): int(row[1] or 0)
            for row in connection.execute("SELECT query_id,attempts FROM queries")
        }
    finally:
        connection.close()


def query_records(
    corpus: Path, baseline_attempts: dict[str, int] | None = None
) -> list[dict[str, Any]]:
    path = corpus / "state/frontier.sqlite"
    if not path.is_file():
        return []
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT query_id,provider,query_text,created_at,updated_at,attempts "
            "FROM queries ORDER BY created_at,query_id"
        ).fetchall()
    finally:
        connection.close()
    records: list[dict[str, Any]] = []
    baseline_attempts = baseline_attempts or {}
    for row in rows:
        first = int(baseline_attempts.get(str(row["query_id"]), 0)) + 1
        for attempt in range(first, int(row["attempts"] or 0) + 1):
            records.append(
                {
                    "query": str(row["query_text"]),
                    "backend": str(row["provider"]),
                    "page": attempt,
                    # Frontier v1 stores aggregate query timing. The final update
                    # timestamp is retained for each request reconstruction.
                    "timestamp": str(row["updated_at"] or row["created_at"]),
                }
            )
    return records


def latest_certified_snapshot(corpus: Path) -> Path | None:
    latest = corpus / "snapshots/latest_release.json"
    if not latest.is_file():
        return None
    payload = read_json(latest)
    snapshot = corpus / "snapshots" / str(payload.get("corpus_snapshot_id") or "")
    completion = snapshot / "completion.json"
    if not completion.is_file() or read_json(completion).get("status") != "CERTIFIED":
        return None
    return snapshot


def previous_selection_state(campaign: Path, method_id: str, batch_id: int) -> tuple[set[tuple[str, str, str]], set[str]]:
    keys: set[tuple[str, str, str]] = set()
    families: set[str] = set()
    for earlier in range(1, batch_id):
        root = campaign / "method_runs" / f"{method_id}.batch{earlier}.formal"
        selection = root / "selection_manifest.json"
        if not selection.is_file():
            raise ExperimentError(
                f"missing earlier batch selection state for {method_id}/batch{earlier}"
            )
        payload = read_json(selection)
        keys.update(tuple(item) for item in payload.get("selected_keys", []))
        families.update(str(item) for item in payload.get("selected_family_ids", []))
    return keys, families


def size_bucket(cells: int) -> str:
    if cells < 1000:
        return "small"
    if cells < 10000:
        return "medium"
    return "large"


def select_diverse(
    rows: list[dict[str, Any]],
    *,
    target: int,
    prior_keys: set[tuple[str, str, str]],
    prior_families: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    batch_families: set[str] = set()
    for row in rows:
        candidate = row["candidate"]
        key = (
            str(candidate["repo_url"]).rstrip("/"),
            str(candidate["commit"]).lower(),
            str(candidate["top_module"]),
        )
        family = str(row.get("family_id") or "")
        reason = None
        if key in prior_keys:
            reason = "cross_batch_candidate_duplicate"
        elif not family:
            reason = "family_identity_missing"
        elif family in prior_families or family in batch_families:
            reason = "family_duplicate"
        if reason:
            excluded.append({"candidate_id": candidate["candidate_id"], "reason": reason})
            continue
        batch_families.add(family)
        row["_repo"] = normalized_repo_url(candidate["repo_url"])
        row["_category"] = str(candidate.get("category") or "other")
        row["_size"] = size_bucket(int(row["mapped_cells"]))
        eligible.append(row)

    selected: list[dict[str, Any]] = []
    remaining = list(eligible)
    repos: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    sizes: Counter[str] = Counter()
    while remaining and len(selected) < target:
        allowed = [row for row in remaining if repos[row["_repo"]] < 4]
        if not allowed:
            break
        chosen = min(
            allowed,
            key=lambda row: (
                repos[row["_repo"]],
                categories[row["_category"]],
                sizes[row["_size"]],
                row["candidate"]["candidate_id"],
            ),
        )
        selected.append(chosen)
        remaining.remove(chosen)
        repos[chosen["_repo"]] += 1
        categories[chosen["_category"]] += 1
        sizes[chosen["_size"]] += 1
    for row in remaining:
        excluded.append(
            {
                "candidate_id": row["candidate"]["candidate_id"],
                "reason": "repository_cap" if repos[row["_repo"]] >= 4 else "target_capacity",
            }
        )
    return [row["candidate"] for row in selected], {
        "selected_count": len(selected),
        "selected_keys": [
            [
                str(row["candidate"]["repo_url"]).rstrip("/"),
                str(row["candidate"]["commit"]).lower(),
                str(row["candidate"]["top_module"]),
            ]
            for row in selected
        ],
        "selected_family_ids": [str(row["family_id"]) for row in selected],
        "repository_counts": dict(sorted(repos.items())),
        "category_counts": dict(sorted(categories.items())),
        "size_counts": dict(sorted(sizes.items())),
        "excluded": excluded,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--method-id", choices=sorted(R2G_METHOD_IDS), required=True)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--batch-id", type=int, choices=range(1, 5), required=True)
    parser.add_argument("--cores", type=int, default=4)
    parser.add_argument("--family-target", type=int, default=FORMAL_FAMILY_TARGET)
    parser.add_argument(
        "--non-scoring-target",
        type=int,
        help="Diagnostic-only selection target; formal runs always use the frozen batch target.",
    )
    parser.add_argument("--revision-batch", type=int, default=FORMAL_REVISION_BATCH)
    parser.add_argument(
        "--max-revision-batch", type=int, default=FORMAL_MAX_REVISION_BATCH
    )
    parser.add_argument(
        "--certified-corpus",
        type=Path,
        help="Diagnostic replay only: skip discovery and consume this certified corpus.",
    )
    args = parser.parse_args()
    if args.cores < 1:
        parser.error("--cores must be positive")
    if args.non_scoring_target is not None and args.non_scoring_target < 1:
        parser.error("--non-scoring-target must be positive")
    if args.revision_batch < 1 or args.max_revision_batch < args.revision_batch:
        parser.error("revision batch bounds are invalid")

    campaign = args.campaign_root.resolve()
    manifest = read_json(campaign / "execution_manifest.json")
    verify_bound_campaign(manifest)
    validate_run_mode(
        manifest,
        family_target=args.family_target,
        non_scoring_target=args.non_scoring_target,
        revision_batch=args.revision_batch,
        max_revision_batch=args.max_revision_batch,
        certified_corpus=args.certified_corpus,
    )
    task = read_json(TASK_SPEC)
    if manifest["task_spec"]["sha256"] != sha256_file(TASK_SPEC):
        raise ExperimentError("campaign task-spec binding no longer matches")
    if manifest["agent_snapshot"]["commit"] != git_text("rev-parse", "HEAD"):
        raise ExperimentError("current Agent commit differs from campaign snapshot")
    method_root = campaign / "method_runs" / f"{args.method_id}.batch{args.batch_id}.formal"
    if method_root.exists():
        raise ExperimentError(f"method workspace already exists: {method_root}")
    runtime = method_root / "runtime/r2g-skills"
    logs = method_root / "logs"
    method_root.mkdir(parents=True)
    shutil.copytree(REPO / "r2g-skills", runtime)
    expander = runtime / "rtl-acquire/vendor/rtl-expander"
    state_root = campaign / "method_runs" / f"{args.method_id}.formal_state"
    corpus = args.certified_corpus.resolve() if args.certified_corpus else state_root / "expander_corpus"
    if args.certified_corpus is None:
        if args.batch_id == 1:
            if state_root.exists():
                raise ExperimentError(f"R2G method state already exists: {state_root}")
            state_root.mkdir(parents=True)
            install_benchmark_registry(
                Path(manifest["benchmark_registry"]["path"]), corpus
            )
        elif not corpus.is_dir():
            raise ExperimentError(
                f"R2G batch {args.batch_id} requires the preceding method frontier"
            )
    started_at = now_iso()
    started = time.monotonic()
    env = manifest_toolchain_env(manifest)
    env.update({"NUM_CORES": str(args.cores), "ORFS_MAX_CPUS": str(args.cores)})
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "__PYVENV_LAUNCHER__"):
        env.pop(key, None)
    budget = task["method_budget"]
    wall_limit = int(budget["wall_time_seconds"])
    memory_record: dict[str, Any] | None = None
    discovery_rc = 0
    counts_before = frontier_counts(corpus)
    query_attempts_before = query_attempts_by_id(corpus)
    objective = (
        f"{args.method_id}-batch{args.batch_id}-"
        + manifest["campaign_id"].replace("_", "-")
    )

    if args.certified_corpus is None:
        corpus.mkdir(parents=True, exist_ok=True)
        seed_command = [
            sys.executable,
            str(expander / "scripts/discover_repositories.py"),
            "--corpus-root",
            str(corpus),
            "--providers",
            "github",
            "--budget",
            "500",
            "--query-budget",
            str(len(task["seed_categories"])),
            "--graph-budget",
            "0",
        ]
        for seed in task["seed_categories"]:
            seed_command.extend(["--query", seed])
        seed = run_logged(
            seed_command,
            logs / "seed_discovery.log",
            env=env,
            cwd=method_root,
        )
        if seed.returncode:
            discovery_rc = seed.returncode
        else:
            elapsed = time.monotonic() - started
            remaining = max(1, int(wall_limit - elapsed))
            command = [
                "timeout",
                "--signal=TERM",
                "--kill-after=60",
                str(remaining),
                sys.executable,
                str(expander / "scripts/run_until_family_target.py"),
                "--corpus-root",
                str(corpus),
                "--target-global-design-families",
                str(args.family_target * args.batch_id),
                "--revision-batch",
                str(args.revision_batch),
                "--max-revision-batch",
                str(args.max_revision_batch),
                "--objective-id",
                objective,
                "--providers",
                "github",
                "--process-budget",
                "100",
                "--pipeline-workers",
                str(args.cores),
            ]
            discovery_rc = run_logged(
                command,
                logs / "expander.log",
                env=env,
                cwd=method_root,
            ).returncode

    counts = frontier_counts(corpus)
    queries = query_records(corpus, query_attempts_before)
    count_deltas = {
        key: counts[key] - counts_before.get(key, 0) for key in counts
    }
    if count_deltas["search_requests"] != len(queries):
        raise ExperimentError("Expander search-request reconstruction is inconsistent")
    if count_deltas["search_requests"] > int(budget["search_requests"]):
        raise ExperimentError(
            "Expander exceeded the frozen search budget: "
            f"{count_deltas['search_requests']}"
        )
    snapshot = latest_certified_snapshot(corpus)
    prequalified: list[dict[str, Any]] = []
    precheck_rows: list[dict[str, Any]] = []
    precheck_attempts = 0
    selection_payload: dict[str, Any] = {
        "selected_count": 0,
        "selected_keys": [],
        "selected_family_ids": [],
        "excluded": [],
    }
    if snapshot is not None:
        bridge_path = method_root / "expander_bridge.json"
        candidate_csv = method_root / "expander_candidates.csv"
        imported = run_logged(
            [
                sys.executable,
                str(runtime / "rtl-acquire/scripts/acquire/import_expander_snapshot.py"),
                "--corpus-root",
                str(corpus),
                "--snapshot",
                str(snapshot),
                "--view",
                "public_export_allowed",
                "--output-csv",
                str(candidate_csv),
                "--bridge-manifest",
                str(bridge_path),
            ],
            logs / "snapshot_import.log",
            env=env,
            cwd=method_root,
        )
        if imported.returncode == 0:
            bridge = read_json(bridge_path)
            prior_keys, prior_families = previous_selection_state(
                campaign, args.method_id, args.batch_id
            )
            screened_families: set[str] = set()
            precheck_root = method_root / "precheck"
            source_cache = precheck_root / "_source_cache"
            source_cache.mkdir(parents=True)
            for record in bridge.get("candidates") or []:
                if time.monotonic() - started >= wall_limit:
                    break
                candidate = candidate_from_expander_record(record, args.method_id)
                candidate_key = (
                    str(candidate["repo_url"]).rstrip("/"),
                    str(candidate["commit"]).lower(),
                    str(candidate["top_module"]),
                )
                family_id = str(record.get("family_id") or "")
                if (
                    candidate_key in prior_keys
                    or family_id in prior_families
                    or family_id in screened_families
                ):
                    continue
                if family_id:
                    screened_families.add(family_id)
                precheck_attempts += 1
                remaining_wall = max(
                    1, int(wall_limit - (time.monotonic() - started))
                )
                result = evaluate_candidate(
                    candidate,
                    precheck_root,
                    source_cache,
                    env,
                    synth_timeout_seconds=min(3600, remaining_wall),
                )
                write_json_atomic(
                    precheck_root / candidate["candidate_id"] / "result.json", result
                )
                if result.get("failure_class") == "evaluator_exception":
                    raise ExperimentError(
                        f"public precheck infrastructure failed: {result.get('error')}"
                    )
                if result.get("publishable_qualified", result.get("qualified")):
                    precheck_rows.append(
                        {
                            "candidate": candidate,
                            "family_id": family_id,
                            "mapped_cells": int(
                                (result.get("synthesis") or {}).get("mapped_cells", 0)
                            ),
                        }
                    )
            prequalified, selection_payload = select_diverse(
                precheck_rows,
                target=(
                    int(args.non_scoring_target)
                    if args.non_scoring_target is not None
                    else int(task["batch_policy"]["target_candidates_per_batch"])
                ),
                prior_keys=prior_keys,
                prior_families=prior_families,
            )

    write_json_atomic(method_root / "prequalified_candidates.json", prequalified)
    selection_payload.update(
        {
            "schema_version": "1.0",
            "method_id": args.method_id,
            "batch_id": args.batch_id,
            "snapshot": str(snapshot) if snapshot else None,
            "scheduler_memory": memory_record,
            "precheck_qualified_before_selection": len(precheck_rows),
        }
    )
    write_json_atomic(method_root / "selection_manifest.json", selection_payload)
    elapsed = min(float(wall_limit), round(time.monotonic() - started, 3))
    target = (
        int(args.non_scoring_target)
        if args.non_scoring_target is not None
        else int(task["batch_policy"]["target_candidates_per_batch"])
    )
    effective_returncode = discovery_rc
    if elapsed >= wall_limit and len(prequalified) < target:
        effective_returncode = 124
    if snapshot is None and discovery_rc == 0:
        effective_returncode = 1
    controller = (
        load_controller_state(corpus, objective)
        if args.certified_corpus is None
        else None
    )
    stop_reason = classify_expander_stop_reason(
        effective_returncode, len(prequalified), target, controller
    )
    end = {
        "schema_version": "1.0",
        "experiment_id": task["experiment_id"],
        "method_id": args.method_id,
        "batch_id": args.batch_id,
        "started_at": started_at,
        "ended_at": now_iso(),
        "method_runtime_seconds": elapsed,
        "stop_reason": stop_reason,
        "returncode": discovery_rc,
        "scheduler_memory": memory_record,
        "certified_snapshot": str(snapshot) if snapshot else None,
        "certified_snapshot_sha256": (
            sha256_file(snapshot / "release_identity.json") if snapshot else None
        ),
        "queries": queries,
        "search_requests": count_deltas["search_requests"],
        "query_definitions": count_deltas["query_definitions"],
        "clone_count": count_deltas["repository_revisions"],
        "self_synth_attempts": count_deltas["acquisition_attempts"] + precheck_attempts,
        "public_precheck_attempts": precheck_attempts,
        "precheck_qualified": len(precheck_rows),
        "selected_candidates": len(prequalified),
        "non_scoring_target": args.non_scoring_target,
        "controller_state": (controller or {}).get("state"),
    }
    write_json_atomic(method_root / "method_end.json", end)
    print(
        f"{args.method_id}/batch{args.batch_id}: selected={len(prequalified)}/{target} "
        f"queries={len(queries)} stop={stop_reason}"
    )
    return 0 if stop_reason != "provider_failure" else 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ExperimentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
