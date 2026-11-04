#!/usr/bin/env python3
"""Run the bare-agent arm over a sample: one subprocess per design, N at a time.

Each design gets <root>/cases/<id>/ (run_bare.py output). A design with a
result.json is skipped, so the launcher can be re-run to resume. After a design
finishes, its ORFS workspace (flow/{results,logs,objects,reports}/<variant>) is
removed; the project keeps its own archived runs, reports and logs.
"""
import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


START_LOCK = __import__("threading").Lock()


def wait_for_load(a):
    """Admission gate, one design at a time: every waiting worker used to read
    the same momentary dip in the load average and start together (216,
    2026-10-29: 19 starts at once after load fell below the gate). A start is
    followed by --start-spacing seconds before the next check, so the load
    average can register it."""
    with START_LOCK:
        _wait_for_load(a)
        time.sleep(getattr(a, "start_spacing", 0))


def _wait_for_load(a):
    """Start a design only while the machine has headroom (shared hosts: other
    users' jobs come and go). Contention would turn stage timeouts and the wall
    budget into failures that say nothing about the arm being measured."""
    cpus = os.cpu_count() or 1
    while ((a.max_load and os.getloadavg()[0] > a.max_load * cpus)
           or (getattr(a, "min_mem_gb", 0) and mem_available_gb() < a.min_mem_gb)
           or (getattr(a, "min_disk_gb", 0) and disk_free_gb(a.root) < a.min_disk_gb)):
        time.sleep(60)


def disk_free_gb(path) -> float:
    """Free space on the work root's filesystem (203, 2026-11-04: /data reached 2 GB
    while big designs ran; three attempts hit ENOSPC mid-flow and had to be voided).
    Admission only -- a running design is never touched."""
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize / 1e9
    except OSError:
        return float("inf")


def mem_available_gb() -> float:
    """MemAvailable from /proc/meminfo (203, 2026-10-29: swap full and new
    runners died at start with ENOMEM while load looked fine)."""
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1048576
    except OSError:
        pass
    return float("inf")


def live_runner(out: Path):
    """PID of a run_bare.py still working on this case directory (e.g. its
    launcher was restarted to rebalance hosts), else None."""
    needle = str(out / "case.json").encode()
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            cmd = (proc / "cmdline").read_bytes()
        except OSError:
            continue
        if b"run_bare.py" in cmd and needle in cmd:
            return int(proc.name)
    return None


def container_cmd(cmd, case, a):
    """Run the runner in the ORFS image (216: host glibc too old for the tools).

    The design's ORFS workspace (results/logs/objects/reports/designs) is a
    per-design host directory mounted over the image's flow/; work root,
    runtime, inputs and signoff tools are mounted at their host paths, so every
    path in the case and the transcript reads the same as on the other hosts."""
    ws = a.container_ws / case["id"]
    mounts = []
    for sub in ("results", "logs", "objects", "reports", "designs"):
        (ws / sub).mkdir(parents=True, exist_ok=True)
        mounts += ["-v", "%s:%s/flow/%s:Z" % (ws / sub, a.container_orfs, sub)]
    for m in a.container_mount:
        mounts += ["-v", m]
    mem = ["--memory", a.container_memory, "--memory-swap", a.container_memory] if getattr(a, "container_memory", "") else []
    # the prefix keeps two arms on one host from colliding on a design's container
    # name (a second `podman run` of the same name fails and voids that attempt)
    name = getattr(a, "container_name_prefix", "bare_") + case["id"]
    return (["podman", "run", "--rm", "--network", "host", "--name", name] + mem + [
             "--env-file", str(a.container_env_file), "-w", str(a.repo)] + mounts
            + [a.container_image, "python3"] + cmd[1:])


def kill_leftovers(case):
    """A runner that dies (or is killed) leaves its EDA steps running: they run in
    their own sessions. Reap every process naming this design's variant (203,
    2026-10-30: an orphaned floorplan held 172 GB for 22 h)."""
    tag = (case["id"] + "_bare").encode()
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            if tag in (proc / "cmdline").read_bytes():
                os.kill(int(proc.name), 9)
        except (OSError, ProcessLookupError):
            continue


def cleanup_workspace(case, a=None):
    if a is not None and a.container_image:
        shutil.rmtree(a.container_ws / case["id"], ignore_errors=True)
        return
    kill_leftovers(case)
    variant = case["id"] + "_bare"
    flow = Path(os.environ["ORFS_ROOT"]) / "flow"
    for sub in ("results", "logs", "objects", "reports"):
        for d in (flow / sub / case["platform"]).glob("*/" + variant):
            shutil.rmtree(d, ignore_errors=True)


