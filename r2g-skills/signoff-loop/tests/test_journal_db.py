"""Tests for the Tier-0 journal DB (engineer-loop spec §5.2, decisions 10/11)."""
from pathlib import Path

import journal_db


def _conn(tmp_path: Path):
    c = journal_db.connect(tmp_path / "journal.sqlite")
    journal_db.ensure_schema(c)
    return c


def test_schema_creates_three_tables(tmp_path):
    c = _conn(tmp_path)
    tables = {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"actions", "log_summaries", "tool_bugs"} <= tables


def test_wal_mode_enabled(tmp_path):
    c = _conn(tmp_path)
    assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_append_action_returns_id_and_persists(tmp_path):
    c = _conn(tmp_path)
    aid = journal_db.append_action(
        c, project_path="/p/x", actor="loop", action_type="tool_invoke",
        payload={"cmd": "make route", "exit_code": 0, "duration_s": 12.5},
        design="aes", platform="nangate45")
    row = c.execute("SELECT project_path, actor, action_type, run_id "
                    "FROM actions WHERE action_id=?", (aid,)).fetchone()
    assert row == ("/p/x", "loop", "tool_invoke", None)


def test_backfill_run_id_links_all_rows_for_project(tmp_path):
    c = _conn(tmp_path)
    journal_db.append_action(c, project_path="/p/x", actor="loop",
                             action_type="tool_invoke", payload={})
    journal_db.append_log_summary(c, project_path="/p/x", stage="route",
                                  tool="openroad", source_path="/p/x/l.log",
                                  status="pass", digest="ok")
    journal_db.append_tool_bug(c, project_path="/p/x", stage="cts",
                               tool="openroad", signature="SIGSEGV in repair",
                               symptom_id="abc123", log_excerpt="...")
    n = journal_db.backfill_run_id(c, project_path="/p/x", run_id="RUN1")
    assert n == 3
    for t in ("actions", "log_summaries", "tool_bugs"):
        assert c.execute(f"SELECT run_id FROM {t}").fetchone()[0] == "RUN1"


def test_backfill_does_not_clobber_existing_run_id(tmp_path):
    c = _conn(tmp_path)
    journal_db.append_action(c, project_path="/p/x", actor="loop",
                             action_type="promote", payload={}, run_id="OLD")
    assert journal_db.backfill_run_id(c, project_path="/p/x", run_id="NEW") == 0
    assert c.execute("SELECT run_id FROM actions").fetchone()[0] == "OLD"


def test_ensure_schema_idempotent_on_legacy_db(tmp_path):
    c = _conn(tmp_path)
    journal_db.ensure_schema(c)   # second call must not raise
    journal_db.ensure_schema(c)
