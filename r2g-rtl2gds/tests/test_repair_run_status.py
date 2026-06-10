"""Tests for repair_run_status.py — reconcile dead orfs_status='partial' rows.

The one-time repair pass re-reads each project's latest
backend/RUN_*/stage_log.jsonl and re-derives orfs_status via the same
ingest_run helpers, updating the row only when the derived value differs.
It is read-from-stage-log only, idempotent, and backs up the DB to <db>.bak
before writing.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import knowledge_db
import repair_run_status


def _make_project(root: Path, name: str) -> Path:
    """A fixture project with clean signoff reports and an all-pass stage log."""
    proj = root / name
    (proj / "reports").mkdir(parents=True)
    (proj / "reports" / "drc.json").write_text(
        json.dumps({"status": "clean", "total_violations": 0}))
    (proj / "reports" / "lvs.json").write_text(json.dumps({"status": "clean"}))
    (proj / "reports" / "rcx.json").write_text(json.dumps({"status": "complete"}))
    run_dir = proj / "backend" / "RUN_2026-06-05_00-00-00"
    run_dir.mkdir(parents=True)
    # Record shape that ingest_run._derive_orfs_status parses as all-pass:
    # status == "pass" for each of the six required stages.
    stages = ["synth", "floorplan", "place", "cts", "route", "finish"]
    with (run_dir / "stage_log.jsonl").open("w") as fh:
        for st in stages:
            fh.write(json.dumps({"stage": st, "status": "pass", "elapsed_s": 1}) + "\n")
    return proj


def _new_db(tmp_knowledge_dir: Path) -> Path:
    db_path = tmp_knowledge_dir / "knowledge.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    conn.close()
    return db_path


def _insert_run(conn: sqlite3.Connection, run_id: str, project: Path,
                status: str = "partial") -> None:
    conn.execute(
        "INSERT INTO runs (run_id, project_path, design_name, ingested_at, "
        "orfs_status, orfs_fail_stage) VALUES (?,?,?,?,?,?)",
        (run_id, str(project.resolve()), project.name,
         "2026-06-05T00:00:00Z", status, "route"),
    )
    conn.commit()


def test_repair_flips_partial_to_pass(tmp_knowledge_dir, tmp_path):
    db_path = _new_db(tmp_knowledge_dir)
    proj = _make_project(tmp_path / "cases", "good_design")

    conn = knowledge_db.connect(db_path)
    _insert_run(conn, "run1", proj, status="partial")

    repair_run_status.repair(tmp_path / "cases", conn)

    row = conn.execute(
        "SELECT orfs_status, orfs_fail_stage FROM runs WHERE run_id='run1'"
    ).fetchone()
    assert row[0] == "pass"
    assert row[1] is None
    conn.close()


def test_repair_is_idempotent(tmp_knowledge_dir, tmp_path):
    db_path = _new_db(tmp_knowledge_dir)
    proj = _make_project(tmp_path / "cases", "good_design")

    conn = knowledge_db.connect(db_path)
    _insert_run(conn, "run1", proj, status="partial")

    repair_run_status.repair(tmp_path / "cases", conn)
    first = conn.execute(
        "SELECT orfs_status FROM runs WHERE run_id='run1'").fetchone()[0]
    # Second pass must change nothing.
    repair_run_status.repair(tmp_path / "cases", conn)
    second = conn.execute(
        "SELECT orfs_status FROM runs WHERE run_id='run1'").fetchone()[0]
    assert first == second == "pass"
    conn.close()


def test_repair_leaves_unresolvable_rows_untouched(tmp_knowledge_dir, tmp_path):
    """A row whose project dir is missing keeps its original status."""
    db_path = _new_db(tmp_knowledge_dir)
    missing = tmp_path / "cases" / "gone"

    conn = knowledge_db.connect(db_path)
    _insert_run(conn, "run_gone", missing, status="partial")

    repair_run_status.repair(tmp_path / "cases", conn)

    status = conn.execute(
        "SELECT orfs_status FROM runs WHERE run_id='run_gone'").fetchone()[0]
    assert status == "partial"
    conn.close()


def test_main_creates_backup(tmp_knowledge_dir, tmp_path, capsys):
    db_path = _new_db(tmp_knowledge_dir)
    proj = _make_project(tmp_path / "cases", "good_design")

    conn = knowledge_db.connect(db_path)
    _insert_run(conn, "run1", proj, status="partial")
    conn.close()

    repair_run_status.main([
        "--db", str(db_path),
        "--cases-root", str(tmp_path / "cases"),
    ])

    bak = Path(str(db_path) + ".bak")
    assert bak.exists()

    # The histogram is printed and the row was repaired on disk.
    out = capsys.readouterr().out
    assert "orfs_status" in out
    conn = knowledge_db.connect(db_path)
    status = conn.execute(
        "SELECT orfs_status FROM runs WHERE run_id='run1'").fetchone()[0]
    assert status == "pass"
    conn.close()
