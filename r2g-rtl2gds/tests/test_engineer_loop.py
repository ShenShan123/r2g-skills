"""Engineer-loop orchestrator: ledger state machine + one full turn (spec §5.1/§6)."""
import json
import os
from pathlib import Path

import engineer_loop


def _entry(name="d0", kind="normal"):
    return {"design": name, "project_path": f"/p/{name}",
            "platform": "nangate45", "kind": kind}


def test_ledger_roundtrip_and_resume(tmp_path):
    led = engineer_loop.Ledger(tmp_path / "ledger.jsonl")
    led.add(_entry("d0"))
    led.add(_entry("d1"))
    led.set_state("d0", "clean")
    led2 = engineer_loop.Ledger(tmp_path / "ledger.jsonl")   # re-open = resume
    assert led2.state("d0") == "clean"
    assert led2.state("d1") == "pending"
    assert [e["design"] for e in led2.pending()] == ["d1"]


def test_state_transitions_are_legal_only(tmp_path):
    led = engineer_loop.Ledger(tmp_path / "ledger.jsonl")
    led.add(_entry("d0"))
    import pytest
    with pytest.raises(ValueError):
        led.set_state("d0", "bogus_state")


def test_process_one_clean_path(tmp_path, monkeypatch):
    """Flow pass + clean signoff -> state clean; ingest called once."""
    calls = []
    monkeypatch.setattr(engineer_loop, "_run_flow",
                        lambda e: calls.append(("flow", e["design"])) or 0)
    monkeypatch.setattr(engineer_loop, "_signoff_status",
                        lambda e: {"drc": "clean", "lvs": "clean"})
    monkeypatch.setattr(engineer_loop, "_ingest",
                        lambda e: calls.append(("ingest", e["design"])) or "rid")
    led = engineer_loop.Ledger(tmp_path / "ledger.jsonl")
    led.add(_entry("d0"))
    engineer_loop.process_one(led, led.pending()[0], conn=None)
    assert led.state("d0") == "clean"
    assert ("flow", "d0") in calls and ("ingest", "d0") in calls


def test_process_one_fix_path_then_escalate(tmp_path, monkeypatch):
    """Violations + fix loop fails to clear -> escalated, loop continues."""
    import knowledge_db
    conn = knowledge_db.connect(tmp_path / "knowledge.sqlite")
    knowledge_db.ensure_schema(conn)
    monkeypatch.setattr(engineer_loop, "_run_flow", lambda e: 0)
    monkeypatch.setattr(engineer_loop, "_signoff_status",
                        lambda e: {"drc": "fail", "lvs": "clean"})
    monkeypatch.setattr(engineer_loop, "_run_fix", lambda e: 2)   # residual
    monkeypatch.setattr(engineer_loop, "_ingest", lambda e: "rid")
    led = engineer_loop.Ledger(tmp_path / "ledger.jsonl")
    led.add(_entry("d0"))
    engineer_loop.process_one(led, led.pending()[0], conn=conn)
    assert led.state("d0") == "escalated"
    import escalations
    assert escalations.list_open(conn)[0]["reason"] == "catalog_exhausted"


