#!/usr/bin/env python3
"""Lightweight status/finalization monitor for a frozen reproduction campaign."""

from __future__ import annotations

from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import subprocess
import time


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    temporary.replace(path)


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def monitor(root: Path, interval_seconds: int) -> None:
    total = json.loads((root / "plan.json").read_text())["runnable"]
    while True:
        status_run = run(["python3", str(root / "runner.py"), "status"])
        summary_run = run([
            "python3", str(root / "summarize_reproduction.py"), "--campaign", str(root)
        ])
        status = json.loads((root / "status.json").read_text())
        counts = status["counts"]
        completed = sum(
            value
            for key, value in counts.items()
            if key not in {"queued", "started_not_finalized", "needs_review"}
        )
        active_workers = sum(
            1 for value in status.get("workers", {}).values() if value.get("stage") == "physical"
        )
        anomaly = None
        if counts.get("needs_review"):
            anomaly = "needs_review_requires_human_inspection"
        elif completed < total and not counts.get("queued") and not counts.get("started_not_finalized"):
            anomaly = "incomplete_without_runnable_work"
        save(
            root / "supervision_status.json",
            {
                "updated_at": now(),
                "total": total,
                "completed": completed,
                "active_workers_last_reported": active_workers,
                "counts": counts,
                "anomaly": anomaly,
                "status_command_returncode": status_run.returncode,
                "summary_command_returncode": summary_run.returncode,
            },
        )
        if completed == total or anomaly:
            return
        time.sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--interval-seconds", type=int, default=1800)
    args = parser.parse_args()
    monitor(args.campaign.resolve(), args.interval_seconds)


if __name__ == "__main__":
    main()
