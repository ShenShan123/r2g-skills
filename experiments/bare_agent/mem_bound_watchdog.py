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

With --total-cap-gb C it is also a slice guard: when this user's cgroup working set (memory.current -
inactive_file of the nearest memory-limited ancestor) exceeds C, the YOUNGEST running attempt (latest
case.json) is stopped -- its run_bare.py and its EDA steps -- and <attempt>/INFRA_INTERRUPTED.json is written.
That is an infrastructure interruption, not a memory_bound outcome: the next launcher start moves the attempt
to interrupted/ and reruns it. It keeps many concurrent designs from adding up to the slice limit.

  mem_bound_watchdog.py [--limit-gb 64] [--total-cap-gb 0] [--interval 15] [--log FILE] [--dry-run]
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


def slice_working_set_gb():
    """Working set of the nearest memory-limited cgroup v2 ancestor of this process (None if unknown)."""
    try:
        rel = next(l.split("::", 1)[1].strip() for l in open("/proc/self/cgroup") if l.startswith("0::"))
    except (OSError, StopIteration):
        return None
    d = Path("/sys/fs/cgroup" + rel)
    while d != Path("/sys/fs/cgroup") and d.parent != d:
        try:
            if (d / "memory.max").read_text().strip() != "max":
                cur = int((d / "memory.current").read_text())
                inact = next((int(l.split()[1]) for l in (d / "memory.stat").read_text().splitlines()
                              if l.startswith("inactive_file ")), 0)
                return (cur - inact) / 2**30
        except (OSError, ValueError):
            pass
        d = d.parent
    return None


def attempts():
    """attempt dir -> pids of this user's processes that belong to it (run_bare.py or an EDA step)."""
    out = {}
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            if p.stat().st_uid != ME:
                continue
            cmd = (p / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        d = attempt_dir(int(p.name))
        if d is None and b"--case" in cmd:
            try:
                c = cmd[cmd.index(b"--case") + 1].decode()
                if c.endswith("/case.json") and any(b.endswith(b"run_bare.py") for b in cmd):
                    d = Path(c).parent
            except (ValueError, IndexError):
                pass
        if d is not None:
            out.setdefault(d, []).append(int(p.name))
    return out


def stop_youngest(log, total_gb, cap_gb, grace, dry):
    live = {d: pids for d, pids in attempts().items() if (d / "case.json").exists() and not (d / "result.json").exists()}
    if not live:
        return
    d = max(live, key=lambda x: (x / "case.json").stat().st_mtime)
    rec = {"at": time.time(), "at_text": time.strftime("%F %T"), "slice_working_set_gb": round(total_gb, 1),
           "total_cap_gb": cap_gb, "attempt": str(d), "pids": live[d], "dry_run": dry,
           "reason": "slice guard: our total memory passed the cap; youngest attempt stopped (infrastructure "
                     "interruption, rerun later -- not a memory_bound outcome)"}
    with log.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    if dry:
        return
    (d / "INFRA_INTERRUPTED.json").write_text(json.dumps(rec, indent=1))
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in live[d]:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        time.sleep(grace if sig == signal.SIGTERM else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit-gb", type=float, default=64.0)
    ap.add_argument("--total-cap-gb", type=float, default=0.0, help="slice guard (0 = off)")
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
        if a.total_cap_gb:
            ws = slice_working_set_gb()
            if ws is not None and ws > a.total_cap_gb:
                stop_youngest(a.log, ws, a.total_cap_gb, a.grace, a.dry_run)
        if a.once:
            return
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
