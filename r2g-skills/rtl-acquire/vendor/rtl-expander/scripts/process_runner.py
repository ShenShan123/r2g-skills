#!/usr/bin/env python3
"""Bounded-memory subprocess execution with durable logs and heartbeats."""

from __future__ import annotations

import datetime as dt
import os
import selectors
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def run_streamed(
    command: list[str], log_dir: Path, stage: str, *,
    heartbeat: Callable[[dict[str, Any]], None] | None = None,
    heartbeat_seconds: float = 15.0, tail_bytes: int = 64 * 1024,
) -> dict[str, Any]:
    """Stream stdout/stderr to files and retain only bounded tails in memory."""
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{stage}.stdout.log"
    stderr_path = log_dir / f"{stage}.stderr.log"
    started_at = utc_now()
    started = time.monotonic()
    with stdout_path.open("ab", buffering=0) as stdout_log, stderr_path.open("ab", buffering=0) as stderr_log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert process.stdout is not None and process.stderr is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, (stdout_log, "stdout"))
        selector.register(process.stderr, selectors.EVENT_READ, (stderr_log, "stderr"))
        tails: dict[str, deque[bytes]] = {"stdout": deque(), "stderr": deque()}
        tail_sizes = {"stdout": 0, "stderr": 0}
        last_output_at = started_at
        next_heartbeat = time.monotonic()
        while selector.get_map():
            for key, _ in selector.select(timeout=1.0):
                chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                log, stream = key.data
                log.write(chunk)
                tails[stream].append(chunk)
                tail_sizes[stream] += len(chunk)
                while tail_sizes[stream] > tail_bytes and tails[stream]:
                    removed = tails[stream].popleft()
                    tail_sizes[stream] -= len(removed)
                last_output_at = utc_now()
            if heartbeat and time.monotonic() >= next_heartbeat:
                heartbeat({
                    "child_pid": process.pid, "heartbeat_at": utc_now(),
                    "last_output_at": last_output_at,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "stdout_log": str(stdout_path), "stderr_log": str(stderr_path),
                })
                next_heartbeat = time.monotonic() + max(1.0, heartbeat_seconds)
        returncode = process.wait()
        process.stdout.close()
        process.stderr.close()
    decode = lambda chunks: b"".join(chunks)[-tail_bytes:].decode("utf-8", "replace")
    return {
        "started_at": started_at, "completed_at": utc_now(),
        "returncode": returncode, "child_pid": process.pid,
        "stdout_log": str(stdout_path), "stderr_log": str(stderr_path),
        "stdout_tail": decode(tails["stdout"]), "stderr_tail": decode(tails["stderr"]),
        "last_output_at": last_output_at,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
