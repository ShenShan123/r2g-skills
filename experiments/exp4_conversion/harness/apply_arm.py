#!/usr/bin/env python3
"""Exp 4 (conversion): apply one arm to a list of designs, one fresh container per design.

    apply_arm.py --arm llm --converter FROZEN_DIR --designs LIST --data-root D --out-root O
    apply_arm.py --arm r2g --skill DEF_GRAPH_DIR --designs LIST --data-root D --out-root O

Every design runs in a fresh `--network none` container of the conversion image with the
design's three input files (5_route.def, 6_final.spef, constraint.sdc; read-only), the
platform files (read-only) and an empty writable output dir. The LLM arm's frozen converter
is mounted read-only; no model is called. The R2G arm mounts the def-graph skill tree
(read-only) and runs r2g_arm/run_r2g_design.sh (shim + run_graphs.sh; it also needs the run's
config.mk for DESIGN_NAME, mounted from the design's full directory). Each run is wrapped by
measure.py (3,600 s wall limit, exit code, wall/CPU seconds, peak RSS), then the public
check_format.py is run on the output. Conversion success = exit 0, not timed out, both files
present and format check ok.

Writes <out-root>/<design>/{out/, measure.json, format.json, run.log, apply.json} and
<out-root>/apply_summary.json. Respects host load: a new design starts only while the
1-min load is below --max-load and available memory above --min-mem-gb.
"""
import argparse
import concurrent.futures as cf
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent


def podman(*a, **kw):
    return subprocess.run(["podman", *a], capture_output=True, text=True, errors="replace", **kw)


MAX_CONTAINERS = None   # set from --max-containers: cap on ALL running exp4c_* containers (dev + apply)


def n_exp4c():
    r = podman("ps", "--format", "{{.Names}}")
    return sum(1 for n in r.stdout.split() if n.startswith("exp4c_"))


def host_ok(max_load, min_mem_gb):
    if MAX_CONTAINERS is not None and n_exp4c() >= MAX_CONTAINERS:
        return False, os.getloadavg()[0], -1
    load = os.getloadavg()[0]
    avail = 0
    for line in open("/proc/meminfo"):
        if line.startswith("MemAvailable:"):
            avail = int(line.split()[1]) / 1024 / 1024
    return load < max_load and avail > min_mem_gb, load, avail


