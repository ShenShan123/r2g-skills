#!/usr/bin/env python3
"""Resumable sky130hd campaign ledger over the clean-on-nangate45 design corpus.

Race-free by design: the per-design driver writes its own result file under
design_cases/_batch/sky130hd_results/<proj-basename>.json. This tool only READS
those + the frozen candidate list, so any number of parallel drivers are safe.

Subcommands:
  init                 build/refresh the candidate list (dedup by design_name,
                       smallest clean variant) -> sky130hd_candidates.json
  wave --size N [--diverse]
                       print N PENDING source project_paths (one per line).
                       --diverse: round-robin across design_family (first wave).
                       default: smallest-cell-count first.
  status               summarize pass / fail / residual / pending
"""
import argparse
import json
import os
import sqlite3
import sys
from collections import OrderedDict, defaultdict
from pathlib import Path

REPO = Path("/proj/workarea/user5/agent-r2g")
DB = REPO / "r2g-rtl2gds/knowledge/knowledge.sqlite"
BATCH = REPO / "design_cases/_batch"
CANDS = BATCH / "sky130hd_candidates.json"
RESULTS = BATCH / "sky130hd_results"


def build_candidates() -> list[dict]:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    rows = c.execute(
        """SELECT project_path, design_name, design_family, cell_count
           FROM runs
           WHERE platform='nangate45'
             AND drc_status IN ('clean','clean_beol') AND lvs_status='clean'
             AND cell_count IS NOT NULL AND cell_count>0
             AND project_path IS NOT NULL"""
    ).fetchall()
    best: dict[str, dict] = {}
    for r in rows:
        pp = r["project_path"]
        if not pp or not (Path(pp) / "constraints/config.mk").is_file():
            continue
        d = r["design_name"]
        if d not in best or (r["cell_count"] or 1e18) < best[d]["cell_count"]:
            best[d] = {
                "design_name": d,
                "design_family": r["design_family"] or "unknown",
                "project_path": pp,
                "cell_count": int(r["cell_count"]),
            }
    cands = sorted(best.values(), key=lambda x: x["cell_count"])
    return cands


def load_candidates() -> list[dict]:
    if not CANDS.is_file():
        sys.exit("no candidate list; run `sky130_campaign.py init` first")
    return json.loads(CANDS.read_text())


def result_path(proj_path: str) -> Path:
    return RESULTS / (os.path.basename(proj_path.rstrip("/")) + ".json")


def is_done(proj_path: str) -> bool:
    return result_path(proj_path).is_file()


def cmd_init(_):
    BATCH.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    cands = build_candidates()
    CANDS.write_text(json.dumps(cands, indent=2))
    fam = defaultdict(int)
    for c in cands:
        fam[c["design_family"]] += 1
    print(f"candidates: {len(cands)} unique designs across {len(fam)} families")
    print(f"written -> {CANDS}")


def cmd_wave(args):
    cands = load_candidates()
    pending = [c for c in cands if not is_done(c["project_path"])]
    if args.diverse:
        by_fam = OrderedDict()
        for c in pending:
            by_fam.setdefault(c["design_family"], []).append(c)
        for f in by_fam:
            by_fam[f].sort(key=lambda x: x["cell_count"])
        picked, fams = [], list(by_fam.keys())
        while len(picked) < args.size and any(by_fam[f] for f in fams):
            for f in fams:
                if by_fam[f] and len(picked) < args.size:
                    picked.append(by_fam[f].pop(0))
    else:
        picked = pending[: args.size]
    for c in picked:
        print(c["project_path"])


def cmd_status(_):
    cands = load_candidates()
    total = len(cands)
    done = passed = failed = 0
    residuals = defaultdict(int)
    for c in cands:
        rp = result_path(c["project_path"])
        if not rp.is_file():
            continue
        done += 1
        try:
            r = json.loads(rp.read_text())
        except Exception:
            continue
        if r.get("signoff_pass"):
            passed += 1
        else:
            failed += 1
            residuals[r.get("residual_class") or "unknown"] += 1
    print(f"candidates : {total}")
    print(f"done       : {done}  (pending {total - done})")
    print(f"  pass     : {passed}")
    print(f"  fail     : {failed}")
    for k, v in sorted(residuals.items(), key=lambda x: -x[1]):
        print(f"      {k:24s} {v}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=cmd_init)
    pw = sub.add_parser("wave")
    pw.add_argument("--size", type=int, default=12)
    pw.add_argument("--diverse", action="store_true")
    pw.set_defaults(fn=cmd_wave)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
