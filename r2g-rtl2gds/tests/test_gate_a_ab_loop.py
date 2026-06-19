"""Tier −1 Gate A: prove the A/B closed loop fires on the PRODUCTION path.

Diagnosis (2026-06-16, paper-absorption plan): the shadow→candidate→promoted
pipeline lives only inside engineer_loop.run, which never drove a production
campaign (no design_cases/_loop ledger). The batch driver calls
learn_heuristics.learn() directly, and learn() never enqueued candidates — so
recipe_status stayed empty and ab_trials=0 across 1267 runs. These tests pin the
fix: learn() now enqueues candidates, and a standalone ab_drain fires a trial
end-to-end without re-running normal designs.
"""
import json
from pathlib import Path

import engineer_loop
import knowledge_db
import learn_heuristics
import recipe_lifecycle


def _mk_project_with_fix(tmp_path: Path, name: str, *, sid: str) -> Path:
    """A failing design whose reports/fix_log.jsonl records a CLEARED antenna
    episode, so ingest stores a fix_event the learner can turn into a recipe, and
    run_violations carries the same symptom for A/B matching."""
    p = tmp_path / "designs" / name
    (p / "constraints").mkdir(parents=True)
    (p / "reports").mkdir()
    (p / "backend").mkdir()
    (p / "constraints" / "config.mk").write_text(
        f"export DESIGN_NAME = {name}\nexport PLATFORM = nangate45\n")
    (p / "reports" / "drc.json").write_text(json.dumps(
        {"status": "fail", "total_violations": 3,
         "categories": {"antenna": {"count": 3}}}))
    (p / "reports" / "lvs.json").write_text(json.dumps({"status": "clean"}))
    (p / "reports" / "ppa.json").write_text(json.dumps(
        {"summary": {}, "geometry": {"instance_count": 900}}))
    (p / "backend" / "stage_log.jsonl").write_text(
        '{"stage":"finish","status":0,"elapsed_s":10}\n')
    (p / "reports" / "fix_log.jsonl").write_text(json.dumps({
        "fix_session_id": f"s_{name}", "check": "drc",
        "violation_class": "antenna", "iter": 1,
        "strategy": "antenna_diode_repair", "before": 3, "after": 0,
        "verdict": "cleared"}) + "\n")
    return p


def _seed_two_designs(tmp_path, conn):
    import ingest_run
    import symptom
    sig = symptom.canonical_signature("drc", "antenna", None)
    sid = symptom.symptom_id(sig)
    for n in ("alpha_small", "beta_small"):
        ingest_run.ingest(_mk_project_with_fix(tmp_path, n, sid=sid), conn)
    conn.commit()
    return sid


# ── The gap fix: production learn() enqueues candidates ──────────────────────

def test_learn_enqueues_candidates_on_production_path(tmp_path, monkeypatch):
    """Calling learn() directly (the batch/production driver path) — NOT
    engineer_loop.run — must populate recipe_status with candidates. Before the
    fix this was a no-op, which is why ab_trials stayed at 0."""
    monkeypatch.setenv("R2G_JOURNAL_DB", str(tmp_path / "journal.sqlite"))
    db = tmp_path / "knowledge.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    _seed_two_designs(tmp_path, conn)
    conn.close()

    # No prior heuristics.json on disk -> every learned recipe is "new".
    out = tmp_path / "heuristics.json"
    learn_heuristics.learn(db, out)

    conn = knowledge_db.connect(db)
    cands = recipe_lifecycle.pending_candidates(conn)
    assert cands, "production learn() must enqueue at least one candidate recipe"
    assert any(c["strategy"] == "antenna_diode_repair" for c in cands)


def test_learn_enqueue_is_opt_outable(tmp_path, monkeypatch):
    monkeypatch.setenv("R2G_JOURNAL_DB", str(tmp_path / "journal.sqlite"))
    db = tmp_path / "knowledge.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    _seed_two_designs(tmp_path, conn)
    conn.close()
    learn_heuristics.learn(db, tmp_path / "heuristics.json",
                           enqueue_candidates=False)
    conn = knowledge_db.connect(db)
    assert recipe_lifecycle.pending_candidates(conn) == []


# ── enqueue a grandfathered recipe for explicit re-validation ────────────────

KEY = dict(symptom_id="deadbeef00000001", design_class="crypto/small",
           platform="nangate45", strategy="antenna_diode_repair")


