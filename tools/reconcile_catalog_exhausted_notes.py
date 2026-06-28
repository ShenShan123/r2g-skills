#!/usr/bin/env python3
"""Reconcile stale catalog_exhausted escalation notes to the real POST-fix residual.

Before engineer_loop.py commit cbcad40, a catalog_exhausted escalation recorded the
PRE-fix `_signoff_status` snapshot in its `notes` -- usually {drc:unknown,lvs:unknown}
on a first signoff pass (no report existed yet). That made every such escalation look
identical in the queue while the genuine residuals were diverse (drc=stuck / lvs=fail /
both). The code fix makes FUTURE escalations honest; this tool corrects the EXISTING
rows in place by re-reading each design's current reports/{drc,lvs}.json.

Idempotent + additive: only rewrites notes that are still the bare {drc:unknown,lvs:unknown}
(or an unparseable/empty) snapshot; never touches failure_events/runs (honesty gates unaffected).

Usage:  python3 tools/reconcile_catalog_exhausted_notes.py [--db PATH] [--apply]
        (dry-run by default; --apply writes the updates)
"""
import argparse
import json
import os
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(REPO, "r2g-rtl2gds", "knowledge", "knowledge.sqlite")
STALE = '{"drc": "unknown", "lvs": "unknown"}'


def _report_status(design, check):
    p = os.path.join(REPO, "design_cases", design, "reports", f"{check}.json")
    try:
        return json.load(open(p)).get("status", "absent")
    except FileNotFoundError:
        return "absent"
    except Exception:
        return "malformed"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--apply", action="store_true", help="write updates (default: dry-run)")
    args = ap.parse_args()

    con = sqlite3.connect(args.db, timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    rows = con.execute(
        "SELECT escalation_id, design, notes FROM escalations "
        "WHERE reason='catalog_exhausted' AND status='open'").fetchall()

    updates, from_counts = [], {}
    for eid, design, notes in rows:
        # only reconcile the bare pre-fix snapshot (or an unparseable/empty note)
        stale = (notes or "").strip() == STALE or not (notes or "").strip().startswith("{")
        if not stale:
            continue
        drc, lvs = _report_status(design, "drc"), _report_status(design, "lvs")
        new = json.dumps({"drc": drc, "lvs": lvs,
                          "reconciled": "2026-06-28 post-fix residual (cbcad40)"},
                         sort_keys=True)
        updates.append((eid, design, new))
        from_counts[(drc, lvs)] = from_counts.get((drc, lvs), 0) + 1

    print(f"open catalog_exhausted: {len(rows)}  |  stale-note rows to reconcile: {len(updates)}")
    print("real residual distribution:")
    for (drc, lvs), n in sorted(from_counts.items(), key=lambda x: -x[1]):
        print(f"  {n:4d}  drc={drc:10s} lvs={lvs}")

    if args.apply and updates:
        con.executemany("UPDATE escalations SET notes=? WHERE escalation_id=?",
                        [(new, eid) for eid, _d, new in updates])
        con.commit()
        print(f"\nAPPLIED {len(updates)} note updates.")
    elif updates:
        print("\n(dry-run; re-run with --apply to write)")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
