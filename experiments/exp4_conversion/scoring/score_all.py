#!/usr/bin/env python3
"""Exp 4 (conversion): score every arm on every design with the same checker, one container
per design (the conversion image: numpy + OpenROAD for the timing truth).

    score_all.py --designs LIST --arm LABEL=APPLY_ROOT [--arm ...] --res-root R

APPLY_ROOT/<design>/out holds that arm's b_graph.pt / c_graph.pt (apply_arm.py layout). An arm
whose output files are missing for a design is recorded as not scorable for it. Truth is
cached per design under R/truth/<design>.pkl.
"""
import argparse
import concurrent.futures as cf
import json
import os
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
V1 = HERE.parent.parent / "exp4_validation"
TECH = "/r2g_toolchain/OpenROAD-flow-scripts/flow/platforms/nangate45"


def host_ok(max_load, min_mem):
    avail = next(int(l.split()[1]) for l in open("/proc/meminfo") if l.startswith("MemAvailable")) / 1048576
    return os.getloadavg()[0] < max_load and avail > min_mem


def one(a, d):
    rd = a.res_root / "per_design" / d
    want = {lab: root / d / "out" for lab, root in a.arms}
    files = ("b_graph.pt", "c_graph.pt") if a.task == "graph" else ("features.npz", "labels.npz")
    pre = "score_" if a.task == "graph" else "imgscore_"
    have = {lab: p for lab, p in want.items() if all((p / f).is_file() for f in files)}
    todo = {lab: p for lab, p in have.items() if not (rd / f"{pre}{lab}.json").is_file()}
    rd.mkdir(parents=True, exist_ok=True)
    for lab in set(want) - set(have):
        (rd / f"{pre}{lab}.json").write_text(json.dumps({"design": d, "label": lab, "error": "no output files",
                                                          "all_groups_pass": False}))
    if not todo:
        return d, "cached"
    while not host_ok(a.max_load, a.min_mem_gb):
        time.sleep(60)
    mounts = ["-v", f"{HERE}:/scoring:ro,Z", "-v", f"{V1}:/v1:ro,Z",
              "-v", f"{(a.data_root / d).resolve()}:/design:ro,Z", "-v", f"{a.res_root.resolve()}:/res:Z"]
    arms = []
    for i, (lab, p) in enumerate(sorted(todo.items())):
        mounts += ["-v", f"{p.resolve()}:/arm{i}:ro,Z"]
        arms.append(f"{lab}=/arm{i}")
    if a.task == "graph":
        inner = ["python3", "/scoring/defgraph_check.py", "design", "/design", TECH, f"/res/truth/{d}.pkl", f"/res/per_design/{d}", *arms]
    else:
        al = " ".join(arms)
        inner = ["bash", "-c", f"python3 /scoring/image_check.py design /design {TECH} /res/truth/{d}.img.npz /res/per_design/{d} {al} ; "
                 f"IMG_BIN=dbu python3 /scoring/image_check.py design /design {TECH} /res/truth/{d}.imgdbu.npz /res/per_design/{d}/dbu {al}"]
    cmd = ["podman", "run", "--rm", "--network", "none", "-e", "EXP4_V1_DIR=/v1",
           "-e", "OMP_NUM_THREADS=2", *mounts, a.image, *inner]
    (a.res_root / "truth").mkdir(exist_ok=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    (rd / "score.log").write_text(r.stdout + r.stderr)
    return d, r.returncode


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--designs", type=Path, required=True)
    ap.add_argument("--arm", action="append", required=True)
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--res-root", type=Path, required=True)
    ap.add_argument("--image", default="localhost/exp4c-conv:1")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--task", choices=["graph", "image"], default="graph")
    ap.add_argument("--max-load", type=float, default=110)
    ap.add_argument("--min-mem-gb", type=float, default=200)
    a = ap.parse_args()
    a.arms = [(s.split("=", 1)[0], Path(s.split("=", 1)[1])) for s in a.arm]
    designs = [l.strip() for l in a.designs.read_text().splitlines() if l.strip()]
    a.res_root.mkdir(parents=True, exist_ok=True)
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for d, rc in ex.map(lambda d: one(a, d), designs):
            print(d, rc, flush=True)


if __name__ == "__main__":
    main()
