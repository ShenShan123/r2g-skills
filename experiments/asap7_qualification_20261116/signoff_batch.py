#!/usr/bin/env python3
"""Batch ASAP7 signoff on memlab216 for harvested runs from 216 (container) and 203 (native, pulled to an inbox).

  signoff_batch.py --runs DIR [--runs DIR ...] --out SIGNOFF_ROOT [--jobs N] [--drc-budget S] [--watch]

Every run directory under each --runs DIR that has a 6_final.def and 6_final.odb is signed off once into
SIGNOFF_ROOT/<run name>/signoff.json by asap7_signoff.py. A run without a 6_final.gds (a 203 run, whose GDS is
never transferred) first gets one from regen_gds.sh, the baseline image's own `do-gds`, so every signoff input GDS
comes from the same writer (KLayout 0.30.7). --watch keeps polling for new runs until SIGNOFF_ROOT/STOP exists.
Writes SIGNOFF_ROOT/batch.log (one line per design) and never re-signs a design that already has signoff.json.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import subprocess
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def ready(run: Path) -> bool:
    res = run / "result.json"
    return res.is_file() and (run / "6_final.def").is_file() and (run / "6_final.odb").is_file() \
        and (run / "config.mk").is_file()


def cells(run: Path) -> int:
    try:
        return int(json.loads((run / "result.json").read_text()).get("cells") or 0)
    except (OSError, ValueError):
        return 0


def one(run: Path, out_root: Path, budget: int) -> str:
    out = out_root / run.name
    t0 = time.monotonic()
    regen = False
    if not (run / "6_final.gds").is_file():
        regen = True
        tmp = run / f"6_final.gds.tmp.{os.getpid()}.{threading.get_ident()}"   # unique: lanes may race here
        r = subprocess.run(["bash", str(HERE / "regen_gds.sh"), str(run), str(tmp)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            out.mkdir(parents=True, exist_ok=True)
            (out / "signoff.json").write_text(json.dumps(
                {"design_dir": str(run), "drc": "failed", "lvs": "failed", "error": "regen_gds: " + r.stderr[-2000:]},
                indent=1))
            return f"{run.name} regen_failed"
        os.replace(tmp, run / "6_final.gds")
    subprocess.run(["python3", str(HERE / "asap7_signoff.py"), str(run), str(out), "--drc-budget", str(budget)],
                   capture_output=True, text=True)
    try:
        r = json.loads((out / "signoff.json").read_text())
    except (OSError, ValueError):
        return f"{run.name} no_signoff_json"
    r["gds_regenerated_on_216"] = regen
    (out / "signoff.json").write_text(json.dumps(r, indent=1))
    return "%s drc=%s results=%s lvs=%s wall=%ss total=%ss" % (
        run.name, r.get("drc"), r.get("drc_results"), r.get("lvs"), r.get("wall_s"), round(time.monotonic() - t0))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", action="append", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--drc-budget", type=int, default=21600)
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--max-cells", type=int, default=0,
                    help="only designs with at most this many cells (a small-design lane; 0 = all)")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    log = open(a.out / "batch.log", "a", buffering=1)
    queued: set[str] = set()
    with cf.ThreadPoolExecutor(a.jobs) as pool:
        futs = {}
        while True:
            found = [p for root in a.runs for p in root.glob("*") if p.is_dir() and ready(p)]
            for run in sorted(found, key=lambda p: (cells(p), p.name)):          # smallest first
                if True:
                    if run.name in queued or (a.out / run.name / "signoff.json").exists():
                        continue
                    if a.max_cells and cells(run) > a.max_cells:
                        continue
                    queued.add(run.name)
                    futs[pool.submit(one, run, a.out, a.drc_budget)] = run.name
            for f in [f for f in futs if f.done()]:
                name = futs.pop(f)
                try:
                    line = f.result()
                except Exception as exc:                       # noqa: BLE001
                    line = f"{name} error {exc!r}"
                log.write(time.strftime("%H:%M:%S ") + line + "\n")
            if not a.watch and not futs:
                break
            if a.watch and (a.out / "STOP").exists() and not futs:
                break
            time.sleep(30)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
