#!/usr/bin/env python3
"""Run the native-harness arm over the sample: one run_native.py per design, N at a time.

<root>/cases/<id>/ per design; a design with a valid result.json is skipped, so the launcher can
be re-run to resume. Rules carried over from the model-only arm's launcher (experiments/bare_agent):
  * admission gates on load, memory (cgroup-aware headroom) and free disk, one start at a time;
  * an attempt the harness never started (no model call, harness exit) is an infrastructure
    failure: voided (kept under voided/) and run once more;
  * an attempt stopped by the host slice guard (INFRA_INTERRUPTED.json) is moved to interrupted/
    and rerun; MEMORY_BOUND.json is an outcome, not an interruption.
Tool environment per platform as for the model-only arm on 203 (tools203_*.env).
"""
import argparse
import concurrent.futures
import glob
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
START = threading.Lock()


def load_env(path: Path) -> dict:
    out = subprocess.run(["bash", "-c", "set -a; . %s; set +a; env -0" % path], capture_output=True, check=True)
    return dict(kv.split("=", 1) for kv in out.stdout.decode().split("\0") if "=" in kv)


def gate(a):
    sys.path.insert(0, str(a.repo / "experiments/bare_agent"))
    from launch_bare import mem_available_gb, disk_free_gb   # same admission measures as the model-only arm
    with START:
        cpus = os.cpu_count() or 1
        while ((a.max_load and os.getloadavg()[0] > a.max_load * cpus)
               or mem_available_gb() < a.min_mem_gb or disk_free_gb(a.root) < a.min_disk_gb
               or Path(a.root / "PAUSE").exists() or plan_blocked(a)
               or (a.max_running and running_attempts(a) >= a.max_running)):
            time.sleep(60)
        time.sleep(a.start_spacing)


def running_attempts(a) -> int:
    """Attempts of THIS campaign running now, whichever launcher started them (a restarted launcher must not
    stack its workers on top of attempts it inherited)."""
    needle = (str(a.root) + "/cases/").encode()
    n = 0
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            cmd = (proc / "cmdline").read_bytes()
        except OSError:
            continue
        if b"run_native" in cmd and b"--case" in cmd and needle in cmd and b"python" in cmd.split(b"\0")[0]:
            n += 1
    return n


def plan_percent(a):
    """Latest weekly-window used percent the Codex attempts of this campaign recorded (None if none yet)."""
    best = (0.0, None)
    for f in glob.glob(str(a.root / "cases" / "*" / "result.json")):
        try:
            r = json.loads(Path(f).read_text())
        except (OSError, ValueError):
            continue
        pct = r.get("plan_used_percent_end")
        ts = os.path.getmtime(f)
        if pct is not None and ts >= best[0]:
            best = (ts, pct)
    return best[1]


def plan_blocked(a):
    if a.harness != "codex":
        return False
    pct = plan_percent(a)
    return pct is not None and pct >= a.plan_stop_percent


def valid(res: Path) -> bool:
    if not res.is_file():
        return False
    r = json.loads(res.read_text())
    return not (r.get("model_calls", 0) == 0 and str(r.get("stop_reason", "")).startswith("harness_"))


def cleanup_workspace(case, orfs_root: str):
    """As the model-only arm's launcher: reap leftover EDA steps of this design's variant under this
    ORFS root (they run in their own sessions), then drop its ORFS workspace. The project keeps its
    own archived runs, reports and logs."""
    tag = (case["id"] + "_native").encode()
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            env = b"\0" + (proc / "environ").read_bytes() + b"\0"
            if tag in (proc / "cmdline").read_bytes() and (b"\0ORFS_ROOT=" + orfs_root.encode() + b"\0") in env:
                os.kill(int(proc.name), 9)
        except (OSError, ProcessLookupError):
            continue
    flow = Path(orfs_root) / "flow"
    for sub in ("results", "logs", "objects", "reports"):
        for d in (flow / sub / case["platform"]).glob("*/" + case["id"] + "_native"):
            shutil.rmtree(d, ignore_errors=True)


