#!/usr/bin/env python3
"""Aggregate signoff status across all design_cases.

Uses each design's reports/{drc,lvs,rcx}.json as the source of truth (current
state), not the batch JSONL (historical run-time record). Provides repo-wide
counts, a per-design terminal state (clean|partial|failed — pilot P1-3), and
surfaces any design whose final status isn't a known good outcome.

Exit code: 0 by default (reporter). With --strict, exit 1 when any counted
design is not `clean` — the pilot found a caller observing only the process
return code misread a batch with a DRC-dirty and a drc=stuck design as
successful, because `violations` and `stuck` were classified as acceptable.
They are non-clean terminal states, never successes.
"""
import argparse
import json
import sys
from pathlib import Path
from collections import Counter, defaultdict

REPO_ROOT = Path(__file__).resolve().parent.parent
DESIGN_CASES = REPO_ROOT / "design_cases"

# Per-check clean states (mirrors fix_signoff.sh's fail-closed clean gate).
DRC_CLEAN = {"clean", "clean_beol"}
LVS_CLEAN = {"clean", "skipped"}
RCX_CLEAN = {"complete"}
# Statuses that mean the check RAN and the design is definitively dirty/stuck —
# `failed`, not merely `partial` (pilot P1-3: these were counted acceptable).
DRC_DIRTY = {"fail", "failed", "violations", "stuck", "timeout"}
LVS_DIRTY = {"fail", "failed", "crash", "incomplete"}


def load_status(path: Path) -> str:
    if not path.exists():
        return "missing"
    try:
        return json.load(path.open()).get("status", "unknown")
    except Exception:
        return "unparseable"


def terminal_state(drc: str, lvs: str, rcx: str) -> str:
    if drc in DRC_CLEAN and lvs in LVS_CLEAN and rcx in RCX_CLEAN:
        return "clean"
    if drc in DRC_DIRTY or lvs in LVS_DIRTY:
        return "failed"
    return "partial"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 unless every counted design is clean (pilot P1-3)")
    args = ap.parse_args()

    by_drc = Counter()
    by_lvs = Counter()
    by_rcx = Counter()
    by_state = Counter()
    failures = defaultdict(list)
    total = 0

    for design_dir in sorted(DESIGN_CASES.iterdir()):
        if not design_dir.is_dir() or design_dir.name.startswith("_"):
            continue
        reports = design_dir / "reports"
        # Only count designs that have a ppa.json (a completed ORFS run).
        if not (reports / "ppa.json").exists():
            continue
        total += 1
        drc = load_status(reports / "drc.json")
        lvs = load_status(reports / "lvs.json")
        rcx = load_status(reports / "rcx.json")
        by_drc[drc] += 1
        by_lvs[lvs] += 1
        by_rcx[rcx] += 1
        state = terminal_state(drc, lvs, rcx)
        by_state[state] += 1
        if state != "clean":
            failures[state].append(f"{design_dir.name} (drc={drc}, lvs={lvs}, rcx={rcx})")

    print(f"Total designs with ppa.json: {total}")
    print("\nTerminal state (pilot P1-3):")
    for k in ("clean", "partial", "failed"):
        print(f"  {k:>12}: {by_state.get(k, 0)}")
    print("\nDRC distribution:")
    for k, v in by_drc.most_common():
        print(f"  {k:>12}: {v}")
    print("\nLVS distribution:")
    for k, v in by_lvs.most_common():
        print(f"  {k:>12}: {v}")
    print("\nRCX distribution:")
    for k, v in by_rcx.most_common():
        print(f"  {k:>12}: {v}")
    for state in ("failed", "partial"):
        if failures[state]:
            print(f"\nDesigns {state} ({len(failures[state])}):")
            for n in failures[state][:15]:
                print(f"  - {n}")
            if len(failures[state]) > 15:
                print(f"  ... and {len(failures[state]) - 15} more")

    if args.strict and (by_state.get("partial", 0) or by_state.get("failed", 0)):
        print("\nstrict mode: NOT all designs clean -> exit 1", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
