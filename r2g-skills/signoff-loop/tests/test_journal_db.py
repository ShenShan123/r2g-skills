"""Tests for the Tier-0 journal module — DB helpers, deterministic summarizer,
and producer CLI (engineer-loop spec §5.2, decisions 10/11; the three former
files journal_db/summarize_log/journal_action merged 2026-07-18)."""
import json
import subprocess
import sys
from pathlib import Path

import journal_db

CLI = Path(__file__).resolve().parents[1] / "knowledge" / "journal_db.py"


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


# --- Summarizer (spec decision 10) ------------------------------------------

PASS_LOG = """[INFO GRT-0001] starting global route
[WARNING GRT-0044] congestion at gcell (1,2)
Finished route: 0 violations.
"""

FAIL_LOG = """[INFO DRT-0001] start detailed routing
[ERROR DRT-0085] cannot fix violation
[WARNING DRT-0009] net u1/n3 ripped up
Signal 11 received
""" + "\n".join(f"tail line {i}" for i in range(40))


def test_pass_log_counts_and_digest():
    s = journal_db.summarize_text(PASS_LOG, status_hint="pass")
    assert s["error_count"] == 0
    assert s["warning_count"] == 1
    assert s["first_error"] is None
    assert s["last_lines"] is None            # tail only kept on failure
    assert "0 errors, 1 warnings" in s["digest"]


def test_fail_log_first_error_and_bounded_tail():
    s = journal_db.summarize_text(FAIL_LOG, status_hint="fail")
    assert s["error_count"] == 1
    assert "[ERROR DRT-0085]" in s["first_error"]
    tail = s["last_lines"].splitlines()
    assert len(tail) <= journal_db.TAIL_LINES
    assert tail[-1] == "tail line 39"


def test_detect_bugs_finds_sigsegv_with_symptom():
    bugs = journal_db.detect_bugs(FAIL_LOG, check="orfs_stage", vclass="route")
    assert len(bugs) == 1
    b = bugs[0]
    assert "signal 11" in b["signature"].lower()
    assert b["symptom_id"] and len(b["symptom_id"]) == 16


def test_summarize_report_json_extracts_metrics():
    rep = {"status": "fail", "total_violations": 7,
           "categories": {"M3_ANTENNA": {"count": 7}}}
    s = journal_db.summarize_report(rep, kind="drc")
    assert s["status"] == "fail"
    assert s["metrics"]["total_violations"] == 7
    assert "M3_ANTENNA" in s["digest"]


def test_summarizer_deterministic():
    a = journal_db.summarize_text(FAIL_LOG, status_hint="fail")
    b = journal_db.summarize_text(FAIL_LOG, status_hint="fail")
    assert a == b


# --- Producer CLI (spec §5.2) -----------------------------------------------

def _run(args, env_extra=None):
    import os
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(CLI)] + args,
                          capture_output=True, text=True, env=env)


def test_action_subcommand_appends_row(tmp_path):
    db = tmp_path / "journal.sqlite"
    r = _run(["action", "--project", "/p/x", "--actor", "agent",
              "--type", "config_knob_delta",
              "--payload", json.dumps({"knob": "CORE_UTILIZATION",
                                       "old": "30", "new": "20"}),
              "--db", str(db)])
    assert r.returncode == 0, r.stderr
    c = journal_db.connect(db)
    row = c.execute("SELECT actor, action_type, payload_json FROM actions").fetchone()
    assert row[0] == "agent" and row[1] == "config_knob_delta"
    assert json.loads(row[2])["knob"] == "CORE_UTILIZATION"


def test_summarize_subcommand_digests_log_file(tmp_path):
    db = tmp_path / "journal.sqlite"
    log = tmp_path / "route.log"
    log.write_text("[ERROR DRT-0085] cannot fix violation\nSignal 11 received\n")
    r = _run(["summarize", "--project", "/p/x", "--stage", "route",
              "--tool", "openroad", "--log", str(log), "--status", "fail",
              "--db", str(db)])
    assert r.returncode == 0, r.stderr
    c = journal_db.connect(db)
    assert c.execute("SELECT COUNT(*) FROM log_summaries").fetchone()[0] == 1
    # Failure log with a SIGSEGV pattern also lands a tool_bugs row.
    assert c.execute("SELECT COUNT(*) FROM tool_bugs").fetchone()[0] == 1


def test_report_subcommand_digests_json_report(tmp_path):
    """Spec rev 3: reports generated by ORFS/EDA tools are summarized too."""
    db = tmp_path / "journal.sqlite"
    rep = tmp_path / "drc.json"
    rep.write_text(json.dumps({"status": "fail", "total_violations": 7,
                               "categories": {"M3_ANTENNA": {"count": 7}}}))
    r = _run(["report", "--project", "/p/x", "--kind", "drc",
              "--file", str(rep), "--db", str(db)])
    assert r.returncode == 0, r.stderr
    c = journal_db.connect(db)
    row = c.execute("SELECT stage, status, metrics_json FROM log_summaries").fetchone()
    assert row[0] == "drc" and row[1] == "fail"
    assert json.loads(row[2])["total_violations"] == 7


def test_journal_disabled_is_silent_noop(tmp_path):
    db = tmp_path / "journal.sqlite"
    r = _run(["action", "--project", "/p/x", "--actor", "loop",
              "--type", "promote", "--db", str(db)],
             env_extra={"R2G_JOURNAL": "0"})
    assert r.returncode == 0
    assert not db.exists()


def test_bad_db_path_warns_but_exits_zero(tmp_path):
    # Journal failures must NEVER break the flow (spec §7).
    r = _run(["action", "--project", "/p/x", "--actor", "loop",
              "--type", "promote", "--db", "/nonexistent/dir/x/j.sqlite"])
    assert r.returncode == 0
    assert "WARNING" in r.stderr


def test_cli_honors_r2g_journal_db_env(tmp_path):
    """Without --db the CLI resolves R2G_JOURNAL_DB before the shipped default
    (2026-07-18 merge: parity with every library-path caller)."""
    db = tmp_path / "env_journal.sqlite"
    r = _run(["action", "--project", "/p/x", "--actor", "loop",
              "--type", "promote"], env_extra={"R2G_JOURNAL_DB": str(db)})
    assert r.returncode == 0, r.stderr
    assert db.exists()
    c = journal_db.connect(db)
    assert c.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 1