def run_one(case: dict, a) -> tuple[str, str]:
    cid = case["id"]
    out = a.root / "cases" / cid
    for attempt in (1, 2):
        if (out / "INFRA_INTERRUPTED.json").is_file():
            dst = a.root / "interrupted" / ("%s.%d" % (cid, int(time.time())))
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(out), dst)
        if valid(out / "result.json"):
            return cid, json.loads((out / "result.json").read_text())["outcome"]
        if (out / "result.json").is_file() or out.exists():
            dst = a.root / "voided" / ("%s.%d" % (cid, int(time.time())))
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(out), dst)
        gate(a)
        out.mkdir(parents=True)
        (out / "case.json").write_text(json.dumps(case, indent=1))
        env = {**os.environ, **a.envs[case["platform"]]}
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
            env.pop(k, None)
        if a.harness == "codex":
            cmd = [sys.executable, str(HERE / "run_native_codex.py"), "--case", str(out / "case.json"),
                   "--baseline", str(a.inputs / "baselines" / cid), "--out", str(out), "--repo", str(a.repo),
                   "--node-dir", str(a.node_dir), "--codex", str(a.codex), "--codex-home", str(a.codex_home),
                   "--proxy", a.proxy, "--keep-ro", a.keep_ro]
        else:
            cmd = [sys.executable, str(HERE / "run_native.py"), "--case", str(out / "case.json"),
                   "--baseline", str(a.inputs / "baselines" / cid), "--out", str(out), "--repo", str(a.repo),
                   "--node-dir", str(a.node_dir), "--dsh", str(a.dsh), "--keep-ro", a.keep_ro, "--relay", a.relay]
        with open(out / "runner.log", "ab") as log:
            subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=env)
        cleanup_workspace(case, env["ORFS_ROOT"])
        if valid(out / "result.json"):
            return cid, json.loads((out / "result.json").read_text())["outcome"]
    return cid, "infra_failed_twice"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--inputs", type=Path, default=Path("/data/yangao/native_harness/inputs"))
    ap.add_argument("--repo", type=Path, default=Path("/data/yangao/r2g-api-repair-cost-20261028/r2g_vs_bare/runtime_gpt1_ovr"))
    ap.add_argument("--node-dir", type=Path, default=Path("/data/yangao/native_harness/node"))
    ap.add_argument("--dsh", type=Path, default=Path("/data/yangao/native_harness/dsh/node_modules/.bin/dsh"))
    ap.add_argument("--keep-ro", default="/data/yangao/orfs203:/home/yangao/r2g_toolchain")
    ap.add_argument("--relay", default="http://127.0.0.1:18776")
    ap.add_argument("--harness", choices=("dsh", "codex"), default="dsh")
    ap.add_argument("--codex", type=Path, default=Path("/data/yangao/native_harness/codex/node_modules/.bin/codex"))
    ap.add_argument("--codex-home", type=Path, default=Path("/data/yangao/native_harness/codex_home"))
    ap.add_argument("--proxy", default="http://127.0.0.1:12080", help="codex: HTTPS proxy to the ChatGPT backend")
    ap.add_argument("--plan-stop-percent", type=float, default=80.0,
                    help="codex: pause new starts while the plan's weekly window is at or above this used percent")
    ap.add_argument("--interleave", action="store_true", help="order designs round-robin across platforms")
    ap.add_argument("--max-running", type=int, default=0,
                    help="cap on running attempts of this campaign across all launchers (0 = only --workers)")
    ap.add_argument("--env-dir", type=Path, default=Path("/data/yangao/r2g-api-repair-cost-20261028/r2g_vs_bare"))
    ap.add_argument("--platforms", default="nangate45,sky130hd,gf180")
    ap.add_argument("--ids", default="", help="comma-separated subset")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-load", type=float, default=0.85)
    ap.add_argument("--min-mem-gb", type=float, default=60)
    ap.add_argument("--min-disk-gb", type=float, default=100)
    ap.add_argument("--start-spacing", type=float, default=60)
    a = ap.parse_args()
    a.root = a.root.resolve()
    (a.root / "cases").mkdir(parents=True, exist_ok=True)
    a.envs = {"nangate45": load_env(a.env_dir / "tools203_data.env"),
              "sky130hd": load_env(a.env_dir / "tools203_data.env"),
              "gf180": load_env(a.env_dir / "tools203_gf180_data.env")}
    for e in a.envs.values():
        e["R2G_BARE_MEM_MAX"] = "64G"
        e["LD_LIBRARY_PATH"] = "/home/yangao/r2g_toolchain/pylib311"
    plats = a.platforms.split(",")
    ids = set(a.ids.split(",")) if a.ids else None
    cases = [c for c in json.loads((a.inputs / "cases.json").read_text())
             if c["platform"] in plats and (ids is None or c["id"] in ids)]
    if a.interleave:
        by = {p: [c for c in cases if c["platform"] == p] for p in plats}
        order = []
        for i in range(max(len(v) for v in by.values())):
            for p in plats:
                if i < len(by[p]):
                    order.append(by[p][i])
        cases = order
    (a.root / ("launch_%d.json" % int(time.time()))).write_text(json.dumps(
        {"argv": sys.argv, "cases": [c["id"] for c in cases]}, indent=1))
    def safe(c):
        # One failing design must not cancel the rest: pool.map() cancels every pending future when its
        # iterator raises (2026-11-12: 80 designs silently never started).
        try:
            return run_one(c, a)
        except Exception:                                   # noqa: BLE001
            import traceback
            print(time.strftime("%F %T"), c["id"], "LAUNCHER_ERROR\n" + traceback.format_exc(), flush=True)
            return c["id"], "launcher_error"

    with concurrent.futures.ThreadPoolExecutor(a.workers) as pool:
        for cid, outcome in pool.map(safe, cases):
            print(time.strftime("%F %T"), cid, outcome, flush=True)


if __name__ == "__main__":
    main()
