#!/usr/bin/env python3
"""Reconcile dead orfs_status='partial' rows from their per-project stage logs.

Most historical `runs` rows carry orfs_status='partial' only because their
backend/RUN_*/stage_log.jsonl was incomplete at ingest time. This one-time
pass re-reads each project's *latest* stage log and re-derives the status with
the very same helpers ingest_run uses (`_read_stage_log` + `_derive_orfs_status`),
then UPDATEs the row only when the freshly-derived value differs.

Properties:
  * Read-from-stage-log only — never invents a status; uses the faithful
    ingest_run derivation, so the corpus the learner sees matches reality.
  * Idempotent — re-running changes nothing once rows are reconciled.
  * Reversible — main() copies the DB to <db>.bak (shutil.copy2) before writing
    and prints a before/after orfs_status histogram.

Usage:
  repair_run_status.py --db knowledge/knowledge.sqlite [--cases-root design_cases]
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

import ingest_run


def _find_latest_stage_log(project: Path) -> Optional[Path]:
    """Locate the most-recently-modified backend/RUN_*/stage_log.jsonl, falling
    back to the legacy flat backend/stage_log.jsonl — mirrors ingest_run."""
    backend = project / "backend"
    if backend.is_dir():
        run_dirs = sorted(
            (d for d in backend.iterdir()
             if d.is_dir() and d.name.startswith("RUN_")),
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        for rd in run_dirs:
            candidate = rd / "stage_log.jsonl"
            if candidate.exists():
                return candidate
    legacy = backend / "stage_log.jsonl"
    if legacy.exists():
        return legacy
    return None


def _resolve_project(project_path: Optional[str], cases_root: Path) -> Optional[Path]:
    """Find the project dir for a runs row.

    Prefer the stored absolute project_path; if it has since moved, fall back to
    <cases_root>/<basename>. Returns None when neither exists.
    """
    if project_path:
        p = Path(project_path)
        if p.is_dir():
            return p
        relocated = cases_root / p.name
        if relocated.is_dir():
            return relocated
    return None


def repair(cases_root: Path | str, conn: sqlite3.Connection) -> int:
    """Re-derive orfs_status for every runs row from its latest stage log.

    Returns the number of rows whose orfs_status (or orfs_fail_stage) changed.
    """
    cases_root = Path(cases_root)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT run_id, project_path, orfs_status, orfs_fail_stage FROM runs"
    ).fetchall()

    changed = 0
    for row in rows:
        project = _resolve_project(row["project_path"], cases_root)
        if project is None:
            continue
        stage_log_path = _find_latest_stage_log(project)
        if stage_log_path is None:
            continue
        stages = ingest_run._read_stage_log(stage_log_path)
        new_status, new_fail_stage = ingest_run._derive_orfs_status(stages)
        # 'unknown' means the stage log was empty/unparseable — never downgrade a
        # row to it; the original value is at least as informative.
        if new_status == "unknown":
            continue
        if new_status == row["orfs_status"] and new_fail_stage == row["orfs_fail_stage"]:
            continue
        conn.execute(
            "UPDATE runs SET orfs_status = ?, orfs_fail_stage = ? WHERE run_id = ?",
            (new_status, new_fail_stage, row["run_id"]),
        )
        changed += 1

    conn.commit()
    return changed


def _status_histogram(conn: sqlite3.Connection) -> Counter:
    return Counter(
        (r[0] if r[0] is not None else "NULL")
        for r in conn.execute("SELECT orfs_status FROM runs")
    )


def _format_histogram(hist: Counter) -> str:
    lines = []
    for status, n in sorted(hist.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"  {status:<10} {n}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Repair dead orfs_status='partial' runs rows from stage logs.")
    parser.add_argument("--db", required=True,
                        help="Path to knowledge/knowledge.sqlite.")
    parser.add_argument("--cases-root", default="design_cases",
                        help="Root holding the project dirs (relocation fallback).")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"error: DB not found: {db_path}", file=sys.stderr)
        return 1

    # Reversible: back up before we touch a single row.
    bak_path = Path(str(db_path) + ".bak")
    shutil.copy2(db_path, bak_path)
    print(f"backed up {db_path} -> {bak_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        before = _status_histogram(conn)
        print("orfs_status (before):")
        print(_format_histogram(before))

        changed = repair(args.cases_root, conn)

        after = _status_histogram(conn)
        print(f"\nrepaired {changed} row(s).")
        print("orfs_status (after):")
        print(_format_histogram(after))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
