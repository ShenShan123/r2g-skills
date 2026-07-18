#!/usr/bin/env python3
"""Read-only observability over the two memory DBs (2026-07-18 consolidation of
monitor_health.py + trace_provenance.py).

Two forensics lenses, one module, ZERO writes:
  * health — family/platform degradation alerts: recent success rate vs the
    historical baseline (inspired by OpenSpace quality monitoring). Success is
    judged by the ONE shared predicate knowledge_db.is_success (README inv 6),
    so the monitor and the learner can never disagree.
  * trace  — cross-DB provenance (engineer-loop spec §5.9, decision 11).
    solution_origin(): recipe -> ab_trials + fix_trajectories -> journal
    actions -> runs/designs -> symptoms/tool_bugs. bug_solutions(): symptom ->
    every known solution + lifecycle status + the designs it was proven on.
    Opens BOTH DBs URI mode=ro (same discipline as build_lineage_view).

CLI:
  observe.py health [--db P] [--window N] [--threshold F] [--out P]
  observe.py trace solution --symptom S --class C --platform P --strategy ST
  observe.py trace bug --symptom S
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import knowledge_db  # noqa: E402

KNOWLEDGE = Path(__file__).resolve().parent
DEFAULT_KDB = KNOWLEDGE / "knowledge.sqlite"
DEFAULT_JDB = KNOWLEDGE / "journal.sqlite"

# Single source of truth for "a learnable success" lives in knowledge_db, so
# the health monitor and the learner agree on what "success" means.
_is_success = knowledge_db.is_success


# --- health (formerly monitor_health.py) ------------------------------------

def _fetch_all(conn) -> list[dict]:
    cur = conn.execute(
        "SELECT design_family, platform, orfs_status, drc_status, "
        "lvs_status, lvs_mismatch_class, rcx_status, ingested_at "
        "FROM runs ORDER BY julianday(ingested_at) ASC"
    )
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def check(db_path: Path | str,
          window: int = 5,
          threshold: float = 0.3) -> list[dict]:
    """Check for degraded family/platform pairs.

    Args:
        db_path: Path to knowledge.sqlite.
        window: Number of most recent runs to evaluate per family/platform.
        threshold: Minimum drop in success rate (recent vs historical) to alert.

    Returns:
        List of alert dicts with keys: family, platform, recent_success_rate,
        historical_success_rate, recent_window, total_runs, severity,
        recent_failures.
    """
    with contextlib.closing(knowledge_db.connect(db_path)) as conn:
        rows = _fetch_all(conn)

    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        fam = r.get("design_family") or "unknown"
        plat = r.get("platform") or "unknown"
        groups.setdefault((fam, plat), []).append(r)

    alerts = []
    for (fam, plat), group in sorted(groups.items()):
        if len(group) < window:
            continue

        recent = group[-window:]
        historical = group[:-window] if len(group) > window else group

        recent_successes = sum(1 for r in recent if _is_success(r))
        recent_rate = recent_successes / len(recent)

        hist_successes = sum(1 for r in historical if _is_success(r))
        hist_rate = hist_successes / len(historical) if historical else 1.0

        drop = hist_rate - recent_rate
        if drop < threshold:
            continue

        recent_failures = [
            r.get("orfs_status", "unknown")
            for r in recent if not _is_success(r)
        ]

        alerts.append({
            "family": fam,
            "platform": plat,
            "recent_success_rate": round(recent_rate, 3),
            "historical_success_rate": round(hist_rate, 3),
            "recent_window": window,
            "total_runs": len(group),
            "severity": "degraded" if recent_rate < 0.5 else "warning",
            "recent_failures": recent_failures,
        })

    return alerts


# --- trace (formerly trace_provenance.py) -----------------------------------

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


# --- CLI --------------------------------------------------------------------

def _cmd_health(args) -> int:
    alerts = check(args.db, window=args.window, threshold=args.threshold)
    output = json.dumps(alerts, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
        print(f"Wrote {len(alerts)} alert(s) to {args.out}")
    else:
        print(output)
    if alerts:
        for a in alerts:
            print(f"  [{a['severity'].upper()}] {a['family']}/{a['platform']}: "
                  f"recent {a['recent_success_rate']:.0%} vs "
                  f"historical {a['historical_success_rate']:.0%}",
                  file=sys.stderr)
    else:
        print("All family/platform pairs healthy.", file=sys.stderr)
    return 1 if alerts else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    ph = sub.add_parser("health", help="family/platform degradation alerts")
    ph.add_argument("--db", type=Path, default=knowledge_db.DEFAULT_DB_PATH)
    ph.add_argument("--window", type=int, default=5,
                    help="Number of recent runs to evaluate (default: 5)")
    ph.add_argument("--threshold", type=float, default=0.3,
                    help="Minimum success rate drop to alert (default: 0.3)")
    ph.add_argument("--out", type=Path, default=None,
                    help="Write alerts JSON to file (default: stdout)")

    pt = sub.add_parser("trace", help="cross-DB provenance queries")
    tsub = pt.add_subparsers(dest="trace_cmd", required=True)
    ps = tsub.add_parser("solution")
    for f in ("--symptom", "--class", "--platform", "--strategy"):
        ps.add_argument(f, required=True,
                        dest=f.lstrip("-").replace("class", "dclass"))
    pb = tsub.add_parser("bug")
    pb.add_argument("--symptom", required=True)

    args = ap.parse_args(argv)
    if args.cmd == "health":
        return _cmd_health(args)
    if args.trace_cmd == "solution":
        out = solution_origin(symptom_id=args.symptom, design_class=args.dclass,
                              platform=args.platform, strategy=args.strategy)
    else:
        out = bug_solutions(symptom_id=args.symptom)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
