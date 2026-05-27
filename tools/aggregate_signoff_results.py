#!/usr/bin/env python3
"""Aggregate signoff status across all design_cases.

Uses each design's reports/{drc,lvs,rcx}.json as the source of truth (current
state), not the batch JSONL (historical run-time record). Provides repo-wide
counts and surfaces any design whose final status isn't a known good outcome.
"""
import json
from pathlib import Path
from collections import Counter, defaultdict

REPO_ROOT = Path(__file__).resolve().parent.parent
DESIGN_CASES = REPO_ROOT / "design_cases"


def load_status(path: Path) -> str:
    if not path.exists():
        return "missing"
    try:
        return json.load(path.open()).get("status", "unknown")
    except Exception:
        return "unparseable"


def main():
    by_drc = Counter()
    by_lvs = Counter()
    by_rcx = Counter()
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
        if rcx not in ("complete",):
            failures["rcx"].append(design_dir.name)
        if drc not in ("clean", "violations", "stuck"):
            failures["drc"].append(design_dir.name)

    print(f"Total designs with ppa.json: {total}")
    print("\nDRC distribution:")
    for k, v in by_drc.most_common():
        print(f"  {k:>12}: {v}")
    print("\nLVS distribution:")
    for k, v in by_lvs.most_common():
        print(f"  {k:>12}: {v}")
    print("\nRCX distribution:")
    for k, v in by_rcx.most_common():
        print(f"  {k:>12}: {v}")
    if failures["rcx"]:
        print(f"\nDesigns with unexpected RCX status ({len(failures['rcx'])}):")
        for n in failures["rcx"][:15]:
            print(f"  - {n}")
        if len(failures["rcx"]) > 15:
            print(f"  ... and {len(failures['rcx']) - 15} more")
    if failures["drc"]:
        print(f"\nDesigns with unexpected DRC status ({len(failures['drc'])}):")
        for n in failures["drc"][:15]:
            print(f"  - {n}")
        if len(failures["drc"]) > 15:
            print(f"  ... and {len(failures['drc']) - 15} more")


if __name__ == "__main__":
    main()
