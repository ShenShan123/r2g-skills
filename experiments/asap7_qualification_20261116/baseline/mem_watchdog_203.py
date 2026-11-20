#!/usr/bin/env python3
"""Memory watchdog for the ASAP7 baseline on memlab203 (runs ON 203, our own flows only).

The 203 user cgroup is capped at 300 GiB (throttling from 280). The driver's --mem-pause-gb guard only stops NEW
launches; a single running flow can still grow to the cap. Two designs did exactly that on 2026-11-17 (C01031,
C03200: floorplan-stage repair_timing on a hugely violating netlist, ~280 GiB each), which throttles every other
flow and makes OpenROAD die with Signal 11 -- results that describe the machine, not the design.

Every --interval s: if the cgroup's anonymous memory exceeds --trigger-gb, find the baseline driver's flow
process groups, sum each group's RSS from /proc/<pid>/stat, and terminate the largest group if it alone holds more
than --min-group-gb (TERM, 15 s, KILL). The design is appended to rerun_on_216.txt (re-run on 216's 1.1 TB, same
frozen spec, so its verdict is still earned on the same ruler) and, once the driver writes its result.json, the
record is annotated `failure_class: operator_killed_memory` with a note -- never left looking like a design failure.
Never touches a process that is not a child group of OUR driver.
"""
import argparse
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path

B = Path("/data/yangao/asap7_baseline")
PAGE = os.sysconf("SC_PAGE_SIZE")


def anon_gib() -> float:
    uid = os.getuid()
    for line in Path(f"/sys/fs/cgroup/user.slice/user-{uid}.slice/memory.stat").read_text().splitlines():
        if line.startswith("anon "):
            return int(line.split()[1]) / 2**30
    return 0.0


def driver_pid() -> int | None:
    r = subprocess.run(["pgrep", "-f", "orfs_spec.py.*baseline.select"], capture_output=True, text=True)
    pids = [int(x) for x in r.stdout.split()]
    return pids[0] if pids else None


def groups(drv: int) -> dict:
    """{pgid: design} for the driver's child flow groups."""
    out = {}
    for t in Path(f"/proc/{drv}/task").iterdir():
        try:
            kids = (t / "children").read_text().split()
        except OSError:
            continue
        for k in kids:
            design = None
            try:
                for fd in Path(f"/proc/{k}/fd").iterdir():
                    m = re.search(r"baseline_scratch/(C\d{5}__[^/]+?)__asap7__", os.readlink(fd))
                    if m:
                        design = m.group(1)
                        break
                pgid = int(Path(f"/proc/{k}/stat").read_text().rsplit(")", 1)[1].split()[2])
            except (OSError, ValueError, IndexError):
                continue
            if design:
                out[pgid] = design
    return out


def group_rss_gib(pgids) -> dict:
    tot = {g: 0 for g in pgids}
    uid = os.getuid()
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            if p.stat().st_uid != uid:
                continue
            f = (p / "stat").read_text().rsplit(")", 1)[1].split()
            pg, rss = int(f[2]), int(f[21])
        except (OSError, ValueError, IndexError):
            continue
        if pg in tot:
            tot[pg] += rss * PAGE
    return {g: v / 2**30 for g, v in tot.items()}


def annotate(design: str, gib: float, when: str) -> bool:
    runs = sorted((B / "baseline/runs").glob(design + "__asap7__*"))
    if not runs or not (runs[0] / "result.json").is_file():
        return False
    p = runs[0] / "result.json"
    r = json.loads(p.read_text())
    if r.get("failure_class") == "operator_killed_memory":
        return True
    r["failure_class"] = "operator_killed_memory"
    r["operator_note"] = (f"{when} killed by mem_watchdog_203: this flow held ~{gib:.0f} GiB of the 203 user "
                          "cgroup (limit 300 GiB). Not a design verdict: re-run on memlab216.")
    p.write_text(json.dumps(r, indent=1))
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=int, default=20)
    ap.add_argument("--trigger-gb", type=float, default=230)
    ap.add_argument("--min-group-gb", type=float, default=120)
    a = ap.parse_args()
    pending = {}
    log = open(B / "mem_watchdog.log", "a", buffering=1)
    while True:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        for d, (gib, when) in list(pending.items()):
            if annotate(d, gib, when):
                log.write(f"{now} annotated {d}\n")
                pending.pop(d)
        try:
            anon = anon_gib()
        except OSError:
            anon = 0.0
        drv = driver_pid()
        if anon > a.trigger_gb and drv:
            g = groups(drv)
            rss = group_rss_gib(g)
            if rss:
                pg, gib = max(rss.items(), key=lambda kv: kv[1])
                if gib > a.min_group_gb:
                    design = g[pg]
                    log.write(f"{now} anon {anon:.0f} GiB; killing {design} (pgid {pg}, {gib:.0f} GiB)\n")
                    for sig, wait in ((signal.SIGTERM, 15), (signal.SIGKILL, 5)):
                        try:
                            os.killpg(pg, sig)
                        except ProcessLookupError:
                            break
                        time.sleep(wait)
                    with open(B / "rerun_on_216.txt", "a") as f:
                        f.write(design + "\n")
                    pending[design] = (gib, now)
                else:
                    log.write(f"{now} anon {anon:.0f} GiB but largest group {gib:.0f} GiB < {a.min_group_gb}; "
                              "leaving it to the dispatch guard\n")
        if not drv and not pending:
            log.write(f"{now} driver gone, nothing pending: exit\n")
            return
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
