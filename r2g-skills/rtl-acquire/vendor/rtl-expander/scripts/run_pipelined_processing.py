#!/usr/bin/env python3
"""Process acquired revisions into immutable staging artifacts before cohort lock."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import re
from pathlib import Path
from typing import Any

from corpus_state import CorpusState
from processing_queue import ProcessingQueue, exact_terminal_set, utc_now
from staged_closure_audit import atomic_json as atomic_audit_json, audit as staged_closure_audit


SCHEMA = "rtl_pipelined_processing_v1"
STOP_REQUESTED = False


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def load_keys(path: Path) -> set[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(value) for value in data.get("revision_keys", [])}


def process_command(args: argparse.Namespace, row: dict[str, Any], receipt: Path) -> list[str]:
    revision_key = str(row["repository_revision_key"])
    token = hashlib.sha256(revision_key.encode()).hexdigest()[:20]
    work = args.corpus_root / "state/pipeline_work" / args.round_id / token
    work.mkdir(parents=True, exist_ok=True)
    link = work / "revision"
    source = Path(str(row["source_path"]))
    if link.is_symlink() and link.resolve() != source.resolve():
        link.unlink()
    if not link.exists():
        os.symlink(source, link, target_is_directory=True)
    command = [
        sys.executable, str(Path(__file__).parent / "run_expansion_round.py"),
        "--source-root", str(work), "--corpus-root", str(args.corpus_root),
        "--repo", "revision", "--max-repos", "1", "--synthesize",
        "--max-repo-seconds", str(args.max_repo_seconds),
        "--stage-only", "--staging-receipt", str(receipt),
    ]
    match = re.search(r"_batch(\d+)$", args.round_id)
    if args.round_id.startswith("p2f_") and match and int(match.group(1)) >= 4:
        command.append("--discovery-precision-policy")
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--round-id", required=True)
    parser.add_argument("--start-json", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--max-repo-seconds", type=int, default=900)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--parent-pid", type=int, default=0)
    parser.add_argument("--status-path", type=Path)
    parser.add_argument("--staged-audit-min-terminal-delta", type=int, default=25)
    parser.add_argument("--staged-audit-max-seconds", type=float, default=60.0)
    args = parser.parse_args()
    args.workers = max(1, args.workers)
    status_path = args.status_path or (
        args.corpus_root / "quality/phase2/rounds" / args.round_id / "pipeline_status.json"
    )
    cohort_path = args.corpus_root / "quality/phase2/rounds" / args.round_id / "cohort_lock.json"
    receipts = args.corpus_root / "state/pipeline_receipts" / args.round_id
    receipts.mkdir(parents=True, exist_ok=True)
    logs = args.corpus_root / "quality/phase2/rounds" / args.round_id / "logs/pipeline"
    logs.mkdir(parents=True, exist_ok=True)
    lock_path = args.corpus_root / "state" / f"pipelined-processing-{args.round_id}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    started_at = utc_now()
    started_monotonic = time.monotonic()
    peak = 0
    processing_idle_due_to_acquisition_seconds = 0.0
    last_loop_monotonic = time.monotonic()
    children: dict[int, dict[str, Any]] = {}
    staged_audit_path = status_path.parent / "staged_split_audit.json"
    staged_audit_state: dict[str, Any] = {}
    last_staged_audit_terminal = -1
    last_staged_audit_monotonic = 0.0

    def request_stop(_signum: int, _frame: Any) -> None:
        global STOP_REQUESTED
        STOP_REQUESTED = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    excluded = load_keys(args.start_json)
    with lock_path.open("a+") as lock_handle:
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 3
        with ProcessingQueue(args.corpus_root) as queue, CorpusState(args.corpus_root) as corpus_state:
            queue.requeue_abandoned(args.round_id)
            try:
                while True:
                    loop_started_monotonic = time.monotonic()
                    if args.parent_pid:
                        try:
                            os.kill(args.parent_pid, 0)
                        except OSError:
                            request_stop(signal.SIGTERM, None)
                    inserted = queue.reconcile_frontier(
                        args.round_id, args.corpus_root / "state/frontier.sqlite", excluded,
                    )
                    if inserted:
                        corpus_state.record_processing_events([
                            (
                                "REVISION_ACQUIRED", str(row["repository_revision_key"]),
                                {
                                    "round_id": args.round_id, "source_path": row["source_path"],
                                    "acquired_at": row.get("acquired_at"),
                                },
                            )
                            for row in inserted
                        ])

                    for pid, child in list(children.items()):
                        process: subprocess.Popen[Any] = child["process"]
                        returncode = process.poll()
                        if returncode is None:
                            continue
                        child["stdout"].close()
                        child["stderr"].close()
                        revision_key = child["revision_key"]
                        receipt_path: Path = child["receipt"]
                        try:
                            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                            rows = receipt.get("repositories", [])
                            staged = rows[0] if len(rows) == 1 else {}
                            terminal_state = str(staged.get("terminal_state") or "")
                            if returncode != 0 or not terminal_state:
                                raise RuntimeError(f"stage exit={returncode}; repositories={len(rows)}")
                            run_key = str(staged.get("run_key") or "") or None
                            artifact = (
                                args.corpus_root / "state/repo_runs" / f"{run_key}.json"
                                if run_key else receipt_path
                            )
                            admission = queue.finish(
                                args.round_id, revision_key, terminal_state=terminal_state,
                                run_key=run_key, artifact_path=str(artifact),
                            )
                            corpus_state.record_processing_event("PROCESSING_TERMINAL", revision_key, {
                                "round_id": args.round_id, "run_key": run_key,
                                "terminal_state": terminal_state,
                                "artifact_path": str(artifact),
                                "artifact_sha256": admission["artifact_sha256"],
                                "artifact_size": admission["artifact_size"],
                                "hash_policy": "HASH_ON_IMMUTABLE_ADMISSION_V1",
                            })
                        except Exception as exc:
                            queue.fail(args.round_id, revision_key, f"{type(exc).__name__}: {exc}", args.max_attempts)
                            corpus_state.record_processing_event("PROCESSING_RETRY_SCHEDULED", revision_key, {
                                "round_id": args.round_id, "detail": f"{type(exc).__name__}: {exc}",
                            })
                        children.pop(pid)

                    while not STOP_REQUESTED and len(children) < args.workers:
                        worker_id = f"pipeline-{os.getpid()}-{len(children) + 1}"
                        row = queue.claim(args.round_id, worker_id, args.max_attempts)
                        if row is None:
                            break
                        revision_key = str(row["repository_revision_key"])
                        token = hashlib.sha256(revision_key.encode()).hexdigest()[:20]
                        receipt = receipts / f"{token}.json"
                        command = process_command(args, row, receipt)
                        attempt = int(row["attempts"])
                        stdout_handle = (logs / f"{token}.attempt{attempt}.stdout.log").open("ab")
                        stderr_handle = (logs / f"{token}.attempt{attempt}.stderr.log").open("ab")
                        process = subprocess.Popen(
                            command, stdout=stdout_handle, stderr=stderr_handle,
                            start_new_session=True,
                        )
                        children[process.pid] = {
                            "process": process, "revision_key": revision_key,
                            "receipt": receipt, "stdout": stdout_handle, "stderr": stderr_handle,
                        }
                        corpus_state.record_processing_event("PROCESSING_STARTED", revision_key, {
                            "round_id": args.round_id, "worker_id": worker_id,
                            "attempt": attempt, "child_pid": process.pid,
                        })

                    counts = queue.counts(args.round_id)
                    lifecycle = queue.lifecycle_counts(args.round_id)
                    peak = max(peak, queue.waiting_depth(args.round_id))
                    cohort_keys = load_keys(cohort_path) if cohort_path.is_file() else set()
                    exact = bool(cohort_keys) and exact_terminal_set(queue, args.round_id, cohort_keys)
                    terminal_count = int(lifecycle["terminal"])
                    audit_due = terminal_count > 0 and (
                        last_staged_audit_terminal < 0
                        or terminal_count - last_staged_audit_terminal
                        >= max(1, args.staged_audit_min_terminal_delta)
                        or time.monotonic() - last_staged_audit_monotonic
                        >= max(1.0, args.staged_audit_max_seconds)
                        or exact
                    )
                    if audit_due:
                        try:
                            staged_audit_state = staged_closure_audit(
                                args.corpus_root, args.round_id
                            )
                            atomic_audit_json(staged_audit_path, staged_audit_state)
                            last_staged_audit_terminal = terminal_count
                            last_staged_audit_monotonic = time.monotonic()
                        except Exception as exc:
                            staged_audit_state = {
                                "schema": "rtl_staged_closure_audit_v1",
                                "round_id": args.round_id,
                                "generated_at": utc_now(),
                                "publication_performed": False,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                            atomic_audit_json(staged_audit_path, staged_audit_state)
                    timings = queue.timing_seconds(args.round_id)
                    state = (
                        "DRAINED" if exact and not children else
                        "STOPPING" if STOP_REQUESTED else
                        "COHORT_LOCKED_DRAINING" if cohort_keys else "ACQUIRING_PIPELINED"
                    )
                    status = {
                        "schema": SCHEMA, "round_id": args.round_id, "state": state,
                        "manager_pid": os.getpid(), "worker_limit": args.workers,
                        "active_workers": len(children), "queue_counts": counts,
                        "acquired_revision_count": lifecycle["acquired"],
                        "processing_started_count": lifecycle["started"],
                        "processing_terminal_count": lifecycle["terminal"],
                        "processing_queue_peak": peak,
                        "processing_queue_p50_seconds": percentile(timings, 0.50),
                        "processing_queue_p95_seconds": percentile(timings, 0.95),
                        "cohort_locked": bool(cohort_keys), "cohort_size": len(cohort_keys),
                        "terminal_set_matches_cohort": exact,
                        "staged_split_audit_path": str(staged_audit_path),
                        "staged_split_audit": {
                            key: staged_audit_state.get(key)
                            for key in (
                                "generated_at", "terminal_artifacts_audited",
                                "staged_designs_audited", "potential_train_val_components",
                                "potential_test_boundary_conflicts", "unknown_split_groups", "error",
                            )
                            if key in staged_audit_state
                        },
                        "pipeline_overlap_hours": round(
                            max(0.0, time.monotonic() - started_monotonic) / 3600.0, 6
                        ),
                        "processing_idle_due_to_acquisition_seconds": round(
                            processing_idle_due_to_acquisition_seconds, 3
                        ),
                        "acquisition_idle_due_to_processing_seconds": 0.0,
                        "started_at": started_at, "heartbeat_at": utc_now(),
                    }
                    atomic_json(status_path, status)
                    if state == "DRAINED":
                        return 0
                    if STOP_REQUESTED and not children:
                        return 130
                    if not cohort_keys and not children and queue.waiting_depth(args.round_id) == 0:
                        processing_idle_due_to_acquisition_seconds += max(
                            0.0, loop_started_monotonic - last_loop_monotonic
                        )
                    last_loop_monotonic = loop_started_monotonic
                    time.sleep(max(0.25, args.poll_seconds))
            finally:
                for child in children.values():
                    process = child["process"]
                    if process.poll() is None:
                        try:
                            os.killpg(process.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                for child in children.values():
                    process = child["process"]
                    if process.poll() is None:
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            process.wait(timeout=10)
                for child in children.values():
                    child["stdout"].close()
                    child["stderr"].close()
                queue.requeue_abandoned(args.round_id)


if __name__ == "__main__":
    raise SystemExit(main())
