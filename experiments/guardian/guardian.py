#!/usr/bin/env python3
"""guardian.py -- unattended resource protection for OUR EDA jobs on memlab203 / memlab216.

Run by cron once a minute under flock (see install_guardian.sh); one pass, then exit, so there is no daemon that
can die unnoticed. It only ever acts on processes owned by the invoking user whose command is an EDA tool
(TOOLS below) or on that user's podman containers, and on the user's own flow drivers (SIGSTOP/SIGCONT only).
Other users' processes are read for nothing and touched never.

Each pass:
  memory   group our tool processes by process group, sum RSS per group (/proc/<pid>/stat), then kill a group
           (TERM, 15 s, KILL) when
             - one group exceeds --group-bound-gib (declared per-design bound, default 200 GiB; on 2026-11-17 the
               only designs past it were floorplan repair_timing runaways, every real design stayed < 40 GiB), or
             - the PRESSURE signal fires and the largest group holds more than --pressure-min-gib:
                 203: our user cgroup's anon memory > --cgroup-act-gib (cap there is 300 GiB, throttling at 280);
                 216: host MemAvailable < --avail-floor-gib (a shared host: other people's jobs come first).
           At most one kill per pass; the next pass re-measures.
  disk     free space under each --watch-dir below --disk-stop-gib: SIGSTOP our flow drivers (DRIVERS below) so no
           new design starts; SIGCONT them once free space is back above --disk-resume-gib.
  stale    our podman containers older than --container-max-h (stage timeout + grace) are removed; our tool
           processes re-parented to init (ppid 1) and older than --orphan-max-h are killed (a killed driver leaves
           its flow tree running otherwise).
  record   every action is one JSON line in <state>/guardian.jsonl. A killed flow's design is looked up by its
           DESIGN_NAME and, once its driver writes result.json (any run dir under --runs-root modified after the
           kill), the record is annotated failure_class=guardian_<reason> with a note -- never left looking like a
           design verdict. Pending annotations persist in <state>/pending.json.
  health   <state>/heartbeat.json is rewritten every pass (memory, disk, our group sizes, paused drivers).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

TOOLS = re.compile(r"^(openroad|yosys|yosys-abc|klayout|calibre|calibr\w*|rve|magic|netgen|netgen-lvs|sta|iverilog|vvp)$")
DRIVERS = re.compile(r"(orfs_spec\.py|orfs_baseline\.py|run_experiment\w*\.py|engineer_loop\.py|repair_\w+\.py)")
PAGE = os.sysconf("SC_PAGE_SIZE")
UID = os.getuid()
GIB = 2 ** 30


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def our_procs() -> list[dict]:
    out = []
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            if p.stat().st_uid != UID:
                continue
            stat = (p / "stat").read_text()
            comm = stat[stat.index("(") + 1:stat.rindex(")")]
            f = stat[stat.rindex(")") + 2:].split()
            out.append({"pid": int(p.name), "comm": comm, "ppid": int(f[1]), "pgid": int(f[2]),
                        "start": int(f[19]), "rss": int(f[21]) * PAGE})
        except (OSError, ValueError, IndexError):
            continue
    return out


def cmdline(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        return ""


def age_s(start_ticks: int) -> float:
    up = float(Path("/proc/uptime").read_text().split()[0])
    return up - start_ticks / os.sysconf("SC_CLK_TCK")


def tool_groups(procs: list[dict]) -> dict:
    """{pgid: {rss, pids, tools, design}} for process groups that contain at least one EDA tool."""
    groups: dict[int, dict] = {}
    for p in procs:
        groups.setdefault(p["pgid"], {"rss": 0, "pids": [], "tools": set(), "design": None})
        g = groups[p["pgid"]]
        g["rss"] += p["rss"]
        g["pids"].append(p["pid"])
        if TOOLS.match(p["comm"]):
            g["tools"].add(p["comm"])
            if g["design"] is None:
                m = re.search(r"/logs/[^/]+/([^/\s]+)/", cmdline(p["pid"]))
                if m:
                    g["design"] = m.group(1)
    return {k: v for k, v in groups.items() if v["tools"] and k > 1}


def kill_group(pgid: int) -> None:
    for sig, wait in ((signal.SIGTERM, 15), (signal.SIGKILL, 3)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        time.sleep(wait)


def cgroup_anon_gib() -> float | None:
    p = Path(f"/sys/fs/cgroup/user.slice/user-{UID}.slice/memory.stat")
    try:
        for line in p.read_text().splitlines():
            if line.startswith("anon "):
                return int(line.split()[1]) / GIB
    except OSError:
        return None
    return None


def mem_available_gib() -> float:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024 / GIB
    return 0.0


def podman_containers() -> list[dict]:
    if not shutil.which("podman"):
        return []
    r = subprocess.run(["podman", "ps", "--format", "{{.Names}}\t{{.StartedAt}}"], capture_output=True,
                       text=True, timeout=60)
    out = []
    for line in r.stdout.splitlines():
        name, _, started = line.partition("\t")
        ins = subprocess.run(["podman", "inspect", "--format", "{{.State.StartedAt.Unix}}", name],
                             capture_output=True, text=True, timeout=60)
        try:
            out.append({"name": name, "age_s": time.time() - int(ins.stdout.strip())})
        except ValueError:
            continue
    return out


def annotate_pending(state: Path, runs_roots: list[str], log) -> None:
    pf = state / "pending.json"
    pending = json.loads(pf.read_text()) if pf.is_file() else []
    keep = []
    for item in pending:
        done = False
        for root in runs_roots:
            for rj in Path(root).glob("*/runs/*/result.json"):
                if rj.stat().st_mtime < item["t_epoch"]:
                    continue
                cfg = rj.parent / "config.mk"
                try:
                    if not re.search(r"^export DESIGN_NAME\s*=\s*%s\s*$" % re.escape(item["design"]),
                                     cfg.read_text(), re.M):
                        continue
                    r = json.loads(rj.read_text())
                except (OSError, ValueError):
                    continue
                if r.get("status") == "pass" or str(r.get("failure_class", "")).startswith("guardian_"):
                    done = True
                    continue
                r["failure_class"] = "guardian_" + item["reason"]
                r["operator_note"] = ("%s killed by guardian.py (%s, %.0f GiB). A resource action, not a design "
                                      "verdict." % (item["t"], item["reason"], item["gib"]))
                rj.write_text(json.dumps(r, indent=1))
                log({"action": "annotated", "result": str(rj), "reason": item["reason"]})
                done = True
        if not done and time.time() - item["t_epoch"] < 6 * 3600:
            keep.append(item)
    pf.write_text(json.dumps(keep, indent=1))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", required=True, type=Path)
    ap.add_argument("--watch-dir", action="append", default=[])
    ap.add_argument("--runs-root", action="append", default=[],
                    help="directory whose */runs/*/result.json the guardian may annotate after a kill")
    ap.add_argument("--group-bound-gib", type=float, default=200)
    ap.add_argument("--pressure-min-gib", type=float, default=60)
    ap.add_argument("--cgroup-act-gib", type=float, default=0, help="203: act when our cgroup anon exceeds this")
    ap.add_argument("--avail-floor-gib", type=float, default=0, help="216: act when MemAvailable falls below this")
    ap.add_argument("--disk-stop-gib", type=float, default=100)
    ap.add_argument("--disk-resume-gib", type=float, default=150)
    ap.add_argument("--container-max-h", type=float, default=6.75)
    ap.add_argument("--orphan-max-h", type=float, default=7.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    a.state.mkdir(parents=True, exist_ok=True)
    lock = open(a.state / "pass.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0
    jl = open(a.state / "guardian.jsonl", "a", buffering=1)

    def log(rec: dict) -> None:
        rec = {"t": now(), "dry_run": a.dry_run, **rec}
        jl.write(json.dumps(rec) + "\n")

    procs = our_procs()
    groups = tool_groups(procs)
    anon = cgroup_anon_gib()
    avail = mem_available_gib()

    # ---- memory ----
    killed = None
    if groups:
        pg, g = max(groups.items(), key=lambda kv: kv[1]["rss"])
        gib = g["rss"] / GIB
        pressure = ((a.cgroup_act_gib and anon is not None and anon > a.cgroup_act_gib)
                    or (a.avail_floor_gib and avail < a.avail_floor_gib))
        reason = ("group_bound" if gib > a.group_bound_gib
                  else "memory_pressure" if pressure and gib > a.pressure_min_gib else None)
        if reason:
            log({"action": "kill_group", "reason": reason, "pgid": pg, "gib": round(gib, 1),
                 "tools": sorted(g["tools"]), "design": g["design"], "cgroup_anon_gib": anon,
                 "mem_available_gib": round(avail, 1)})
            if not a.dry_run:
                kill_group(pg)
                if g["design"]:
                    pf = a.state / "pending.json"
                    pend = json.loads(pf.read_text()) if pf.is_file() else []
                    pend.append({"design": g["design"], "reason": reason, "gib": gib, "t": now(),
                                 "t_epoch": time.time()})
                    pf.write_text(json.dumps(pend, indent=1))
            killed = pg
        elif pressure:
            log({"action": "pressure_no_target", "largest_gib": round(gib, 1), "cgroup_anon_gib": anon,
                 "mem_available_gib": round(avail, 1)})

    # ---- disk ----
    paused_f = a.state / "paused_drivers.json"
    paused = json.loads(paused_f.read_text()) if paused_f.is_file() else []
    drivers = [p for p in procs if p["comm"].startswith("python") and DRIVERS.search(cmdline(p["pid"]))]
    disk = {}
    for d in a.watch_dir:
        st = os.statvfs(d)
        disk[d] = round(st.f_bavail * st.f_frsize / GIB, 1)
    low = [d for d, gib in disk.items() if gib < a.disk_stop_gib]
    ok = all(gib > a.disk_resume_gib for gib in disk.values())
    if low:
        for p in drivers:
            if p["pid"] not in paused:
                log({"action": "pause_driver", "pid": p["pid"], "cmd": cmdline(p["pid"])[:200], "disk": disk})
                if not a.dry_run:
                    os.kill(p["pid"], signal.SIGSTOP)
                    paused.append(p["pid"])
    elif ok and paused:
        for pid in paused:
            log({"action": "resume_driver", "pid": pid, "disk": disk})
            if not a.dry_run:
                try:
                    os.kill(pid, signal.SIGCONT)
                except ProcessLookupError:
                    pass
        paused = []
    paused_f.write_text(json.dumps(paused))

    # ---- stale containers / orphans ----
    for c in podman_containers():
        if c["age_s"] > a.container_max_h * 3600:
            log({"action": "rm_container", "name": c["name"], "age_h": round(c["age_s"] / 3600, 2)})
            if not a.dry_run:
                subprocess.run(["podman", "rm", "-f", "-t", "5", c["name"]], capture_output=True, timeout=120)
    for p in procs:
        if (p["ppid"] == 1 and TOOLS.match(p["comm"]) and p["pgid"] != killed
                and age_s(p["start"]) > a.orphan_max_h * 3600):
            log({"action": "kill_orphan", "pid": p["pid"], "pgid": p["pgid"], "comm": p["comm"],
                 "age_h": round(age_s(p["start"]) / 3600, 2)})
            if not a.dry_run:
                kill_group(p["pgid"])

    if not a.dry_run:
        annotate_pending(a.state, a.runs_root, log)

    top = sorted(groups.values(), key=lambda g: -g["rss"])[:5]
    (a.state / "heartbeat.json").write_text(json.dumps({
        "t": now(), "cgroup_anon_gib": anon, "mem_available_gib": round(avail, 1), "disk_free_gib": disk,
        "paused_drivers": paused, "drivers": len(drivers),
        "largest_groups": [{"gib": round(g["rss"] / GIB, 1), "tools": sorted(g["tools"]), "design": g["design"]}
                           for g in top]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
