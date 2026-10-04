#!/usr/bin/env python3
"""Run one continuous R2G-Expander Experiment 1 acquisition."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from typing import Any


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from experiments.build_experiment1_r2g_submission import candidate_from_expander_record  # noqa: E402
from experiments.run_experiment1_rtl_acquisition import (  # noqa: E402
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


# The Expander controller counts synthesis-valid design families, while the
# Experiment 1 target additionally requires license, closure, size, and frozen
# Sky130HD qualification. The larger discovery target supplies enough raw
# families for the 200-slot formal gate without treating the family count as
# successful completion.
FORMAL_FAMILY_TARGET = 2000
FORMAL_REVISION_BATCH = 200
FORMAL_MAX_REVISION_BATCH = 2000
FORMAL_DISCOVERY_QUOTA_RESERVE = 0
FORMAL_QUALIFICATION_GAP_MULTIPLIER = 15


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
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            text=True,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        previous_handlers: dict[signal.Signals, Any] = {}

        def forward_termination(signum: int, _frame: Any) -> None:
            try:
                os.killpg(process.pid, signum)
            except ProcessLookupError:
                pass
            raise SystemExit(128 + signum)

        for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, forward_termination)
        try:
            returncode = process.wait()
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=30)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            raise
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
        return subprocess.CompletedProcess(command, returncode)


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


def formal_synthesis_valid_family_count(corpus: Path) -> int:
    """Return the Expander completion metric used by the family controller."""
    path = corpus / "state/corpus.sqlite"
    if not path.is_file():
        return 0
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT COUNT(DISTINCT family_id) FROM designs "
            "WHERE synthesis_valid=1 AND provenance_complete=1"
        ).fetchone()
        return int(row[0] or 0)
    finally:
        connection.close()


def replenishment_family_target(
    current_families: int,
    selected_candidates: int,
    candidate_target: int,
    *,
    gap_multiplier: int = FORMAL_QUALIFICATION_GAP_MULTIPLIER,
    minimum_target: int = 0,
) -> int:
    """Translate the remaining formal-candidate gap into an absolute family target."""
    missing = max(0, candidate_target - selected_candidates)
    return max(minimum_target, current_families + gap_multiplier * missing)


def discovery_family_target(
    round_index: int,
    current_families: int,
    selected_candidates: int,
    candidate_target: int,
    *,
    initial_target: int = FORMAL_FAMILY_TARGET,
    gap_multiplier: int = FORMAL_QUALIFICATION_GAP_MULTIPLIER,
) -> int:
    """Use a fixed first corpus size, then replenish from the measured gap."""
    if round_index == 1:
        return max(current_families, initial_target)
    return replenishment_family_target(
        current_families,
        selected_candidates,
        candidate_target,
        gap_multiplier=gap_multiplier,
    )


def write_qualification_checkpoints(
    method_root: Path,
    candidates: list[dict[str, Any]],
    checkpoint_targets: list[int],
) -> None:
    """Write immutable method-side checkpoints without exposing evaluator feedback."""
    root = method_root / "checkpoints"
    for target in checkpoint_targets:
        if len(candidates) < target:
            continue
        path = root / f"qualified_{target:03d}.json"
        if path.exists():
            continue
        root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(
            path,
            {
                "schema_version": "1.0",
                "method_id": "r2g-expander-cold",
                "target": target,
                "recorded_at": now_iso(),
                "candidates": candidates[:target],
            },
        )


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
    locked_keys: list[tuple[str, str, str]] | None = None,
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
    by_key = {
        (
            str(row["candidate"]["repo_url"]).rstrip("/"),
            str(row["candidate"]["commit"]).lower(),
            str(row["candidate"]["top_module"]),
        ): row
        for row in eligible
    }
    for key in locked_keys or []:
        row = by_key.get(key)
        if row is None:
            raise ExperimentError(
                "an immutable qualification-checkpoint candidate disappeared"
            )
        if row not in remaining:
            continue
        if repos[row["_repo"]] >= 4:
            raise ExperimentError("immutable checkpoint violates repository cap")
        selected.append(row)
        remaining.remove(row)
        repos[row["_repo"]] += 1
        categories[row["_category"]] += 1
        sizes[row["_size"]] += 1
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
    parser.add_argument("--batch-id", type=int, choices=(1,), required=True)
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
        "--non-scoring-gap-multiplier",
        type=int,
        help="Diagnostic-only qualification-gap multiplier used to force a small multi-round canary.",
    )
    parser.add_argument(
        "--non-scoring-max-replenishment-rounds",
        type=int,
        help="Diagnostic-only bound that ends a canary cleanly after this many replenishment rounds.",
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
    if args.non_scoring_gap_multiplier is not None and args.non_scoring_gap_multiplier < 1:
        parser.error("--non-scoring-gap-multiplier must be positive")
    if (
        args.non_scoring_max_replenishment_rounds is not None
        and args.non_scoring_max_replenishment_rounds < 1
    ):
        parser.error("--non-scoring-max-replenishment-rounds must be positive")

    campaign = args.campaign_root.resolve()
    manifest = read_json(campaign / "execution_manifest.json")
    verify_bound_campaign(manifest)
    if (
        (
            args.non_scoring_gap_multiplier is not None
            or args.non_scoring_max_replenishment_rounds is not None
        )
        and manifest.get("campaign_mode") != "non_scoring_canary"
    ):
        raise ExperimentError(
            "diagnostic replenishment controls require a non_scoring_canary campaign"
        )
    validate_run_mode(
        manifest,
        family_target=args.family_target,
        non_scoring_target=args.non_scoring_target,
        revision_batch=args.revision_batch,
        max_revision_batch=args.max_revision_batch,
        certified_corpus=args.certified_corpus,
    )
    task = read_json(TASK_SPEC)
    frozen_cores = int(task["method_budget"]["cpu_cores"])
    if args.cores != frozen_cores:
        parser.error(
            f"--cores must match the frozen Experiment 1 budget ({frozen_cores})"
        )
    if int(task["method_budget"]["max_concurrent_synthesis"]) != 1:
        parser.error("R2G runner supports exactly one concurrent synthesis per method")
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
    runtime.mkdir(parents=True)
    shutil.copytree(
        REPO / "r2g-skills/rtl-acquire",
        runtime / "rtl-acquire",
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", "*.pyo"),
    )
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
    objective_prefix = (
        f"{args.method_id}-run-{manifest['campaign_id'].replace('_', '-')}"
    )
    target = (
        int(args.non_scoring_target)
        if args.non_scoring_target is not None
        else int(task["batch_policy"]["target_candidates_per_batch"])
    )
    checkpoint_targets = [
        int(value) for value in task["batch_policy"].get("checkpoint_targets", [])
        if int(value) <= target
    ]
    gap_multiplier = int(
        args.non_scoring_gap_multiplier
        if args.non_scoring_gap_multiplier is not None
        else task.get("r2g_replenishment_policy", {}).get(
            "synthesis_valid_families_per_missing_candidate",
            FORMAL_QUALIFICATION_GAP_MULTIPLIER,
        )
    )
    prior_keys, prior_families = previous_selection_state(
        campaign, args.method_id, args.batch_id
    )
    screened_families: set[str] = set()
    screened_keys: set[tuple[str, str, str]] = set()
    locked_selection_keys: list[tuple[str, str, str]] = []
    precheck_rows: list[dict[str, Any]] = []
    prequalified: list[dict[str, Any]] = []
    precheck_attempts = 0
    selection_payload: dict[str, Any] = {
        "selected_count": 0,
        "selected_keys": [],
        "selected_family_ids": [],
        "excluded": [],
    }
    replenishment_rounds: list[dict[str, Any]] = []
    snapshot: Path | None = None
    last_objective: str | None = None
    no_progress = False
    stopped_for_round_limit = False

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
            "--quota-reserve",
            str(FORMAL_DISCOVERY_QUOTA_RESERVE),
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
    round_index = 0
    while discovery_rc == 0 and len(prequalified) < target:
        if time.monotonic() - started >= wall_limit:
            break
        if (
            args.non_scoring_max_replenishment_rounds is not None
            and round_index >= args.non_scoring_max_replenishment_rounds
        ):
            stopped_for_round_limit = True
            break
        round_index += 1
        selected_before_round = len(prequalified)
        families_before = formal_synthesis_valid_family_count(corpus)
        requested_family_target: int | None = None
        if args.certified_corpus is None:
            requested_family_target = discovery_family_target(
                round_index,
                families_before,
                len(prequalified),
                target,
                initial_target=args.family_target,
                gap_multiplier=gap_multiplier,
            )
            last_objective = (
                f"{objective_prefix}-replenish{round_index:03d}-"
                f"target{requested_family_target}"
            )
            remaining = max(1, int(wall_limit - (time.monotonic() - started)))
            command = [
                "timeout", "--signal=TERM", "--kill-after=60", str(remaining),
                sys.executable,
                str(expander / "scripts/run_until_family_target.py"),
                "--corpus-root", str(corpus),
                "--target-global-design-families", str(requested_family_target),
                "--revision-batch", str(args.revision_batch),
                "--min-revision-batch", str(args.revision_batch),
                "--max-revision-batch", str(args.max_revision_batch),
                "--objective-id", last_objective,
                "--providers", "github",
                "--discovery-quota-reserve", str(FORMAL_DISCOVERY_QUOTA_RESERVE),
                "--process-budget", "100",
                "--pipeline-workers", str(args.cores),
            ]
            discovery_rc = run_logged(
                command,
                logs / f"expander_round{round_index:03d}.log",
                env=env,
                cwd=method_root,
            ).returncode

        snapshot = latest_certified_snapshot(corpus)
        round_root = method_root / "replenishment" / f"round{round_index:03d}"
        round_root.mkdir(parents=True, exist_ok=True)
        newly_screened = 0
        newly_qualified = 0
        if snapshot is not None:
            bridge_path = round_root / "expander_bridge.json"
            candidate_csv = round_root / "expander_candidates.csv"
            imported = run_logged(
                [
                    sys.executable,
                    str(runtime / "rtl-acquire/scripts/acquire/import_expander_snapshot.py"),
                    "--corpus-root", str(corpus),
                    "--snapshot", str(snapshot),
                    "--view", "public_export_allowed",
                    "--output-csv", str(candidate_csv),
                    "--bridge-manifest", str(bridge_path),
                ],
                logs / f"snapshot_import_round{round_index:03d}.log",
                env=env,
                cwd=method_root,
            )
            if imported.returncode != 0:
                discovery_rc = imported.returncode
            else:
                bridge = read_json(bridge_path)
                precheck_root = method_root / "precheck" / f"round{round_index:03d}"
                source_cache = method_root / "precheck" / "_source_cache"
                source_cache.mkdir(parents=True, exist_ok=True)
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
                        or candidate_key in screened_keys
                        or family_id in prior_families
                        or family_id in screened_families
                    ):
                        continue
                    screened_keys.add(candidate_key)
                    if family_id:
                        screened_families.add(family_id)
                    newly_screened += 1
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
                    if result.get("failure_class") == "evaluator_exception":
                        raise ExperimentError(
                            f"public precheck infrastructure failed: {result.get('error')}"
                        )
                    if result.get("publishable_qualified", result.get("qualified")):
                        newly_qualified += 1
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
                    target=target,
                    prior_keys=prior_keys,
                    prior_families=prior_families,
                    locked_keys=locked_selection_keys,
                )
                achieved_checkpoints = [
                    value for value in checkpoint_targets if len(prequalified) >= value
                ]
                if achieved_checkpoints:
                    locked_count = max(achieved_checkpoints)
                    locked_selection_keys = [
                        (
                            str(candidate["repo_url"]).rstrip("/"),
                            str(candidate["commit"]).lower(),
                            str(candidate["top_module"]),
                        )
                        for candidate in prequalified[:locked_count]
                    ]
                write_qualification_checkpoints(
                    method_root, prequalified, checkpoint_targets
                )

        families_after = formal_synthesis_valid_family_count(corpus)
        replenishment_rounds.append(
            {
                "round": round_index,
                "missing_candidates_before": max(0, target - selected_before_round),
                "requested_new_synthesis_valid_families": (
                    None
                    if requested_family_target is None
                    else max(0, requested_family_target - families_before)
                ),
                "family_target": requested_family_target,
                "families_before": families_before,
                "families_after": families_after,
                "newly_screened_families": newly_screened,
                "newly_publishable_qualified": newly_qualified,
                "selected_candidates_after": len(prequalified),
                "snapshot": str(snapshot) if snapshot else None,
                "returncode": discovery_rc,
            }
        )
        write_json_atomic(method_root / "replenishment_rounds.json", replenishment_rounds)

        if args.certified_corpus is not None or len(prequalified) >= target:
            break
        if discovery_rc != 0:
            break
        counts_now = frontier_counts(corpus)
        search_delta = counts_now["search_requests"] - counts_before.get("search_requests", 0)
        if search_delta >= int(budget["search_requests"]):
            break
        if families_after <= families_before and newly_screened == 0:
            no_progress = True
            break

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

    write_json_atomic(method_root / "prequalified_candidates.json", prequalified)
    selection_payload.update(
        {
            "schema_version": "1.0",
            "method_id": args.method_id,
            "batch_id": args.batch_id,
            "snapshot": str(snapshot) if snapshot else None,
            "scheduler_memory": memory_record,
            "precheck_qualified_before_selection": len(precheck_rows),
            "immutable_checkpoint_candidate_count": len(locked_selection_keys),
        }
    )
    write_json_atomic(method_root / "selection_manifest.json", selection_payload)
    elapsed = min(float(wall_limit), round(time.monotonic() - started, 3))
    effective_returncode = discovery_rc
    if elapsed >= wall_limit and len(prequalified) < target:
        effective_returncode = 124
    if snapshot is None and discovery_rc == 0:
        effective_returncode = 1
    controller = (
        load_controller_state(corpus, last_objective)
        if args.certified_corpus is None and last_objective is not None
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
        "qualification_gap_multiplier": gap_multiplier,
        "replenishment_round_count": len(replenishment_rounds),
        "replenishment_stopped_for_no_progress": no_progress,
        "replenishment_stopped_for_round_limit": stopped_for_round_limit,
        "replenishment_rounds": replenishment_rounds,
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
