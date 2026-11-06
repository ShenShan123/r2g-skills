#!/usr/bin/env python3
"""Per-design memory bound for the bare / fallback arms (Option A, 2026-11-06, user decision).

Every INTERVAL seconds, look at this user's processes. One whose RSS+swap exceeds LIMIT_GB is an EDA step
of an attempt that has hit the bound: write <attempt>/MEMORY_BOUND.json (run_bare.py then ends the attempt
as memory_bound -- not fixed, cost counted up to that point) and kill the step (TERM, KILL after GRACE s).

The attempt directory comes from the step's own environment (R2G_ORFS_AUDIT_FILE=<attempt>/orfs_audit.jsonl,
set by run_bare for every EDA step), so it works for host processes (203) and for rootless containers whose
processes are visible on the host with the same paths (216). A process without that variable is logged and
left alone -- it is not a bare-arm step. Replaces systemd-run scopes (203 stalls) and podman --memory (ignored
by rootless cgroup v1 on 216).

  mem_bound_watchdog.py [--limit-gb 64] [--interval 15] [--log FILE] [--dry-run]
"""
import argparse, json, os, signal, time
from pathlib import Path

ME = os.getuid()


def kb(status: str, key: str) -> int:
    for line in status.splitlines():
        if line.startswith(key + ":"):
            return int(line.split()[1])
    return 0


def attempt_dir(pid: int):
    try:
        env = Path("/proc/%d/environ" % pid).read_bytes().split(b"\0")
    except OSError:
        return None
    for e in env:
        if e.startswith(b"R2G_ORFS_AUDIT_FILE="):
            return Path(e.split(b"=", 1)[1].decode()).parent
    return None


def scan(limit_kb: int):
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            if p.stat().st_uid != ME:
                continue
            st = (p / "status").read_text()
        except OSError:
            continue
        use = kb(st, "VmRSS") + kb(st, "VmSwap")
        if use > limit_kb:
            yield int(p.name), use, (p / "comm").read_text().strip() if (p / "comm").exists() else "?"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit-gb", type=float, default=64.0)
    ap.add_argument("--interval", type=float, default=15.0)
    ap.add_argument("--grace", type=float, default=15.0)
    ap.add_argument("--log", type=Path, default=Path.home() / "mem_bound_watchdog.log")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    limit_kb = int(a.limit_gb * 1048576)
    while True:
        for pid, use, comm in scan(limit_kb):
            d = attempt_dir(pid)
            try:
                cmd = Path("/proc/%d/cmdline" % pid).read_bytes().replace(b"\0", b" ").decode(errors="replace")[:300]
            except OSError:
                cmd = ""
            rec = {"at": time.time(), "at_text": time.strftime("%F %T"), "pid": pid, "comm": comm,
                   "rss_swap_gb": round(use / 1048576, 1), "limit_gb": a.limit_gb, "cmd": cmd,
                   "attempt": str(d) if d else None, "dry_run": a.dry_run}
            with a.log.open("a") as fh:
                fh.write(json.dumps(rec) + "\n")
            if d is None or a.dry_run:
                continue
            m = d / "MEMORY_BOUND.json"
            if not m.exists():
                m.write_text(json.dumps(rec, indent=1))
            try:
                os.kill(pid, signal.SIGTERM)
                t0 = time.time()
                while time.time() - t0 < a.grace and Path("/proc/%d" % pid).exists():
                    time.sleep(1)
                if Path("/proc/%d" % pid).exists():
                    os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if a.once:
            return
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
