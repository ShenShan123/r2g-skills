#!/usr/bin/env python3
"""Exp 4 v2: score every arm with the validation study's v2 shared checker (one container per design).

Per design (truth cached under R/truth/):
  1. sta_pins_dg.tcl (OpenSTA, no parasitics)          -> truth/<d>.sta.tsv
  2. truth_defgraph.py                                  -> truth/<d>.pkl
  3. nangate45 only: gt_raw.py (four-stage truth)       -> truth/<d>.gt.pkl
  4. per arm: v2_defgraph_check.py (b-f, both forms)    -> per_design/<d>/graphs_<arm>.json
              v1_images_dg.py                           -> per_design/<d>/images_<arm>.json
              v1_4stage_check.py (nangate45)            -> per_design/<d>/four_stage_<arm>.json
Truth design dir = the raw run dir (pilot500 copy with logs/ for Nangate45; the rerun run dir for
SKY130HD/GF180); place DEF = the staged 3_place.def every arm received.

    score_v2.py --v1 SNAP --designs LIST --stage-root data_v2/test --res-root R --arm LABEL=APPLY_ROOT ...
"""
import argparse
import concurrent.futures as cf
import json
import os
import subprocess
import time
from pathlib import Path

W = Path("/proj/workarea/yangao/exp4_conversion_20261107")
RAW = {"nangate45": W / "data/designs", "sky130hd": Path("/proj/workarea/yangao/rerun_sky130hd_20261022/runs"),
       "gf180": Path("/proj/workarea/yangao/rerun_gf180_20261026/runs")}
FLAT = W / "v2/platform_flat"
ORFS_N45 = "/r2g_toolchain/OpenROAD-flow-scripts/flow/platforms/nangate45"


def host_ok(a):
    avail = next(int(l.split()[1]) for l in open("/proc/meminfo") if l.startswith("MemAvailable")) / 1048576
    r = subprocess.run(["podman", "ps", "--format", "{{.Names}}"], capture_output=True, text=True)
    n = sum(1 for x in r.stdout.split() if x.startswith("exp4c_"))
    return os.getloadavg()[0] < a.max_load and avail > a.min_mem_gb and n < a.max_containers


def one(a, d):
    st = json.loads((a.stage_root / d / "STAGED.json").read_text())
    pl = st["platform"]
    rd = a.res_root / "per_design" / d
    rd.mkdir(parents=True, exist_ok=True)
    tr = a.res_root / "truth"
    tr.mkdir(exist_ok=True)
    steps = []
    if not (tr / f"{d}.pkl").is_file():
        steps.append(f"read -r TL SL LB RD SDC <<<\"$(python3 -c \"import sys; sys.path.insert(0,'/v1'); from platforms_v2 import PLATFORMS as P; "
                     f"c=P['{pl}']; print(c['tech_lef'], ' '.join('/plat/'+x for x in c['sc_lef']), ' '.join('/plat/'+x for x in c['libs']), c['route_def'], c['sdc'])\")\"; "
                     f"TECH_LEF=/plat/$TL SC_LEFS=\"$SL\" LIBS=\"$LB\" DEF=/tdesign/$RD SDC=/tdesign/$SDC OUT=/res/truth/{d}.sta.tsv "
                     f"openroad -no_init -no_splash -exit /v1/sta_pins_dg.tcl > /res/per_design/{d}/sta.log 2>&1")
        steps.append(f"python3 /v1/truth_defgraph.py /tdesign {pl} /plat /res/truth/{d}.sta.tsv /res/truth/{d}.pkl.tmp "
                     f"> /res/per_design/{d}/truth.log 2>&1 && mv /res/truth/{d}.pkl.tmp /res/truth/{d}.pkl")
    if pl == "nangate45" and not (tr / f"{d}.gt.pkl").is_file():
        steps.append(f"python3 /v1/gt_raw.py /tdesign {ORFS_N45} /res/truth/{d}.gt.pkl.tmp > /res/per_design/{d}/gt.log 2>&1 "
                     f"&& mv /res/truth/{d}.gt.pkl.tmp /res/truth/{d}.gt.pkl")
    mounts = ["-v", f"{a.v1.resolve()}:/v1:ro,Z", "-v", f"{(RAW[pl] / d).resolve()}:/tdesign:ro,Z",
              "-v", f"{(a.stage_root / d).resolve()}:/sdesign:ro,Z", "-v", f"{(FLAT / pl).resolve()}:/plat:ro,Z",
              "-v", f"{a.res_root.resolve()}:/res:Z"]
    k = 0
    for lab, root in a.arms:
        out = root / d / "out"
        if not out.is_dir():
            continue
        jobs = [("graphs", out / "dataset", f"python3 /v1/v2_defgraph_check.py /res/truth/{d}.pkl /arm{k}/dataset /res/per_design/{d}/graphs_{lab}.json"),
                ("images", out / "images", f"python3 /v1/v1_images_dg.py /tdesign /plat /res/truth/{d}.pkl /arm{k}/images /res/per_design/{d}/images_{lab}.json {pl} /sdesign/3_place.def")]
        if pl == "nangate45":
            jobs.append(("four_stage", out / "four_stage/generated",
                         f"python3 /v1/v1_4stage_check.py /tdesign /res/truth/{d}.gt.pkl /arm{k}/four_stage/generated /res/per_design/{d}/four_stage_{lab}.json"))
        added = False
        for task, path, cmd in jobs:
            res = rd / f"{task}_{lab}.json"
            if res.is_file():
                continue
            if not path.exists():
                res.write_text(json.dumps({"error": "no output", "label": lab}))
                continue
            steps.append(f"{cmd} >> /res/per_design/{d}/score.log 2>&1 || echo FAILED {task} {lab} >> /res/per_design/{d}/score.log")
            added = True
        if added:
            mounts += ["-v", f"{out.resolve()}:/arm{k}:ro,Z"]
            k += 1
    if not steps:
        return d, "cached"
    while not host_ok(a):
        time.sleep(60)
    truth = [s for s in steps if "/v1/v2_" not in s and "/v1/v1_" not in s]
    score = [s for s in steps if s not in truth]
    script = (" && ".join(truth) + " ; " if truth else "") + " ; ".join(score)
    r = subprocess.run(["podman", "run", "--rm", "--name", f"exp4c_score_{abs(hash(d)) % 10**8}", "--network", "none",
                        "-e", "OMP_NUM_THREADS=2", *mounts, a.image, "bash", "-c", script], capture_output=True, text=True)
    (rd / "podman.log").write_text(r.stdout + r.stderr)
    return d, r.returncode


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--v1", type=Path, required=True)
    ap.add_argument("--designs", type=Path, required=True)
    ap.add_argument("--stage-root", type=Path, required=True)
    ap.add_argument("--res-root", type=Path, required=True)
    ap.add_argument("--arm", action="append", default=[])
    ap.add_argument("--image", default="localhost/exp4c-conv:1")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--max-load", type=float, default=110)
    ap.add_argument("--min-mem-gb", type=float, default=200)
    ap.add_argument("--max-containers", type=int, default=6)
    a = ap.parse_args()
    a.arms = [(s.split("=", 1)[0], Path(s.split("=", 1)[1])) for s in a.arm]
    designs = [l.strip() for l in a.designs.read_text().splitlines() if l.strip()]
    a.res_root.mkdir(parents=True, exist_ok=True)
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for d, rc in ex.map(lambda d: one(a, d), designs):
            print(d, rc, flush=True)


if __name__ == "__main__":
    main()
