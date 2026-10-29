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


def run_one(case, a):
    out = a.root / "cases" / case["id"]
    if (out / "result.json").exists():
        return case["id"], "skipped"
    out.mkdir(parents=True, exist_ok=True)
    (out / "case.json").write_text(json.dumps(case, indent=1, ensure_ascii=False))
    baseline = a.baselines / case["id"]
    cmd = [sys.executable, str(HERE / "run_bare.py"), "--case", str(out / "case.json"),
           "--baseline", str(baseline), "--out", str(out), "--repo", str(a.repo),
           "--base-url", a.base_url, "--model", a.model]
    if a.caps:
        cmd += ["--caps", a.caps]
    t0 = time.time()
    with (out / "runner.log").open("ab") as log:
        rc = subprocess.call(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=a.repo)
    variant = case["id"] + "_bare"
    flow = Path(os.environ["ORFS_ROOT"]) / "flow"
    top = json.loads((out / "case.json").read_text()).get("top")
    for sub in ("results", "logs", "objects", "reports"):
        for d in (flow / sub / case["platform"]).glob("*/" + variant):
            shutil.rmtree(d, ignore_errors=True)
    return case["id"], "rc=%s %.0fs" % (rc, time.time() - t0)


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
    a = ap.parse_args()
    a.root = a.root.resolve()
    (a.root / "cases").mkdir(parents=True, exist_ok=True)
    cases = json.loads(a.cases.read_text())
    (a.root / "launch.json").write_text(json.dumps({"started": time.time(), "workers": a.workers,
        "model": a.model, "repo": str(a.repo), "base_url": a.base_url, "caps": a.caps,
        "cases": [c["id"] for c in cases]}, indent=1))
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:
        for cid, status in pool.map(lambda c: run_one(c, a), cases):
            print(time.strftime("%F %T"), cid, status, flush=True)


if __name__ == "__main__":
    main()
