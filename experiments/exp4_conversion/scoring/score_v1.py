#!/usr/bin/env python3
"""Exp 4 (conversion): score every arm with the VALIDATION study's def-graph checkers.

Adapter of record = exp4_validation/adapter_defgraph.py (coordinator, 2026-11-07); ground truth =
exp4_validation/{sta_pins_dg.tcl, truth_defgraph.py} (graphs) and v1_images_dg.py (images). One
container per design (conversion image: numpy + OpenROAD), sequential per design:

  1. openroad sta_pins_dg.tcl            -> res/truth/<d>.sta_pins.tsv
  2. truth_defgraph.py                   -> res/truth/<d>.pkl          (cached)
  3. v1_defgraph_check.py per graph arm  -> res/per_design/<d>/graph_<label>.json
  4. v1_images_dg.py per image arm       -> res/per_design/<d>/image_<label>.json

    score_v1.py --v1 V1_SNAPSHOT_DIR --designs LIST --data-root D --platform P --res-root R \
        [--graph LABEL=APPLY_ROOT ...] [--image LABEL=APPLY_ROOT ...]
"""
import argparse
import concurrent.futures as cf
import os
import subprocess
import time
from pathlib import Path


def host_ok(max_load, min_mem):
    avail = next(int(l.split()[1]) for l in open("/proc/meminfo") if l.startswith("MemAvailable")) / 1048576
    return os.getloadavg()[0] < max_load and avail > min_mem


def one(a, d):
    rd = a.res_root / "per_design" / d
    rd.mkdir(parents=True, exist_ok=True)
    (a.res_root / "truth").mkdir(exist_ok=True)
    steps = []
    tp = f"/res/truth/{d}.pkl"
    if not (a.res_root / "truth" / f"{d}.pkl").is_file():
        steps.append(f"DESIGN_DIR=/design PLATFORM_DIR=/platform OUT=/res/truth/{d}.sta_pins.tsv "
                     f"openroad -no_splash -exit /v1/sta_pins_dg.tcl > /res/per_design/{d}/sta.log 2>&1")
        steps.append(f"python3 /v1/truth_defgraph.py /design /platform /res/truth/{d}.sta_pins.tsv {tp}.tmp "
                     f"> /res/per_design/{d}/truth.log 2>&1 && mv {tp}.tmp {tp}")
    mounts = ["-v", f"{a.v1.resolve()}:/v1:ro,Z", "-v", f"{(a.data_root / d).resolve()}:/design:ro,Z",
              "-v", f"{a.platform.resolve()}:/platform:ro,Z", "-v", f"{a.res_root.resolve()}:/res:Z"]
    k = 0
    for kind, arms, script in (("graph", a.graph, "v1_defgraph_check.py"), ("image", a.image, "v1_images_dg.py")):
        for lab, root in arms:
            out = root / d / "out"
            res = rd / f"{kind}_{lab}.json"
            if res.is_file():
                continue
            files = ("b_graph.pt", "c_graph.pt") if kind == "graph" else ("features.npz", "labels.npz")
            if not all((out / f).is_file() for f in files):
                res.write_text('{"error": "no output files", "verdict": {"passed": false}}')
                continue
            mounts += ["-v", f"{out.resolve()}:/arm{k}:ro,Z"]
            args = f"{tp} /arm{k}" if kind == "graph" else f"/design /platform {tp} /arm{k}"
            steps.append(f"python3 /v1/{script} {args} /res/per_design/{d}/{kind}_{lab}.json "
                         f">> /res/per_design/{d}/score.log 2>&1 || echo FAILED {kind} {lab} >> /res/per_design/{d}/score.log")
            k += 1
    if not steps:
        return d, "cached"
    while not host_ok(a.max_load, a.min_mem_gb):
        time.sleep(60)
    truth = [s for s in steps if "/v1/v1_" not in s]
    score = [s for s in steps if "/v1/v1_" in s]
    script = (" && ".join(truth) + (" ; " if score else "") if truth else "") + " ; ".join(score)
    cmd = ["podman", "run", "--rm", "--network", "none", "-e", "OMP_NUM_THREADS=2", *mounts, a.image_name,
           "bash", "-c", script]
    r = subprocess.run(cmd, capture_output=True, text=True)
    (rd / "podman.log").write_text(r.stdout + r.stderr)
    return d, r.returncode


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--v1", type=Path, required=True)
    ap.add_argument("--designs", type=Path, required=True)
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--platform", type=Path, required=True)
    ap.add_argument("--res-root", type=Path, required=True)
    ap.add_argument("--graph", action="append", default=[])
    ap.add_argument("--image", action="append", default=[])
    ap.add_argument("--image-name", default="localhost/exp4c-conv:1")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--max-load", type=float, default=110)
    ap.add_argument("--min-mem-gb", type=float, default=200)
    a = ap.parse_args()
    a.graph = [(s.split("=", 1)[0], Path(s.split("=", 1)[1])) for s in a.graph]
    a.image = [(s.split("=", 1)[0], Path(s.split("=", 1)[1])) for s in a.image]
    designs = [l.strip() for l in a.designs.read_text().splitlines() if l.strip()]
    a.res_root.mkdir(parents=True, exist_ok=True)
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for d, rc in ex.map(lambda d: one(a, d), designs):
            print(d, rc, flush=True)


if __name__ == "__main__":
    main()
