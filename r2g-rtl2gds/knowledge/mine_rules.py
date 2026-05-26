#!/usr/bin/env python3
"""Surface repeated failure signatures as a review queue.

Usage:
  mine_rules.py [--db <path>] [--out <path>]
                [--min-occurrences 3] [--min-distinct-designs 2]

Scans failure_events + runs, groups by signature, and emits
knowledge/failure_candidates.json — a human-review queue for new
entries in references/failure-patterns.md.

Never auto-merges into failure-patterns.md.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import knowledge_db


def _fetch(conn) -> list[dict]:
    sql = (
        "SELECT r.design_name, r.design_family, r.platform, "
        "r.core_utilization, r.place_density_lb_addon, "
        "r.synth_hierarchical, r.abc_area, "
        "f.signature, f.stage, f.detail "
        "FROM failure_events f "
        "JOIN runs r ON r.run_id = f.run_id"
    )
    cur = conn.execute(sql)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _median(values):
    cleaned = [v for v in values if v is not None]
    return statistics.median(cleaned) if cleaned else None


def mine(db_path: Path | str,
         out_path: Path | str,
         min_occurrences: int = 3,
         min_distinct_designs: int = 2) -> dict:
    db_path = Path(db_path)
    out_path = Path(out_path)

    with contextlib.closing(knowledge_db.connect(db_path)) as conn:
        rows = _fetch(conn)

    by_sig: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_sig[r["signature"]].append(r)

    candidates = []
    for sig, group in sorted(by_sig.items()):
        distinct_designs = {r["design_name"] for r in group}
        if len(group) < min_occurrences:
            continue
        if len(distinct_designs) < min_distinct_designs:
            continue
        candidates.append({
            "signature": sig,
            "occurrences": len(group),
            "distinct_designs": len(distinct_designs),
            "designs": sorted(distinct_designs),
            "stages": sorted({r["stage"] for r in group if r["stage"]}),
            "config_medians": {
                "core_utilization": _median([r["core_utilization"] for r in group]),
                "place_density_lb_addon": _median(
                    [r["place_density_lb_addon"] for r in group]
                ),
                "synth_hierarchical_rate": (
                    sum(1 for r in group if r["synth_hierarchical"]) / len(group)
                ),
                "abc_area_rate": sum(1 for r in group if r["abc_area"]) / len(group),
            },
            "sample_detail": next((r["detail"] for r in group if r["detail"]), None),
        })

    data = {
        "generated_at": _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "min_occurrences": min_occurrences,
        "min_distinct_designs": min_distinct_designs,
        "candidates": candidates,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", type=Path, default=knowledge_db.DEFAULT_DB_PATH)
    p.add_argument("--out", type=Path,
                   default=knowledge_db.DEFAULT_KNOWLEDGE_DIR / "failure_candidates.json")
    p.add_argument("--min-occurrences", type=int, default=3)
    p.add_argument("--min-distinct-designs", type=int, default=2)
    args = p.parse_args()

    data = mine(args.db, args.out,
                min_occurrences=args.min_occurrences,
                min_distinct_designs=args.min_distinct_designs)
    print(f"Wrote {args.out} ({len(data['candidates'])} candidate signatures).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
