#!/usr/bin/env python3
"""Run bounded Experiment 2 method/design campaigns in reproducible fixture blocks."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.experiment2_signoff import (  # noqa: E402
    METHOD_IDS,
    append_jsonl,
    load_cohort,
    now_iso,
    project_paths,
    read_json,
    verify_campaign_bindings,
    write_json_atomic,
)


_ACTIVE_METHOD_GROUPS: set[int] = set()
_ACTIVE_METHOD_GROUPS_LOCK = threading.Lock()


def register_method_group(pgid: int) -> None:
    with _ACTIVE_METHOD_GROUPS_LOCK:
        _ACTIVE_METHOD_GROUPS.add(pgid)


def unregister_method_group(pgid: int) -> None:
    with _ACTIVE_METHOD_GROUPS_LOCK:
        _ACTIVE_METHOD_GROUPS.discard(pgid)


def terminate_active_method_groups(grace_seconds: float = 5.0) -> None:
    with _ACTIVE_METHOD_GROUPS_LOCK:
        groups = tuple(_ACTIVE_METHOD_GROUPS)
    for pgid in groups:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    if groups and grace_seconds > 0:
        time.sleep(grace_seconds)
    for pgid in groups:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def handle_shutdown_signal(signum: int, _frame: object) -> None:
    terminate_active_method_groups()
    raise SystemExit(128 + signum)


def task_command(args: argparse.Namespace, method: str, fixture: str) -> list[str]:
    if method == "full-r2g":
        return [
            sys.executable,
            str(REPO / "tools" / "run_experiment2_signoff.py"),
            "run-full-r2g",
            "--campaign-root", str(args.campaign_root),
            "--method", method,
            "--fixture", fixture,
        ]
    return [
        sys.executable,
        str(REPO / "tools" / "run_experiment2_vanilla_method.py"),
        "--campaign-root", str(args.campaign_root),
        "--method", method,
        "--fixture", fixture,
        "--routes", str(Path(manifest_routes(args.campaign_root))),
        "--env-file", str(args.env_file),
        "--token-budget", str(args.token_budget),
        "--wall-time-seconds", str(args.wall_time_seconds),
        "--max-turns", str(args.max_turns),
        "--max-flow-attempts", str(args.max_flow_attempts),
        "--max-output-tokens", str(args.max_output_tokens),
        "--flow-timeout-seconds", str(args.flow_timeout_seconds),
    ]


def manifest_routes(campaign_root: Path) -> str:
    manifest = read_json(campaign_root / "experiment2_campaign.json", {}) or {}
    return str((manifest.get("model_routes") or {}).get("path") or "")


def terminal_record(root: Path, method: str, fixture: str) -> Path:
    return project_paths(root, method, fixture)["method"] / "batch_result.json"


def run_one(args: argparse.Namespace, method: str, fixture: str) -> dict:
    result_path = terminal_record(args.campaign_root, method, fixture)
    prior = read_json(result_path)
    if isinstance(prior, dict) and prior.get("terminal") is True:
        return {**prior, "scheduler_action": "skipped_terminal"}
    paths = project_paths(args.campaign_root, method, fixture)
    log = paths["logs"] / "batch_console.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    command = task_command(args, method, fixture)
    cpu_affinity = None
    if args.isolated_cpu_affinity:
        slot = METHOD_IDS.index(method)
        first = args.cpu_base + slot * args.cpus_per_method
        last = first + args.cpus_per_method - 1
        cpu_affinity = f"{first}-{last}"
        command = ["taskset", "-c", cpu_affinity, *command]
    started_at = now_iso()
    started = time.monotonic()
    timed_out = False
    with log.open("w", encoding="utf-8") as stream:
        stream.write("COMMAND: " + json.dumps(command) + "\n")
        stream.flush()
        process = subprocess.Popen(
            command,
            cwd=REPO,
            stdout=stream,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        register_method_group(process.pid)
        try:
            try:
                returncode = process.wait(timeout=args.wall_time_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    returncode = process.wait(timeout=120)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    returncode = process.wait()
        finally:
            unregister_method_group(process.pid)
    row = {
        "schema_version": "1.0",
        "method_id": method,
        "fixture_id": fixture,
        "started_at": started_at,
        "ended_at": now_iso(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "returncode": returncode,
        "status": "wall_time_limit" if timed_out else ("completed" if returncode == 0 else "method_error"),
        "terminal": True,
        "console_log": str(log),
        "cpu_affinity": cpu_affinity,
    }
    write_json_atomic(result_path, row)
    append_jsonl(args.campaign_root / "batch_events.jsonl", row)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path.home() / ".config" / "r2g" / "experiment1_api.env")
    parser.add_argument("--method", action="append", choices=METHOD_IDS)
    parser.add_argument("--fixture", action="append")
    parser.add_argument("--max-workers", type=int)
    parser.add_argument("--isolated-cpu-affinity", action="store_true")
    parser.add_argument("--cpu-base", type=int, default=0)
    parser.add_argument("--cpus-per-method", type=int, default=4)
    parser.add_argument("--token-budget", type=int, default=300_000)
    parser.add_argument("--wall-time-seconds", type=int, default=14_400)
    parser.add_argument("--max-turns", type=int, default=50)
    parser.add_argument("--max-flow-attempts", type=int, default=4)
    parser.add_argument("--max-output-tokens", type=int, default=2048)
    parser.add_argument("--flow-timeout-seconds", type=int, default=7200)
    parser.add_argument("--summary-path", type=Path)
    args = parser.parse_args()
    for shutdown_signal in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(shutdown_signal, handle_shutdown_signal)
    args.campaign_root = args.campaign_root.resolve()
    try:
        manifest = verify_campaign_bindings(args.campaign_root)
    except Exception as exc:
        parser.error(str(exc))
    cohort = load_cohort(Path(manifest["cohort"]["path"]))
    task_spec = read_json(Path(manifest["task_spec"]["path"]), {}) or {}
    protocol_workers = int((task_spec.get("budgets") or {}).get("max_concurrent_orfs_flows", 1))
    args.max_workers = args.max_workers if args.max_workers is not None else protocol_workers
    methods = args.method or list(METHOD_IDS)
    fixture_ids = args.fixture or [item["id"] for item in cohort["fixtures"]]
    known = {item["id"] for item in cohort["fixtures"]}
    unknown = sorted(set(fixture_ids) - known)
    if unknown:
        parser.error("unknown fixtures: " + ", ".join(unknown))
    if args.max_workers < 1 or args.max_workers > protocol_workers:
        parser.error(f"--max-workers must be within [1, {protocol_workers}] for the bound task spec")
    if args.max_workers > 1 and not args.isolated_cpu_affinity:
        parser.error("parallel Experiment 2 execution requires --isolated-cpu-affinity")
    if args.cpu_base < 0 or args.cpus_per_method < 1:
        parser.error("CPU affinity values must be positive")
    required_cpus = args.cpu_base + len(METHOD_IDS) * args.cpus_per_method
    if args.isolated_cpu_affinity and required_cpus > (os.cpu_count() or 1):
        parser.error(f"CPU affinity requires logical CPU index {required_cpus - 1}, host is smaller")

    summary = []
    for fixture in fixture_ids:
        print(f"[block] {fixture}: {', '.join(methods)}", flush=True)
        with ThreadPoolExecutor(max_workers=min(args.max_workers, len(methods))) as pool:
            futures = {pool.submit(run_one, args, method, fixture): method for method in methods}
            for future in as_completed(futures):
                method = futures[future]
                try:
                    row = future.result()
                except Exception as exc:
                    row = {
                        "schema_version": "1.0",
                        "method_id": method,
                        "fixture_id": fixture,
                        "ended_at": now_iso(),
                        "status": "scheduler_error",
                        "terminal": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                    append_jsonl(args.campaign_root / "batch_events.jsonl", row)
                summary.append(row)
                print(f"[{row['status']}] {row['method_id']} / {fixture} ({row.get('elapsed_seconds', 0)}s)", flush=True)
    write_json_atomic(
        args.summary_path or (args.campaign_root / "batch_summary.json"),
        {"schema_version": "1.0", "updated_at": now_iso(), "results": summary},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