def test_enqueue_candidate_forces_grandfathered_recipe_into_lifecycle(tmp_path):
    conn = knowledge_db.connect(tmp_path / "knowledge.sqlite")
    knowledge_db.ensure_schema(conn)
    # Absent row == grandfathered == promoted.
    assert recipe_lifecycle.get_status(conn, **KEY) == "promoted"
    assert recipe_lifecycle.enqueue_candidate(conn, **KEY) is True
    assert recipe_lifecycle.get_status(conn, **KEY) == "candidate"
    # Idempotent: a second call does not re-enqueue / clobber.
    assert recipe_lifecycle.enqueue_candidate(conn, **KEY) is False


# ── the drain: fire a trial end-to-end without re-running normal designs ──────

def _fake_arm_scripts(tmp_path):
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
        "  echo '{\"status\":\"clean\",\"total_violations\":0}' > \"$proj/reports/drc.json\"\n"
        "  exit 0\n"
        "elif [ -n \"${R2G_FIX_EXCLUDE:-}\" ]; then\n"
        "  exit 2\n"
        "else\n"
        "  exit 0\n"
        "fi\n")
    for f in (flow, fix):
        f.chmod(0o755)
    return {"R2G_LOOP_RUN_FLOW": str(flow), "R2G_LOOP_FIX": str(fix)}


def test_plan_trial_falls_back_to_recipe_evidence_designs(tmp_path, monkeypatch):
    """Second live-fire blocker (2026-06-16): plan_trial picked A/B subjects only
    from run_violations, which is a POST-fix snapshot — so a successfully-FIXED
    symptom (antenna) has no rows there and the winning recipe could never be
    A/B'd. plan_trial must fall back to the recipe's evidence_designs (the pre-fix
    exhibitors the learner recorded), resolved to on-disk project dirs."""
    import ab_runner
    db = tmp_path / "knowledge.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    sid = "abc1230000000001"
    # Two designs with on-disk dirs and runs rows, but NO run_violations for sid.
    names = []
    for nm, cells in (("ev_small", 120), ("ev_big", 800)):
        d = tmp_path / "designs" / nm
        (d / "constraints").mkdir(parents=True)
        conn.execute(
            "INSERT INTO runs (run_id, project_path, design_name, platform, "
            "design_class, cell_count, orfs_status, ingested_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (f"r_{nm}", str(d), nm, "nangate45", "logic/small", cells, "pass",
             "2026-06-16T00:00:00Z"))
        names.append(nm)
    conn.commit()
    heur = tmp_path / "heuristics.json"
    heur.write_text(json.dumps({"symptoms": {sid: {
        "check": "drc", "class": "antenna", "strategies": {"antenna_diode_repair": {}},
        "evidence_designs": names}}}))
    monkeypatch.setattr(ab_runner, "HEUR_PATH", str(heur))

    trial = ab_runner.plan_trial(conn, symptom_id=sid, design_class="logic/small",
                                 platform="nangate45",
                                 strategy="antenna_diode_repair", n_designs=2)
    conn.close()
    assert trial is not None, "evidence fallback must find subjects when run_violations is empty"
    assert trial["match_level"].startswith("evidence")
    # cheapest-first ordering preserved
    assert [d["design_name"] for d in trial["designs"]] == ["ev_small", "ev_big"]


def test_ab_drain_fires_trial_and_transitions_recipe(tmp_path, monkeypatch):
    """ab_drain plans + runs + judges trials for pending candidates WITHOUT
    re-running the normal designs. Exit criterion (Gate A): ab_trials gains a row
    and the recipe transitions candidate -> promoted (or -> shadow)."""
    import ingest_run
    monkeypatch.setenv("R2G_JOURNAL_DB", str(tmp_path / "journal.sqlite"))
    for k, v in _fake_arm_scripts(tmp_path).items():
        monkeypatch.setenv(k, v)
    db = tmp_path / "knowledge.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    _seed_two_designs(tmp_path, conn)
    conn.close()
    # Production learner enqueues the candidate.
    learn_heuristics.learn(db, tmp_path / "heuristics.json")

    monkeypatch.setattr(engineer_loop, "_ingest",
                        lambda e: ingest_run.ingest(Path(e["project_path"]),
                                                    knowledge_db.connect(db)))
    led_path = tmp_path / "ledger.jsonl"
    engineer_loop.ab_drain(led_path, n_ab_designs=1, db_path=db)

    conn = knowledge_db.connect(db)
    trials = conn.execute("SELECT trial_id, verdict, strategy FROM ab_trials").fetchall()
    assert trials, "Gate A exit criterion: ab_trials must gain >=1 row"
    trial_id, verdict, strategy = trials[0]
    assert verdict in ("win", "loss", "inconclusive")
    # A recipe transitioned out of 'candidate' (promoted on win, shadow otherwise).
    status = conn.execute(
        "SELECT status FROM recipe_status WHERE strategy=?", (strategy,)).fetchone()[0]
    assert status in ("promoted", "shadow")
