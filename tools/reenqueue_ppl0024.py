#!/usr/bin/env python3
"""Re-enqueue the open `unseen_crash` escalations that are actually PPL-0024 (IO pins
exceed die perimeter) so the campaign re-attempts them with the 2026-06-26 pin-aware
recovery (`_relieve_pin_overflow` -> enlarge the die -> more pin positions). Marks their
stale unseen_crash escalations `drained` (honest: acknowledged + being re-worked).

Safe alongside a running campaign: the ledger is JSONL (line-atomic O_APPEND) and the
driver re-reads it fresh each wave; the escalations UPDATE uses a busy_timeout. These
designs are currently ESCALATED (not in the campaign's pending set), so nothing else is
touching them this wave. Idempotent: a design already pending is just re-stated pending.
"""
import datetime
import glob
import json
import os
import sqlite3
import sys

DB = "r2g-skills/signoff-loop/knowledge/knowledge.sqlite"
LEDGER = "design_cases/_batch/campaign.jsonl"


def main():
    dry = "--apply" not in sys.argv
    con = sqlite3.connect(DB, timeout=30)
    rows = con.execute(
        "SELECT escalation_id, design, project_path FROM escalations "
        "WHERE status='open' AND reason='unseen_crash'").fetchall()
    ppl = []
    for eid, design, pp in rows:
        if not pp or not os.path.isdir(pp):
            continue
        logs = sorted(glob.glob(os.path.join(pp, "backend", "RUN_*", "flow.log")))
        if not logs:
            continue
        try:
            if "PPL-0024" in open(logs[-1], errors="ignore").read():
                ppl.append((eid, design, pp))
        except OSError:
            pass
    print(f"PPL-0024 mislabeled-unseen_crash escalations: {len(ppl)}"
          f"{' (DRY RUN — pass --apply to act)' if dry else ''}")
    if dry or not ppl:
        for _, d, _ in ppl[:10]:
            print(f"  would re-enqueue: {d}")
        return 0
    ts = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with open(LEDGER, "a") as f:                       # 1) re-enqueue to pending
        for _, design, pp in ppl:
            f.write(json.dumps({
                "design": design, "project_path": pp, "platform": "nangate45",
                "kind": "normal", "state": "pending", "ts": ts,
                "note": "re-enqueued for PPL-0024 pin-aware recovery 2026-06-26"}) + "\n")
    for eid, _, _ in ppl:                              # 2) drain the stale escalation
        con.execute(
            "UPDATE escalations SET status='drained', resolved_at=?, "
            "notes=COALESCE(notes,'')||' | drained: re-enqueued for PPL-0024 pin-aware "
            "recovery 2026-06-26' WHERE escalation_id=?", (ts, eid))
    con.commit()
    print(f"re-enqueued {len(ppl)} designs to pending + drained their unseen_crash rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
