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


def test_run_flow_invalidates_stale_signoff_reports(tmp_path, monkeypatch):
    """A re-flow MUST delete stale project-local signoff verdicts so the first-pass clean gate
    cannot _mark_clean from a PRIOR platform's reports -- the 2026-06-30 fabricated-clean bug
    (a /r2g-debug asap7 re-target inherited June-19 nangate45 reports/{drc,lvs}.json=clean and
    19 designs were marked clean WITHOUT running fresh asap7 signoff). _run_flow is the upstream
    chokepoint: after it deletes them, _signoff_status returns unknown and the gate falls
    through to _run_fix -> fix_signoff._ensure_baseline (fresh platform-correct signoff)."""
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    stale = ("drc", "lvs", "rcx", "route", "timing_check")
    for chk in stale:
        (proj / "reports" / f"{chk}.json").write_text('{"status": "clean"}', encoding="utf-8")

    class _R:                      # minimal CompletedProcess stand-in (no real ORFS flow)
        returncode = 0
    monkeypatch.setattr(engineer_loop.subprocess, "run", lambda *a, **k: _R())

    rc = engineer_loop._run_flow({"project_path": str(proj), "platform": "asap7"})
    assert rc == 0
    for chk in stale:
        assert not (proj / "reports" / f"{chk}.json").exists(), \
            f"{chk}.json was NOT invalidated before re-flow (stale-clean short-circuit risk)"


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
    # Subject dir must exist on disk: plan_trial Tier 1 isdir-filters subjects
    # since 2026-07-03 (wiped-round ghost dirs must never become arms).
    subj = tmp_path / "d0"
    subj.mkdir()
    conn.execute("INSERT OR REPLACE INTO runs (run_id, project_path, design_name,"
                 " platform, ingested_at, cell_count, design_class) "
                 "VALUES ('r0',?,'d0','nangate45','t',900,'crypto/small')",
                 (str(subj),))
    conn.execute("INSERT OR REPLACE INTO run_violations (run_id, platform,"
                 " drc_status, symptom_id, snapshot_ts) "
                 "VALUES ('r0','nangate45','fail','deadbeef00000001','t')")
    conn.commit()
    # Use a REAL catalog strategy: a genuine learned candidate always derives from a
    # fix_event of an applyable strategy, so it is never parked by the P0-6 no-op guard
    # (a synthetic never-applied id like "s_new" WOULD be parked, correctly).
    heur_new = {"generation": 2, "recipes": {"deadbeef00000001": {
        "crypto/small": {"nangate45": {"strategies": {"density_relief": {
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


def test_reclaim_orphans_resets_transient_states(tmp_path):
    """A crashed/killed driver (host reboot mid-wave) strands designs in a TRANSIENT
    state (flow/signoff/fixing). run() drains only 'pending' and the waves driver's
    ALL_DONE gate counted only 'pending', so orphans were stranded FOREVER and the
    round could terminate with non-terminal designs (failure-patterns.md #31 —
    2026-07-09 sky130hs reboot left 8 'flow' orphans). reclaim_orphans is safe at
    command start because the per-ledger single-instance guard (flock + pgrep,
    2026-07-04) means no live worker can own a transient state then."""
    led = engineer_loop.Ledger(tmp_path / "ledger.jsonl")
    for name, state in (("d_flow", "flow"), ("d_signoff", "signoff"),
                        ("d_fixing", "fixing"), ("d_clean", "clean"),
                        ("d_esc", "escalated")):
        led.add(_entry(name))
        led.set_state(name, state)
    led.add(_entry("d_pending"))
    led.add(_entry("d_arm", kind="ab_arm"))
    led.set_state("d_arm", "flow", judged=True)

    reclaimed = led.reclaim_orphans()

    assert sorted(reclaimed) == ["d_arm", "d_fixing", "d_flow", "d_signoff"]
    for d in ("d_flow", "d_signoff", "d_fixing", "d_arm"):
        assert led.state(d) == "pending"
    # terminal + already-pending entries untouched
    assert led.state("d_clean") == "clean"
    assert led.state("d_esc") == "escalated"
    assert led.state("d_pending") == "pending"
    # mirrors the add()/reload pending invariant: stale 'judged' drops so a
    # re-run arm is RE-judged, not skipped
    assert "judged" not in led.get("d_arm")
    # persisted through the JSONL: a re-opened ledger agrees
    led2 = engineer_loop.Ledger(tmp_path / "ledger.jsonl")
    assert led2.state("d_flow") == "pending"
    assert "judged" not in led2.get("d_arm")
    # idempotent: second call is a no-op
    assert led.reclaim_orphans() == []


def test_run_drains_crash_orphaned_designs(tmp_path, monkeypatch):
    """run() must reclaim crash-orphaned transient designs into the drain, not
    silently skip them (failure-patterns.md #31)."""
    import knowledge_db
    import recipe_lifecycle
    monkeypatch.setattr(knowledge_db, "DEFAULT_DB_PATH",
                        tmp_path / "knowledge.sqlite")
    processed = []
    monkeypatch.setattr(engineer_loop, "_safe_process",
                        lambda led, e: processed.append(e["design"]))
    monkeypatch.setattr(engineer_loop, "_learn", lambda: {})
    monkeypatch.setattr(recipe_lifecycle, "diff_and_enqueue",
                        lambda *a, **k: None)
    monkeypatch.setattr(engineer_loop, "plan_arms_for_candidates",
                        lambda *a, **k: None)
    monkeypatch.setattr(engineer_loop, "judge_finished_trials",
                        lambda *a, **k: None)
    led = engineer_loop.Ledger(tmp_path / "ledger.jsonl")
    led.add(_entry("d_orphan"))
    led.set_state("d_orphan", "flow")          # crash left it here
    led.add(_entry("d_normal"))
    engineer_loop.run(tmp_path / "ledger.jsonl", max_workers=2)
    assert sorted(processed) == ["d_normal", "d_orphan"]
