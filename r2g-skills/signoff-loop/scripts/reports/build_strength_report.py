#!/usr/bin/env python3
"""Strength metrics projection (engineer-loop spec §5.6, decision 6).

Read-only over knowledge.sqlite (mode=ro, like build_lineage_view). Strength =
first-pass clean rate UP and median iterations/wall-clock-to-clean DOWN as the
heuristics generation grows. Also lists per-symptom transfer evidence (one
strategy resolving the same symptom on 2+ distinct designs).

CLI: build_strength_report.py [--db PATH] [--out reports/strength.json]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from pathlib import Path

KNOWLEDGE = Path(__file__).resolve().parents[2] / "knowledge"


def _ro(path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)


def build(db_path) -> dict:
    conn = _ro(db_path)
    try:
        cur = conn.execute(
            "SELECT heuristics_generation AS gen, first_attempt_clean,"
            " fix_iters_to_clean, wall_s_to_clean FROM runs"
            " WHERE heuristics_generation IS NOT NULL")
        rows = [dict(zip([c[0] for c in cur.description], r))
                for r in cur.fetchall()]
        tcur = conn.execute(
            "SELECT symptom_id, winning_strategy, design_name"
            " FROM fix_trajectories WHERE outcome='resolved'"
            " AND winning_strategy IS NOT NULL AND symptom_id IS NOT NULL")
        traj = tcur.fetchall()
    finally:
        conn.close()

    by_gen: dict[int, list[dict]] = {}
    for r in rows:
        by_gen.setdefault(r["gen"], []).append(r)
    generations = []
    for gen in sorted(by_gen):
        g = by_gen[gen]
        firsts = [r["first_attempt_clean"] for r in g
                  if r["first_attempt_clean"] is not None]
        iters = [r["fix_iters_to_clean"] for r in g
                 if r["fix_iters_to_clean"] is not None]
        walls = [r["wall_s_to_clean"] for r in g
                 if r["wall_s_to_clean"] is not None]
        generations.append({
            "generation": gen,
            "n_runs": len(g),
            "first_pass_clean_rate": (sum(firsts) / len(firsts)) if firsts else None,
            "median_fix_iters_to_clean": (int(statistics.median(iters))
                                          if iters else None),
            "median_wall_s_to_clean": (statistics.median(walls)
                                       if walls else None),
        })
    rated = [g for g in generations if g["first_pass_clean_rate"] is not None]
    improving = (len(rated) >= 2
                 and rated[-1]["first_pass_clean_rate"]
                 > rated[0]["first_pass_clean_rate"])

    transfer: dict[tuple, set] = {}
    for sid, strat, design in traj:
        transfer.setdefault((sid, strat), set()).add(design)
    transfer_evidence = [
        {"symptom_id": sid, "strategy": strat, "designs": sorted(designs)}
        for (sid, strat), designs in sorted(transfer.items())
        if len(designs) >= 2]

    return {"generations": generations,
            "trend": {"first_pass_improving": improving},
            "transfer_evidence": transfer_evidence}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=KNOWLEDGE / "knowledge.sqlite")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    rep = build(args.db)
    text = json.dumps(rep, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