def run_one(a, design):
    od = a.out_root / design
    if (od / "apply.json").is_file():
        return json.loads((od / "apply.json").read_text())
    while True:          # etiquette: start only while the shared host has headroom
        ok, load, avail = host_ok(a.max_load, a.min_mem_gb)
        if ok:
            break
        print(f"{design}: waiting, load {load:.0f} avail {avail:.0f} GB", flush=True)
        time.sleep(60 + hash(design) % 30)
    od.mkdir(parents=True, exist_ok=True)
    out = od / "out"
    out.mkdir(exist_ok=True)
    dd = (a.data_root / design).resolve()
    name = "exp4c_%s_%s" % (a.arm, uuid.uuid4().hex[:8])
    plat_dir = "/data/platform"
    if a.task == "full":      # v2: the whole staged design dir; the ORFS platform dir of its PLATFORM
        pl = next(l.split("=", 1)[1].strip() for l in (dd / "config.mk").read_text().splitlines()
                  if l.strip().startswith("export PLATFORM") and "=" in l)
        plat_dir = "/r2g_toolchain/OpenROAD-flow-scripts/flow/platforms/" + pl
        mounts = ["-v", f"{dd}:/data/design:ro,Z"]
    else:
        inputs = ("5_route.def", "6_final.spef", "constraint.sdc") if a.task == "graph" else ("3_place.def", "5_route.def")
        mounts = [x for f in inputs for x in ("-v", f"{dd}/{f}:/data/design/{f}:ro,Z")]
    mounts += [
              "-v", f"{a.platform.resolve()}:/data/platform:ro,Z",
              "-v", f"{out}:/out:Z", "-v", f"{HERE}:/harness:ro,Z", "-v", f"{a.spec.resolve()}:/spec:ro,Z"]
    outputs = {"graph": ("b_graph.pt", "c_graph.pt"), "image": ("features.npz", "labels.npz"),
               "full": ("dataset", "images")}[a.task]
    # 216 is rootless cgroups v1: podman ignores --memory/--cpus there, so thread pools are capped
    # by environment instead (same for every arm)
    env = ["-e", "ORFS_ROOT=/r2g_toolchain/OpenROAD-flow-scripts", "-e", f"OMP_NUM_THREADS={a.cpus}",
           "-e", f"MKL_NUM_THREADS={a.cpus}", "-e", f"OPENBLAS_NUM_THREADS={a.cpus}", "-e", f"NUM_CORES={a.cpus}"]
    if a.arm == "llm":
        mounts += ["-v", f"{a.converter.resolve()}:/work/converter:ro,Z"]
        cmd = ["bash", "/work/converter/run.sh", "/data/design", plat_dir, "/out"]
    else:
        proj = od / "proj"
        proj.mkdir(exist_ok=True)
        mounts += ["-v", f"{a.skill.resolve()}:/r2g/def-graph:ro,Z", "-v", f"{a.r2g_arm.resolve()}:/r2g_arm:ro,Z",
                   "-v", f"{proj}:/proj:Z"]
        if a.task != "full":
            mounts += ["-v", f"{dd}/config.mk:/data/design/config.mk:ro,Z"]
        env += ["-e", "R2G_GRAPH_PYTHON=/opt/py/bin/python3"]
        script = {"graph": "run_r2g_design.sh", "image": "run_r2g_image.sh", "full": "run_r2g_v2.sh"}[a.task]
        cmd = ["bash", "/r2g_arm/" + script, "/r2g/def-graph", "/data/design", plat_dir, "/proj/" + design, "/out"]
    t0 = time.time()
    with open(od / "run.log", "w") as log:
        r = subprocess.run(["podman", "run", "--rm", "--name", name, "--network", "none", "--memory", a.memory,
                            "--cpus", a.cpus, *mounts, *env, "-w", "/tmp", a.image,
                            "python3", "/harness/measure.py", str(a.timeout), "/out/.measure.json", *cmd],
                           stdout=log, stderr=subprocess.STDOUT, timeout=a.timeout + 600)
    host_wall = time.time() - t0
    meas = json.loads((out / ".measure.json").read_text()) if (out / ".measure.json").is_file() else {"exit_code": None}
    if (out / ".measure.json").is_file():
        (out / ".measure.json").replace(od / "measure.json")
    files_ok = all((out / f).exists() for f in outputs)
    fmt = {"ok": False, "skipped": "outputs missing"}
    if a.task == "full":
        f = podman("run", "--rm", "--network", "none", "-v", f"{out}:/out:ro,Z", "-v", f"{dd}:/data/design:ro,Z",
                   "-v", f"{a.spec.resolve()}:/spec:ro,Z", a.image, "python3", "/spec/check_format_v2.py", "/out", "/data/design")
        try:
            fmt = json.loads(f.stdout)
        except ValueError:
            fmt = {"ok": False, "error": (f.stdout + f.stderr)[-2000:]}
        files_ok = True
    elif files_ok:
        chk = ["python3", "/spec/check_format.py", "/out"] if a.task == "graph" else \
              ["python3", "/spec/check_format_image.py", "/out", "/data/design"]
        f = podman("run", "--rm", "--network", "none", "-v", f"{out}:/out:ro,Z",
                   "-v", f"{dd}/5_route.def:/data/design/5_route.def:ro,Z",
                   "-v", f"{a.spec.resolve()}:/spec:ro,Z", a.image, *chk)
        try:
            fmt = json.loads(f.stdout)
        except ValueError:
            fmt = {"ok": False, "error": (f.stdout + f.stderr)[-2000:]}
    (od / "format.json").write_text(json.dumps(fmt, indent=1))
    success = bool(meas.get("exit_code") == 0 and not meas.get("timed_out") and files_ok and fmt.get("ok"))
    task_success = None
    if a.task == "full":      # per task: the run finished in time and that task's outputs pass the format check
        ran = meas.get("exit_code") is not None and not meas.get("timed_out")
        task_success = {k: bool(ran and v.get("ok")) for k, v in (fmt.get("tasks") or {}).items()}
    res = {"design": design, "task": a.task, "arm": a.arm, "label": a.label, "success": success, "exit_code": meas.get("exit_code"),
           "timed_out": meas.get("timed_out"), "files_ok": files_ok, "format_ok": bool(fmt.get("ok")),
           "format_failed": ([c["check"] for c in fmt.get("failed", [])][:10] if a.task != "full" else
                             {k: [c["check"] for c in v.get("failed", [])][:10] for k, v in (fmt.get("tasks") or {}).items()}),
           "task_success": task_success,
           "wall_seconds": meas.get("wall_seconds"), "user_seconds": meas.get("user_seconds"),
           "sys_seconds": meas.get("sys_seconds"), "peak_rss_mb": meas.get("peak_rss_mb"),
           "host_wall_seconds": round(host_wall, 1), "podman_rc": r.returncode}
    if a.arm == "r2g" and (out / "r2g_timing.json").is_file():
        res["r2g_timing"] = json.loads((out / "r2g_timing.json").read_text())
    (od / "apply.json").write_text(json.dumps(res, indent=1))
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arm", choices=["llm", "r2g"], required=True)
    ap.add_argument("--task", choices=["graph", "image", "full"], default="graph")
    ap.add_argument("--label", required=True, help="arm label recorded in results (e.g. deepseek, gpt, r2g)")
    ap.add_argument("--converter", type=Path)
    ap.add_argument("--skill", type=Path)
    ap.add_argument("--r2g-arm", type=Path, default=HERE.parent / "r2g_arm")
    ap.add_argument("--designs", type=Path, required=True, help="text file, one design per line")
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--platform", type=Path, required=True)
    ap.add_argument("--spec", type=Path, required=True)
    ap.add_argument("--out-root", type=Path, required=True)
    ap.add_argument("--image", default="localhost/exp4c-conv:1")
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--memory", default="64g")
    ap.add_argument("--cpus", default="4")
    ap.add_argument("--max-load", type=float, default=85.0)
    ap.add_argument("--min-mem-gb", type=float, default=200.0)
    ap.add_argument("--max-containers", type=int, default=None, help="global cap on running exp4c_* containers")
    a = ap.parse_args()
    global MAX_CONTAINERS
    MAX_CONTAINERS = a.max_containers
    a.out_root.mkdir(parents=True, exist_ok=True)
    designs = [l.strip() for l in a.designs.read_text().splitlines() if l.strip()]
    results = []
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs = []
        for d in designs:
            futs.append(ex.submit(run_one, a, d))
        for f in cf.as_completed(futs):
            r = f.result()
            results.append(r)
            print(json.dumps({k: r.get(k) for k in ("design", "success", "wall_seconds", "peak_rss_mb")}), flush=True)
    summ = {"arm": a.arm, "label": a.label, "n": len(results), "success": sum(r["success"] for r in results),
            "results": sorted(results, key=lambda r: r["design"])}
    (a.out_root / "apply_summary.json").write_text(json.dumps(summ, indent=1))
    print(json.dumps({k: summ[k] for k in ("arm", "label", "n", "success")}))


if __name__ == "__main__":
    main()