def run_one(case, a):
    out = a.root / "cases" / case["id"]
    if (out / "result.json").exists():
        return case["id"], "skipped"
    pid = live_runner(out) if out.exists() else None
    if pid:
        # adopt the running attempt instead of restarting it
        while live_runner(out):
            time.sleep(30)
        return case["id"], "adopted pid %d" % pid
    if out.exists():
        # interrupted by a harness restart (never by the model): keep it for the
        # record, outside the measured cohort, and start the design fresh
        dest = a.root / "interrupted" / ("%s_%d" % (case["id"], int(time.time())))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(out), str(dest))
    for attempt in (1, 2):
        wait_for_load(a)
        out.mkdir(parents=True, exist_ok=True)
        (out / "case.json").write_text(json.dumps(case, indent=1, ensure_ascii=False))
        baseline = a.baselines / case["id"]
        cmd = [sys.executable, str(HERE / "run_bare.py"), "--case", str(out / "case.json"),
               "--baseline", str(baseline), "--out", str(out), "--repo", str(a.repo),
               "--base-url", a.base_url, "--model", a.model, "--max-tokens", str(a.max_tokens)]
        if a.caps:
            cmd += ["--caps", a.caps]
        if a.protocol != "openai":
            cmd += ["--protocol", a.protocol]
        if a.reasoning_effort:
            cmd += ["--reasoning-effort", a.reasoning_effort]
        if case.get("from_project"):
            # LLM + R2G arm: fallback from R2G's final project (hybrid.py queue)
            cmd += ["--from-project", case["from_project"], "--reason", case.get("reason", "")]
            if case.get("context"):
                cmd += ["--context", case["context"]]
        if a.container_image:
            cmd = container_cmd(cmd, case, a)
        t0 = time.time()
        with (out / "runner.log").open("ab") as log:
            rc = subprocess.call(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=a.repo)
        res = out / "result.json"
        if attempt == 1 and res.is_file() and not json.loads(res.read_text()).get("usage_complete"):
            # voided by the invocation (a stream ended without provider usage), not
            # by the model: kept outside the cohort, one fresh attempt (PROTOCOL.md)
            cleanup_workspace(case, a)
            dest = a.root / "voided" / ("%s_%d" % (case["id"], int(time.time())))
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(out), str(dest))
            continue
        break
    cleanup_workspace(case, a)
    return case["id"], "rc=%s %.0fs attempt %d" % (rc, time.time() - t0, attempt)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", type=Path, required=True, help="JSON list of population manifest entries")
    ap.add_argument("--baselines", type=Path, required=True, help="dir of <case>/{config.mk,constraint.sdc}")
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--model", default="deepseek-flash")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--caps", default="")
    ap.add_argument("--protocol", default="openai", choices=["openai", "openai-responses"])
    ap.add_argument("--reasoning-effort", default=None)
    ap.add_argument("--max-tokens", type=int, default=32768)
    ap.add_argument("--container-image", default="", help="run each runner in this ORFS image (podman)")
    ap.add_argument("--container-orfs", default="/r2g_toolchain/OpenROAD-flow-scripts")
    ap.add_argument("--container-ws", type=Path, help="host dir for per-design ORFS workspaces")
    ap.add_argument("--container-env-file", type=Path, help="KEY=VALUE env for the container")
    ap.add_argument("--container-mount", action="append", default=[], help="podman -v spec (repeatable)")
    ap.add_argument("--container-memory", default="", help="podman --memory for each design, e.g. 64g")
    ap.add_argument("--container-name-prefix", default="bare_",
                    help="container name prefix; distinct per arm sharing a host (e.g. bare_gpt_)")
    ap.add_argument("--start-spacing", type=float, default=90.0,
                    help="seconds between design starts (load average catches up)")
    ap.add_argument("--min-mem-gb", type=float, default=0.0,
                    help="start a new design only while MemAvailable is at least this (0 = no gate)")
    ap.add_argument("--min-disk-gb", type=float, default=0.0,
                    help="start a new design only while the root's filesystem has this much free (0 = no gate)")
    ap.add_argument("--max-load", type=float, default=0.0,
                    help="start a new design only while loadavg/cpus is below this (0 = no gate)")
    a = ap.parse_args()
    a.root = a.root.resolve()
    (a.root / "cases").mkdir(parents=True, exist_ok=True)
    cases = json.loads(a.cases.read_text())
    (a.root / "launch.json").write_text(json.dumps({"started": time.time(), "workers": a.workers,
        "model": a.model, "protocol": a.protocol, "reasoning_effort": a.reasoning_effort, "max_tokens": a.max_tokens, "repo": str(a.repo), "base_url": a.base_url, "caps": a.caps,
        "cases": [c["id"] for c in cases]}, indent=1))
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:
        for cid, status in pool.map(lambda c: run_one(c, a), cases):
            print(time.strftime("%F %T"), cid, status, flush=True)


if __name__ == "__main__":
    main()
