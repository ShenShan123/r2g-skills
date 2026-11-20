#!/usr/bin/env python3
"""Hidden reference generation and scoring, only in the evaluator container."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import pickle
import subprocess
import sys

sys.path.insert(0, "/private/exp4_validation")
from platforms_v2 import PLATFORMS, files
from scoring import score_design


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", required=True)
    ap.add_argument("--design", required=True)
    ap.add_argument("--truth-only", action="store_true")
    a = ap.parse_args()
    truth = Path("/reference/truth.pkl")
    lock = open("/reference/build.lock", "a+")
    fcntl.flock(lock, fcntl.LOCK_EX)
    if not truth.exists():
        pf = files(a.platform, "/platform")
        env = {**os.environ, "TECH_LEF": str(pf["tech_lef"]),
               "SC_LEFS": " ".join(map(str, pf["lefs"][1:])), "LIBS": " ".join(map(str, pf["libs"])),
               "DEF": "/design/6_final.def", "SDC": "/design/6_final.sdc", "OUT": "/reference/sta.tsv"}
        with open("/reference/sta.log", "w") as log:
            subprocess.run(["openroad", "-no_init", "-no_splash", "-exit", "/private/exp4_validation/sta_pins_dg.tcl"],
                           env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=1800)
        # The old validation study used a route-stage Nangate45 layout; the new
        # study consistently uses the final artifacts for all three technologies.
        for config in PLATFORMS.values():
            config.update(route_def="6_final.def", sdc="6_final.sdc")
        import truth_defgraph
        pending = truth.with_suffix(".building.pkl")
        sys.argv = ["truth_defgraph.py", "/design", a.platform, "/platform", "/reference/sta.tsv", str(pending)]
        truth_defgraph.main()
        pending.replace(truth)
    lock.close()
    if a.truth_only:
        return
    with truth.open("rb") as stream:
        reference = pickle.load(stream)
    reference["design"], reference["platform"] = a.design, a.platform
    run = json.loads(Path("/production/run.json").read_text())
    output = Path("/production/out")
    dataset = output / "dataset" if (output / "dataset").is_dir() else output
    products = [dataset / f"{view}{suffix}" for view in "bcdef"
                for suffix in ("_graph.pt", "_graph_homo.pt")]
    workers = 4 if sum(p.stat().st_size for p in products if p.is_file()) >= 64 * 1024**2 else 1
    result = score_design(reference, output, run, workers=workers)
    Path("/score/score.json").write_text(json.dumps(result, indent=2, allow_nan=False, default=str))
    print(json.dumps({k: result[k] for k in ("design", "strict_full_pass", "label_accuracy", "failures")}))


if __name__ == "__main__":
    main()
