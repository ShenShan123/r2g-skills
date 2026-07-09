#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import csv
import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from skill_env import default_data_root, default_downloads_root, default_python_bin, default_workspace_root, resolve_path_env

DATA_ROOT = default_data_root()
WORKSPACE_ROOT = default_workspace_root()
PYTHON_BIN = default_python_bin()
RUN_ROUND = SCRIPT_DIR / "run_expansion_round.py"
DISCOVERED_OUT = WORKSPACE_ROOT / "candidates" / "downloads_discovered_candidates_loop.csv"
STATUS_JSON = resolve_path_env("R2G_ACQUIRE_STATUS_JSON", DATA_ROOT / "rtl_acquire_status.json")
STATUS_LOG = resolve_path_env("R2G_ACQUIRE_STATUS_LOG", DATA_ROOT / "rtl_acquire_status.log")


def candidate_count(path: Path) -> int:
    if not path.exists():
        return 0
    return max(0, sum(1 for _ in csv.DictReader(path.open())))


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write_status(payload: dict) -> None:
    STATUS_JSON.parent.mkdir(parents=True, exist_ok=True)
    STATUS_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def pid_is_alive(pid: int | str | None) -> bool:
    try:
        pid_int = int(pid)
    except Exception:
        return False
    if pid_int <= 0:
        return False
    try:
        os.kill(pid_int, 0)
    except OSError:
        return False
    return True


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Repeatedly discover and run graph-expansion rounds until no suitable candidates remain.")
    parser.add_argument("--downloads-root", type=Path, default=default_downloads_root())
    parser.add_argument("--repo-manifest-csv", type=Path, default=None)
    parser.add_argument("--clone-missing", action="store_true")
    parser.add_argument("--priorities", nargs="+", default=["high", "medium", "low"])
    parser.add_argument("--max-rounds", type=int, default=20)
    args = parser.parse_args()
    if STATUS_JSON.exists():
        try:
            previous = json.loads(STATUS_JSON.read_text(encoding="utf-8"))
        except Exception:
            previous = {}
        if previous.get("state") == "running" and not pid_is_alive(previous.get("pid")):
            if candidate_count(DISCOVERED_OUT) == 0:
                previous["state"] = "completed"
                previous["phase"] = "recovered_completed"
                previous["last_output_line"] = "previous expansion loop was no longer alive and candidate list was empty; status auto-recovered to completed"
            else:
                previous["state"] = "stale_interrupted"
                previous["phase"] = "stale_detected"
                previous["last_output_line"] = "previous expansion loop was no longer alive and was marked stale before restart"
            previous["updated_at"] = now_iso()
            write_status(previous)

    payload = {
        "workflow": "nangate45-graph-expander",
        "state": "running",
        "pid": os.getpid(),
        "phase": "loop_init",
        "round_index": 0,
        "max_rounds": args.max_rounds,
        "priorities": args.priorities,
        "downloads_root": str(args.downloads_root),
        "candidate_csv": str(DISCOVERED_OUT),
        "remaining_candidates": None,
        "active_command": "",
        "last_output_line": "",
        "updated_at": now_iso(),
    }
    write_status(payload)

    def _mark_stopped(reason: str, state: str) -> None:
        payload["state"] = state
        payload["phase"] = "stopped"
        payload["active_command"] = ""
        payload["last_output_line"] = reason
        payload["updated_at"] = now_iso()
        write_status(payload)

    def _handle_signal(signum, _frame) -> None:
        _mark_stopped(f"received signal {signum}", "stopped")
        raise SystemExit(128 + int(signum))

    atexit.register(lambda: write_status(payload) if payload.get("state") == "running" else None)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        for round_idx in range(1, args.max_rounds + 1):
            print(f"=== round {round_idx} ===", flush=True)
            payload["phase"] = "round_start"
            payload["round_index"] = round_idx
            payload["active_command"] = str(RUN_ROUND)
            payload["updated_at"] = now_iso()
            write_status(payload)
            run(
                [
                    PYTHON_BIN,
                    str(RUN_ROUND),
                    *(["--repo-manifest-csv", str(args.repo_manifest_csv)] if args.repo_manifest_csv else []),
                    *(["--clone-missing"] if args.clone_missing else []),
                    "--discover",
                    "--downloads-root",
                    str(args.downloads_root),
                    "--discovered-out",
                    str(DISCOVERED_OUT),
                    "--priorities",
                    *args.priorities,
                    "--run-retry",
                    "--round-index",
                    str(round_idx),
                    "--status-json",
                    str(STATUS_JSON),
                    "--status-log",
                    str(STATUS_LOG),
                ]
            )
            remaining = candidate_count(DISCOVERED_OUT)
            payload["phase"] = "round_complete"
            payload["remaining_candidates"] = remaining
            payload["last_output_line"] = f"remaining_candidates_after_round={remaining}"
            payload["updated_at"] = now_iso()
            write_status(payload)
            print(f"remaining_candidates_after_round={remaining}", flush=True)
            if remaining == 0:
                payload["state"] = "completed"
                payload["phase"] = "done"
                payload["active_command"] = ""
                payload["updated_at"] = now_iso()
                write_status(payload)
                print("all currently suitable discovered candidates are exhausted", flush=True)
                return

        payload["state"] = "stopped"
        payload["phase"] = "max_rounds_reached"
        payload["active_command"] = ""
        payload["updated_at"] = now_iso()
        write_status(payload)
        print("reached max_rounds before candidate list was exhausted", flush=True)
    except Exception as exc:
        payload["state"] = "failed"
        payload["phase"] = "error"
        payload["active_command"] = ""
        payload["last_output_line"] = f"{type(exc).__name__}: {exc}"
        payload["updated_at"] = now_iso()
        write_status(payload)
        raise


if __name__ == "__main__":
    main()
