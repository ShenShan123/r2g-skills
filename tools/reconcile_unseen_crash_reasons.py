#!/usr/bin/env python3
"""Reconcile STALE `unseen_crash` escalations to honest synth reasons.

Before engineer_loop.py commit 329c450 (2026-06-28), process_one filed EVERY early synth
abort as reason='unseen_crash'. Designs escalated under the OLD code (the long campaign ran
old code until wave 16) still carry that label, so the escalation queue MISREPRESENTS the
failure landscape -- showing "mysteries" for designs whose synth log names the cause. The
code fix stops NEW misclassifications; this tool corrects the EXISTING rows by re-reading
each design's newest synth log and assigning the honest reason:
    synth_missing_header   -- "Can't open include file" (incomplete upstream RTL)
    synth_memory_residual  -- "exceeds SYNTH_MEMORY_MAX_BITS" (large memory -> fakeram)
    synth_timeout          -- yosys 7200s wrapper timeout (AST pathology)
A genuine downstream crash (place/cts/floorplan rc2, or no parseable synth cause) STAYS
`unseen_crash` -- the honest residue. Never touches runs/failure_events (learning is
unaffected; the learner reads run_violations, not the escalation reason). honesty gates
are re-run after.

Usage:  python3 tools/reconcile_unseen_crash_reasons.py [--db PATH] [--apply]   (dry-run default)
"""
import argparse
import glob
import os
import re
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(REPO, "r2g-skills/signoff-loop", "knowledge", "knowledge.sqlite")


def _classify(design):
    runs = sorted(glob.glob(os.path.join(REPO, "design_cases", design, "backend", "RUN_*")))
    if not runs:
        return None                        # no backend -> cannot reclassify, leave as-is
    try:
        txt = open(os.path.join(runs[-1], "flow.log"), errors="ignore").read()
    except OSError:
        return None
    if "Can't open include file" in txt:
        return "incomplete_missing_header"
    if "exceeds SYNTH_MEMORY_MAX_BITS" in txt:
        return "synth_memory_residual"
    if ("exit code 124" in txt and "synth" in txt) or "do-yosys-canonicalize] Terminated" in txt:
        return "synth_timeout"
    return None                            # genuine downstream crash -> stay unseen_crash


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(args.db, timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    rows = con.execute(
        "SELECT escalation_id, design FROM escalations "
        "WHERE status='open' AND reason='unseen_crash'").fetchall()
    updates = []
    from collections import Counter
    hist = Counter()
    for eid, design in rows:
        new = _classify(design)
        if new:
            updates.append((eid, new))
            hist[new] += 1

    print(f"open unseen_crash: {len(rows)}  |  reclassifiable to honest synth reasons: {len(updates)}")
    for r, n in hist.most_common():
        print(f"  {n:4d}  -> {r}")
    print(f"  {len(rows) - len(updates):4d}  -> stay unseen_crash (genuine downstream / no backend)")

    if args.apply and updates:
        con.executemany(
            "UPDATE escalations SET reason=?, "
            "notes=COALESCE(notes,'')||' [reconciled 2026-06-29: was unseen_crash (329c450)]' "
            "WHERE escalation_id=?",
            [(r, eid) for eid, r in updates])
        con.commit()
        print(f"\nAPPLIED {len(updates)} reason reclassifications.")
    elif updates:
        print("\n(dry-run; re-run with --apply to write)")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
