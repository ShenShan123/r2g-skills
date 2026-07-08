#!/usr/bin/env python3
"""Cross-DB provenance tracing (engineer-loop spec §5.9, decision 11).

Read-only on BOTH DBs (URI mode=ro, same discipline as build_lineage_view).
solution_origin(): recipe -> ab_trials + fix_trajectories -> journal actions
-> runs/designs -> symptoms/tool_bugs. bug_solutions(): symptom -> every known
solution + lifecycle status + the designs it was proven on.

CLI:
  trace_provenance.py solution --symptom S --class C --platform P --strategy ST
  trace_provenance.py bug --symptom S
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

KNOWLEDGE = Path(__file__).resolve().parent
DEFAULT_KDB = KNOWLEDGE / "knowledge.sqlite"
DEFAULT_JDB = KNOWLEDGE / "journal.sqlite"


def _ro(path: Path | str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)


def _rows(conn, sql, params=()) -> list[dict]:
    cur = conn.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def solution_origin(*, symptom_id: str, design_class: str, platform: str,
                    strategy: str, knowledge_db_path=DEFAULT_KDB,
                    journal_db_path=DEFAULT_JDB) -> dict:
    kc = _ro(knowledge_db_path)
    try:
        st = kc.execute(
            "SELECT status, provenance FROM recipe_status WHERE symptom_id=? "
            "AND design_class=? AND platform=? AND strategy=?",
            (symptom_id, design_class, platform, strategy)).fetchone()
        trials = _rows(kc, "SELECT trial_id, verdict, match_level, metrics_json,"
                           " ts FROM ab_trials WHERE symptom_id=? AND strategy=?"
                           " ORDER BY trial_id", (symptom_id, strategy))
        episodes = _rows(kc, "SELECT fix_session_id, design_name, project_path,"
                             " platform, outcome, winning_strategy "
                             "FROM fix_trajectories WHERE symptom_id=? AND"
                             " winning_strategy=?", (symptom_id, strategy))
        symptoms = _rows(kc, "SELECT check_type, class, predicates_json "
                             "FROM symptoms WHERE symptom_id=?", (symptom_id,))
    finally:
        kc.close()
    bugs: list[dict] = []
    if Path(journal_db_path).exists():
        jc = _ro(journal_db_path)
        try:
            for ep in episodes:
                ep["actions"] = _rows(
                    jc, "SELECT action_id, action_type, payload_json, ts "
                        "FROM actions WHERE fix_session_id=? ORDER BY action_id",
                    (ep["fix_session_id"],))
            bugs = _rows(jc, "SELECT project_path, stage, tool, signature, ts "
                             "FROM tool_bugs WHERE symptom_id=?", (symptom_id,))
        finally:
            jc.close()
    else:
        for ep in episodes:
            ep["actions"] = []
    return {
        "key": {"symptom_id": symptom_id, "design_class": design_class,
                "platform": platform, "strategy": strategy},
        "status": st[0] if st else "promoted",     # grandfathered default
        "provenance": st[1] if st else "grandfathered",
        "symptom": symptoms[0] if symptoms else None,
        "ab_trials": trials,
        "episodes": episodes,                       # designs + their actions
        "bugs": bugs,
    }


def bug_solutions(*, symptom_id: str, knowledge_db_path=DEFAULT_KDB) -> list[dict]:
    kc = _ro(knowledge_db_path)
    try:
        sols = _rows(kc, "SELECT winning_strategy AS strategy,"
                         " COUNT(*) AS n_resolved,"
                         " GROUP_CONCAT(DISTINCT design_name) AS proven_on "
                         "FROM fix_trajectories WHERE symptom_id=? AND"
                         " outcome='resolved' AND winning_strategy IS NOT NULL "
                         "GROUP BY winning_strategy ORDER BY n_resolved DESC",
                     (symptom_id,))
        for s in sols:
            row = kc.execute(
                "SELECT status FROM recipe_status WHERE symptom_id=? AND"
                " strategy=? ORDER BY julianday(updated_at) DESC LIMIT 1",
                (symptom_id, s["strategy"])).fetchone()
            s["status"] = row[0] if row else "promoted"
            s["proven_on"] = (s["proven_on"] or "").split(",")
    finally:
        kc.close()
    return sols


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ps = sub.add_parser("solution")
    for f in ("--symptom", "--class", "--platform", "--strategy"):
        ps.add_argument(f, required=True, dest=f.lstrip("-").replace("class", "dclass"))
    pb = sub.add_parser("bug")
    pb.add_argument("--symptom", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "solution":
        out = solution_origin(symptom_id=args.symptom, design_class=args.dclass,
                              platform=args.platform, strategy=args.strategy)
    else:
        out = bug_solutions(symptom_id=args.symptom)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
