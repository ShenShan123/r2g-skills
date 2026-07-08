"""Ingest stamps design_class/strength/generation and back-fills journal run_id."""
import json
from pathlib import Path

import ingest_run
import journal_db
import knowledge_db


def _mk_project(tmp_path: Path, name="aes_unit1", cells=1200) -> Path:
    p = tmp_path / name
    (p / "constraints").mkdir(parents=True)
    (p / "reports").mkdir()
    (p / "rtl").mkdir()
    (p / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = aes\nexport PLATFORM = nangate45\n")
    (p / "rtl" / "design.v").write_text("module aes(); // sbox cipher\nendmodule\n")
    (p / "reports" / "ppa.json").write_text(json.dumps(
        {"summary": {"timing": {"setup_wns": -0.05}},
         "geometry": {"instance_count": cells}}))
    (p / "reports" / "drc.json").write_text(json.dumps({"status": "clean"}))
    (p / "reports" / "lvs.json").write_text(json.dumps({"status": "clean"}))
    return p


def _conn(tmp_path):
    c = knowledge_db.connect(tmp_path / "knowledge.sqlite")
    knowledge_db.ensure_schema(c)
    return c


def test_design_class_stamped_structurally(tmp_path):
    conn = _conn(tmp_path)
    rid = ingest_run.ingest(_mk_project(tmp_path), conn)
    row = conn.execute("SELECT design_class FROM runs WHERE run_id=?",
                       (rid,)).fetchone()
    # RTL contains 'cipher'/'sbox' -> crypto; 1200 cells -> small
    assert row[0] == "crypto/small"


def test_first_attempt_clean_true_then_false_for_repeat(tmp_path):
    conn = _conn(tmp_path)
    p = _mk_project(tmp_path)
    ingest_run.ingest(p, conn)
    first = conn.execute("SELECT first_attempt_clean FROM runs").fetchone()[0]
    assert first == 1
    # touch ppa.json -> new run_id, same design+platform -> not first attempt
    ppa = p / "reports" / "ppa.json"
    ppa.write_text(ppa.read_text())
    import os
    os.utime(ppa, (os.path.getmtime(ppa) + 5, os.path.getmtime(ppa) + 5))
    rid2 = ingest_run.ingest(p, conn)
    assert conn.execute("SELECT first_attempt_clean FROM runs WHERE run_id=?",
                        (rid2,)).fetchone()[0] == 0


def test_generation_stamped_from_heuristics(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    h = tmp_path / "heuristics.json"
    h.write_text(json.dumps({"generation": 7, "families": {}}))
    monkeypatch.setenv("R2G_HEURISTICS_PATH", str(h))
    rid = ingest_run.ingest(_mk_project(tmp_path), conn)
    assert conn.execute("SELECT heuristics_generation FROM runs WHERE run_id=?",
                        (rid,)).fetchone()[0] == 7


def test_journal_run_id_backfilled(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    p = _mk_project(tmp_path)
    jdb = tmp_path / "journal.sqlite"
    monkeypatch.setenv("R2G_JOURNAL_DB", str(jdb))
    jc = journal_db.connect(jdb)
    journal_db.ensure_schema(jc)
    journal_db.append_action(jc, project_path=str(p.resolve()), actor="loop",
                             action_type="tool_invoke", payload={})
    rid = ingest_run.ingest(p, conn)
    assert jc.execute("SELECT run_id FROM actions").fetchone()[0] == rid


def test_report_digests_swept_at_ingest(tmp_path, monkeypatch):
    """Spec rev 3 catch-all: every reports/*.json gets a journal digest row."""
    conn = _conn(tmp_path)
    p = _mk_project(tmp_path)
    jdb = tmp_path / "journal.sqlite"
    monkeypatch.setenv("R2G_JOURNAL_DB", str(jdb))
    rid = ingest_run.ingest(p, conn)
    jc = journal_db.connect(jdb)
    rows = jc.execute("SELECT stage, run_id FROM log_summaries"
                      " WHERE tool='report'").fetchall()
    stages = {r[0] for r in rows}
    assert {"ppa", "drc", "lvs"} <= stages
    assert all(r[1] == rid for r in rows)
    # Re-ingest must not duplicate digest rows (idempotent sweep).
    ingest_run.ingest(p, conn)
    n2 = jc.execute("SELECT COUNT(*) FROM log_summaries"
                    " WHERE tool='report'").fetchone()[0]
    assert n2 == len(rows)


def test_fix_iters_to_clean_from_fix_log(tmp_path):
    conn = _conn(tmp_path)
    p = _mk_project(tmp_path)
    (p / "reports" / "fix_log.jsonl").write_text("\n".join([
        json.dumps({"fix_session_id": "s1", "check": "drc", "iter": 1,
                    "strategy": "a", "before": 9, "after": 4, "verdict": "applied"}),
        json.dumps({"fix_session_id": "s1", "check": "drc", "iter": 2,
                    "strategy": "b", "before": 4, "after": 0, "verdict": "cleared"}),
    ]))
    rid = ingest_run.ingest(p, conn)
    assert conn.execute("SELECT fix_iters_to_clean FROM runs WHERE run_id=?",
                        (rid,)).fetchone()[0] == 2
