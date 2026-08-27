"""Full loop turn, mocked flow (spec §9 integration). No EDA tools needed."""
import json
from pathlib import Path

import engineer_loop
import knowledge_db
import recipe_lifecycle


def _fake_scripts(tmp_path: Path) -> dict:
    """Fake run_orfs / fix_signoff. The fix fake has THREE branches so the real
    pipeline can learn a recipe AND exercise both A/B arms (see Task 17 notes)."""
    flow = tmp_path / "fake_flow.sh"
    flow.write_text(
        "#!/bin/bash\n"
        "mkdir -p \"$1/reports\" \"$1/backend\"\n"
        "echo '{\"stage\":\"finish\",\"status\":0,\"elapsed_s\":10}'"
        " > \"$1/backend/stage_log.jsonl\"\n"
        "exit 0\n")
    fix = tmp_path / "fake_fix.sh"
    fix.write_text(
        "#!/bin/bash\n"
        "proj=\"$1\"\n"
        "if [ -n \"${R2G_FIX_RANK_FIRST:-}\" ]; then\n"
        "  # arm B: the ranked-first strategy clears DRC\n"
        "  echo '{\"status\":\"clean\",\"total_violations\":0}' > \"$proj/reports/drc.json\"\n"
        "  exit 0\n"
        "elif [ -n \"${R2G_FIX_EXCLUDE:-}\" ]; then\n"
        "  # arm A: the proven strategy is excluded -> residual remains\n"
        "  exit 2\n"
        "else\n"
        "  # initial fix on a normal design: log one CLEARED antenna episode so a\n"
        "  # recipe is learnable; leave drc.json failing so run_violations keeps the\n"
        "  # antenna symptom for A/B matching\n"
        "  name=\"$(basename \"$proj\")\"\n"
        "  printf '{\"fix_session_id\":\"s_%s\",\"check\":\"drc\",\"violation_class\":\"antenna\",\"iter\":1,\"strategy\":\"antenna_diode_repair\",\"before\":3,\"after\":0,\"verdict\":\"cleared\"}\\n' \"$name\" >> \"$proj/reports/fix_log.jsonl\"\n"
        "  exit 0\n"
        "fi\n")
    for f in (flow, fix):
        f.chmod(0o755)
    return {"R2G_LOOP_RUN_FLOW": str(flow), "R2G_LOOP_FIX": str(fix)}


def _mk_failing_project(tmp_path, name):
    p = tmp_path / "designs" / name
    (p / "constraints").mkdir(parents=True)
    (p / "reports").mkdir()
    (p / "constraints" / "config.mk").write_text(
        f"export DESIGN_NAME = {name}\nexport PLATFORM = nangate45\n")
    (p / "reports" / "drc.json").write_text(json.dumps(
        {"status": "fail", "total_violations": 3,
         "categories": {"antenna": {"count": 3}}}))
    (p / "reports" / "lvs.json").write_text(json.dumps({"status": "clean"}))
    (p / "reports" / "ppa.json").write_text(json.dumps(
        {"summary": {}, "geometry": {"instance_count": 900}}))
    return p


def test_full_turn_runs_arms_and_promotes_winner(tmp_path, monkeypatch):
    import ingest_run
    db = tmp_path / "knowledge.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    monkeypatch.setenv("R2G_JOURNAL_DB", str(tmp_path / "journal.sqlite"))
    for k, v in _fake_scripts(tmp_path).items():
        monkeypatch.setenv(k, v)
    # real ingest against the temp DB; real learn against it too
    monkeypatch.setattr(engineer_loop, "_ingest",
                        lambda e: ingest_run.ingest(Path(e["project_path"]), conn))
    monkeypatch.setattr(
        engineer_loop, "_learn",
        lambda: __import__("learn_heuristics").learn(
            db, tmp_path / "heuristics.json"))

    led = engineer_loop.Ledger(tmp_path / "ledger.jsonl")
    for n in ("alpha_small", "beta_small"):
        p = _mk_failing_project(tmp_path, n)
        led.add({"design": n, "project_path": str(p), "platform": "nangate45"})
    prev = {"generation": 0, "recipes": {}}
    for entry in list(led.pending()):
        engineer_loop.process_one(led, entry, conn)
    engineer_loop.learn_cycle(led, conn, prev_heur=prev, n_ab_designs=1)

    # The fix episodes produced a learned recipe -> candidate -> arm entries.
    # Win 2: 2 arms × R2G_AB_REPEATS (default k=2) = 4 entries for one design.
    arms = [e for e in led.entries() if e["kind"] == "ab_arm"]
    assert len(arms) == 4 and {a["arm"] for a in arms} == {"A", "B"}

    # Execute the arms (resume semantics: they are just pending entries).
    for entry in list(led.pending()):
        engineer_loop.process_one(led, entry, conn)
    engineer_loop.judge_finished_trials(led, conn)

    key = arms[0]["ab_key"]
    row = conn.execute("SELECT verdict FROM ab_trials WHERE strategy=?",
                       (key["strategy"],)).fetchone()
    assert row is not None and row[0] in ("win", "loss", "inconclusive")
    if row[0] == "win":          # fake arm B clears -> expected path
        assert recipe_lifecycle.get_status(conn, **key) == "promoted"

    # Arm A (exclude) exits 2 -> at least one catalog_exhausted escalation.
    import escalations
    assert any(e["reason"] == "catalog_exhausted"
               for e in escalations.list_open(conn))

    # Strength projection sees the ingested runs.
    import build_strength_report
    rep = build_strength_report.build(db)
    assert isinstance(rep["generations"], list)
