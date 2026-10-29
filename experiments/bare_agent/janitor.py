#!/usr/bin/env python3
"""Reclaim disk from CLOSED attempts of the R2G vs bare comparison, keeping the evidence.

A case is closed when its receipts are final: the bare arm's result.json, or
the R2G arm's summary.json (status complete) plus signoff_evidence.json written
by export_repair_cost_evidence.py. For a closed case, every project directory
loses its layout databases (*.odb, *.spef), ORFS objects/ trees, and the layout
files (*.gds, *.def) of every run except the one the verdict was graded on.
The graded run's 6_final.gds / 6_final.def (what the evidence hashes bind to),
all reports, logs and stage logs are kept. Nothing in an open case is touched.

  python3 janitor.py --root COHORT --arm bare|r2g [--watch SECONDS] [--dry-run]
"""
import argparse
import json
import time
from pathlib import Path


def closed_projects(root: Path, arm: str):
    """Yield (project_dir, graded_run_name) for closed cases."""
    if arm == "bare":
        for res in root.glob("cases/*/result.json"):
            r = json.loads(res.read_text())
            for proj in res.parent.glob("*_bare"):
                yield proj, r.get("run")
        return
    for case in list(root.glob("cases/*")) + list(root.glob("retries/*/cases/*")):
        s = case / "summary.json"
        if not s.is_file() or not (case / "signoff_evidence.json").is_file():
            continue
        if json.loads(s.read_text()).get("status") != "complete":
            continue
        ev = json.loads((case / "signoff_evidence.json").read_text())
        for p in ev.get("projects", []):
            proj = Path(p["project"])
            run = (p.get("reports", {}).get("drc.json") or {}).get("run_tag")
            yield proj, run


def prune(proj: Path, graded: str | None, dry: bool) -> int:
    freed = 0
    if not (proj / "backend").is_dir():
        return 0
    for run in (proj / "backend").glob("RUN_*"):
        keep_layout = run.name == graded
        for f in list(run.rglob("*")):
            if not f.is_file():
                continue
            heavy = f.suffix in (".odb", ".spef") or "objects" in f.relative_to(run).parts
            layout = f.suffix in (".gds", ".def", ".oas")
            if keep_layout and f.name in ("6_final.gds", "6_final.def"):
                continue
            if heavy or layout:
                freed += f.stat().st_size
                if not dry:
                    f.unlink()
    return freed


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--arm", choices=["bare", "r2g"], required=True)
    ap.add_argument("--watch", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    done = set()
    while True:
        freed = n = 0
        for proj, graded in closed_projects(a.root, a.arm):
            if proj in done:
                continue
            freed += prune(proj, graded, a.dry_run)
            n += 1
            if not a.dry_run:
                done.add(proj)
        print(json.dumps({"at": time.strftime("%F %T"), "pruned_projects": n,
                          "freed_gb": round(freed / 1e9, 2), "dry_run": a.dry_run}), flush=True)
        if not a.watch:
            return
        time.sleep(a.watch)


if __name__ == "__main__":
    main()
