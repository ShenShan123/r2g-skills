#!/usr/bin/env python3
"""Reconcile the sky130 campaign after the 2026-06-17 LVS wrong-tool fix.

For each sky130 design that has a built GDS, regenerate the signoff reports
(ppa/drc/lvs) with the corrected extractors, re-ingest so the knowledge store
records the TRUE outcome (Netgen LVS, completed route), then auto-drain any open
escalation whose design is now fully clean (drc clean/clean_beol/skipped + lvs
clean/skipped + orfs complete). Stale aborts a later run already cleared stop
masquerading as "still stuck".

This is a ONE-TIME operator reconcile for escalations opened BEFORE the loop
gained its own auto-resolution (engineer_loop._mark_clean). The durable fix is in
the loop; this only cleans up the pre-fix backlog.

Usage:
  python3 tools/reconcile_sky130_campaign.py [--apply] [design ...]
Without --apply it is a dry run (prints what it would do, touches nothing).
Without explicit designs it reconciles every design named by an OPEN escalation.
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "r2g-skills/signoff-loop"
EXTRACT = SKILL / "scripts" / "extract"
KNOW = SKILL / "knowledge"
sys.path.insert(0, str(KNOW))

CLEAN_DRC = {"clean", "clean_beol", "skipped"}
CLEAN_LVS = {"clean", "skipped"}


def _proj(design: str) -> Path | None:
    p = ROOT / "design_cases" / design
    return p if p.is_dir() else None


def _has_gds(proj: Path) -> bool:
    return bool(glob.glob(str(proj / "backend" / "RUN_*" / "final" / "*.gds")))


def _status(proj: Path, check: str) -> str:
    try:
        return json.loads((proj / "reports" / f"{check}.json").read_text()).get("status", "unknown")
    except Exception:
        return "unknown"


def _run(cmd: list[str]) -> int:
    return subprocess.run(cmd, capture_output=True, text=True).returncode


def reconcile_one(design: str, apply: bool) -> dict:
    proj = _proj(design)
    if proj is None:
        return {"design": design, "action": "skip", "why": "no project dir"}
    if not _has_gds(proj):
        return {"design": design, "action": "skip", "why": "no GDS (flow incomplete)"}
    if apply:
        _run([sys.executable, str(EXTRACT / "extract_ppa.py"), str(proj), str(proj / "reports" / "ppa.json")])
        _run([sys.executable, str(EXTRACT / "extract_drc.py"), str(proj), str(proj / "reports" / "drc.json")])
        _run([sys.executable, str(EXTRACT / "extract_lvs.py"), str(proj), str(proj / "reports" / "lvs.json")])
        _run([sys.executable, str(KNOW / "ingest_run.py"), str(proj)])
    drc, lvs = _status(proj, "drc"), _status(proj, "lvs")
    try:
        ppa = json.loads((proj / "reports" / "ppa.json").read_text())
        orfs = ppa.get("orfs_status", "unknown")
    except Exception:
        orfs = "unknown"
    fully_clean = (drc in CLEAN_DRC and lvs in CLEAN_LVS
                   and orfs in ("complete", "pass"))
    return {"design": design, "action": "reconciled" if apply else "dry-run",
            "drc": drc, "lvs": lvs, "orfs": orfs, "fully_clean": fully_clean}


def main() -> int:
    args = sys.argv[1:]
    apply = "--apply" in args
    designs = [a for a in args if not a.startswith("--")]

    import knowledge_db
    import escalations
    conn = knowledge_db.connect(knowledge_db.DEFAULT_DB_PATH)

    if not designs:
        designs = sorted({r["design"] for r in escalations.list_open(conn)})

    results = []
    for d in designs:
        r = reconcile_one(d, apply)
        results.append(r)
        print(json.dumps(r))

    # Auto-drain escalations for fully-clean designs.
    drained = 0
    for r in results:
        if r.get("fully_clean"):
            if apply:
                n = escalations.resolve_for_design(
                    conn, r["design"],
                    notes="reconcile_sky130_campaign: clean (netgen LVS) — supersedes stale abort")
                drained += n
                if n:
                    print(f"DRAINED {n} escalation(s) for {r['design']}")
            else:
                print(f"WOULD DRAIN escalations for {r['design']} (fully clean)")

    n_clean = sum(1 for r in results if r.get("fully_clean"))
    print(f"\n== {len(results)} designs, {n_clean} fully clean, "
          f"{drained} escalations drained ({'APPLIED' if apply else 'DRY-RUN'}) ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
