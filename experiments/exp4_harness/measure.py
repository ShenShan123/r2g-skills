#!/usr/bin/env python3
"""Measure a complete converter process tree inside its isolated container."""
import argparse
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import time


def tree_rss(pid):
    total, pending, seen = 0, [pid], set()
    while pending:
        p = pending.pop()
        if p in seen:
            continue
        seen.add(p)
        try:
            lines = Path(f"/proc/{p}/status").read_text().splitlines()
            total += next((int(s.split()[1]) * 1024 for s in lines if s.startswith("VmRSS:")), 0)
            for task in Path(f"/proc/{p}/task").iterdir():
                pending.extend(map(int, (task / "children").read_text().split()))
        except OSError:
            pass
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--seconds", type=int, default=1800)
    ap.add_argument("--cpu-count", type=int, default=8)
    ap.add_argument("--cpuset", default="")
    ap.add_argument("--memory-gib", type=int, default=32)
    ap.add_argument("command", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cpus = list(map(int, a.cpuset.split(','))) if a.cpuset else sorted(os.sched_getaffinity(0))[:a.cpu_count]
    os.sched_setaffinity(0, cpus)
    command = a.command[1:] if a.command[0] == "--" else a.command
    t0, peak, stop = time.monotonic(), 0, None
    p = subprocess.Popen(command, start_new_session=True)
    while p.poll() is None:
        peak = max(peak, tree_rss(p.pid))
        if time.monotonic() - t0 >= a.seconds:
            stop = "timeout"
        elif peak > a.memory_gib * 1024 ** 3:
            stop = "memory_limit"
        if stop:
            os.killpg(p.pid, signal.SIGKILL)
            break
        # Image conversions can finish in under a second. A half-second sleep
        # noticeably rounds their wall time and can miss the process RSS peak.
        try:
            p.wait(timeout=.05)
        except subprocess.TimeoutExpired:
            pass
    p.wait()
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    a.report.write_text(json.dumps({"exit_code": p.returncode, "stop_reason": stop or "finished",
        "wall_seconds": round(time.monotonic() - t0, 3), "cpu_seconds": ru.ru_utime + ru.ru_stime,
        "peak_process_tree_rss_bytes": peak, "cpus": cpus}, indent=2))
    return p.returncode


if __name__ == "__main__":
    raise SystemExit(main())