def test_ab_launch_journaled(tmp_path, monkeypatch):
    """Tier B1: processing an ab_arm entry journals an ab_launch action carrying
    arm, strategy, and the trial's symptom_id (from the entry's ab_key)."""
    import journal_db
    jdb = tmp_path / "journal.sqlite"
    monkeypatch.setenv("R2G_JOURNAL_DB", str(jdb))
    monkeypatch.setattr(engineer_loop, "_run_flow", lambda e: 0)
    monkeypatch.setattr(engineer_loop, "_signoff_status",
                        lambda e: {"drc": "clean", "lvs": "clean"})
    monkeypatch.setattr(engineer_loop, "_ingest", lambda e: "rid")
    led = engineer_loop.Ledger(tmp_path / "ledger.jsonl")
    entry = {"design": "d0_abB_dens_0", "project_path": "/p/d0_abB_dens_0",
             "platform": "sky130hd", "kind": "ab_arm", "arm": "B",
             "strategy": "density_relief", "repeat": 0, "check": "both",
             "ab_key": {"symptom_id": "deadbeef00000001",
                        "design_class": "logic/small", "platform": "sky130hd",
                        "strategy": "density_relief"}}
    led.add(entry)
    engineer_loop.process_one(led, led.pending()[0], conn=None)
    jc = journal_db.connect(jdb)
    row = jc.execute("SELECT action_type, symptom_id, "
                     "json_extract(payload_json,'$.arm'), "
                     "json_extract(payload_json,'$.strategy') FROM actions "
                     "WHERE action_type='ab_launch'").fetchone()
    assert row is not None
    assert row[1] == "deadbeef00000001" and row[2] == "B"
    assert row[3] == "density_relief"


def test_escalate_journaled(tmp_path, monkeypatch):
    """Tier B3: open_escalation journals an escalate action (symptom-linked); the
    knowledge-side escalations row stays the source of truth."""
    import journal_db
    import knowledge_db
    import escalations
    jdb = tmp_path / "journal.sqlite"
    monkeypatch.setenv("R2G_JOURNAL_DB", str(jdb))
    conn = knowledge_db.connect(tmp_path / "knowledge.sqlite")
    knowledge_db.ensure_schema(conn)
    escalations.open_escalation(conn, design="d0", project_path="/p/d0",
                                run_id=None, reason="unseen_crash",
                                symptom_id="deadbeef00000001", notes="boom")
    # Dedup hit -> must NOT journal a second escalate.
    escalations.open_escalation(conn, design="d0", project_path="/p/d0",
                                run_id=None, reason="unseen_crash",
                                symptom_id="deadbeef00000001")
    jc = journal_db.connect(jdb)
    rows = jc.execute("SELECT action_type, symptom_id, "
                      "json_extract(payload_json,'$.reason') FROM actions").fetchall()
    assert len(rows) == 1
    assert rows[0] == ("escalate", "deadbeef00000001", "unseen_crash")


def test_learn_cycle_enqueues_candidates_and_ab_arms(tmp_path, monkeypatch):
    """After ingest, learn -> recipe diff -> A/B arms appended to the ledger."""
    import knowledge_db
    conn = knowledge_db.connect(tmp_path / "knowledge.sqlite")
    knowledge_db.ensure_schema(conn)
    conn.execute("INSERT OR REPLACE INTO runs (run_id, project_path, design_name,"
                 " platform, ingested_at, cell_count, design_class) "
                 "VALUES ('r0','/p/d0','d0','nangate45','t',900,'crypto/small')")
    conn.execute("INSERT OR REPLACE INTO run_violations (run_id, platform,"
                 " drc_status, symptom_id, snapshot_ts) "
                 "VALUES ('r0','nangate45','fail','deadbeef00000001','t')")
    conn.commit()
    heur_new = {"generation": 2, "recipes": {"deadbeef00000001": {
        "crypto/small": {"nangate45": {"strategies": {"s_new": {
            "attempts": 1, "successes": 1, "failures": 0, "wins": 0}},
            "n_sessions": 1}}}}}
    monkeypatch.setattr(engineer_loop, "_learn", lambda: heur_new)
    led = engineer_loop.Ledger(tmp_path / "ledger.jsonl")
    engineer_loop.learn_cycle(led, conn, prev_heur={"generation": 1,
                                                    "recipes": {}},
                              n_ab_designs=1)
    arms = [e for e in led.entries() if e["kind"] == "ab_arm"]
    # Win 2: each arm side is replicated R2G_AB_REPEATS times (default k=2), so one
    # matched design yields 2 arms × 2 repeats = 4 entries.
    assert len(arms) == 4
    assert {a["arm"] for a in arms} == {"A", "B"}
    assert {a["repeat"] for a in arms} == {0, 1}
