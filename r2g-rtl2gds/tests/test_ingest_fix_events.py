"""Tests for fix-event ingestion + the three new knowledge tables."""
from __future__ import annotations

import knowledge_db


def _tables(conn):
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def test_schema_creates_fix_tables(tmp_knowledge_dir):
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    tables = _tables(conn)
    assert {"fix_events", "fix_trajectories", "run_violations", "fix_events_archive"} <= tables
    # fix_events has the lossless detail columns + the idempotency constraint
    cols = {r[1] for r in conn.execute("PRAGMA table_info(fix_events)")}
    assert {"fix_session_id", "iter", "strategy", "violation_class", "verdict",
            "before_categories_json", "after_categories_json", "rule_details_json",
            "cumulative_config_json", "env_flags_json", "tool_versions_json",
            "stage_metrics_json", "provenance"} <= cols
    conn.close()


def test_fix_events_unique_constraint(tmp_knowledge_dir):
    import sqlite3
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    ins = ("INSERT OR IGNORE INTO fix_events "
           "(fix_session_id, iter, strategy) VALUES (?,?,?)")
    conn.execute(ins, ("sess1", 1, "antenna_diode_repair"))
    conn.execute(ins, ("sess1", 1, "antenna_diode_repair"))  # dup -> ignored
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM fix_events").fetchone()[0]
    assert n == 1
    conn.close()
