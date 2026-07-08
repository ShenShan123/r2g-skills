"""Route-congestion A/B closed loop fires end-to-end (2026-06-17 route-relief).

The Gate A/B work wired the A/B loop for *signoff* (DRC) symptoms. A route-stage
abort never reaches signoff, so it was structurally invisible. This pins the
backend-abort wiring: a route-abort run carries an orfs_stage/route symptom, a
route_relief fix_log makes the learner enqueue a candidate, and ab_drain fires a
trial through the dedicated apply-then-flow arm runner where the route-relief'd
arm routes (orfs pass) and the control times out (orfs fail) -> win.
"""
import json
from pathlib import Path

import engineer_loop
import knowledge_db
import learn_heuristics
import recipe_lifecycle


def _mk_route_project(tmp_path: Path, name: str) -> Path:
    """A sky130hd design that aborted at route (timeout) and whose fix_log records
    a CLEARED route_relief episode -> ingest stores the route symptom + a fix_event
    the learner turns into the route_relief recipe."""
    p = tmp_path / "designs" / name
    (p / "constraints").mkdir(parents=True)
    (p / "reports").mkdir()
    run = p / "backend" / "RUN_2026-06-17_00-00-00"
    run.mkdir(parents=True)
    (p / "constraints" / "config.mk").write_text(
        f"export DESIGN_NAME = {name}\nexport PLATFORM = sky130hd\n"
        "export CORE_UTILIZATION = 25\n")
    (p / "reports" / "ppa.json").write_text(json.dumps(
        {"summary": {}, "geometry": {"instance_count": 1200}}))
    # stage_log: route killed by the wall-clock timeout (124) -> fail@route
    stages = [{"stage": s, "status": 0} for s in ("synth", "floorplan", "place", "cts")]
    stages.append({"stage": "route", "status": 124})
    (run / "stage_log.jsonl").write_text(
        "\n".join(json.dumps(s) for s in stages) + "\n")
    # fix_log: a cleared route_relief episode (check=orfs_stage/class=route)
    (p / "reports" / "fix_log.jsonl").write_text(json.dumps({
        "fix_session_id": f"s_{name}", "check": "orfs_stage",
        "violation_class": "route", "iter": 1, "strategy": "route_relief",
        "before": 5247, "after": 0, "verdict": "cleared",
        "config_delta": json.dumps({"CORE_UTILIZATION": "17"})}) + "\n")
    return p


def _seed(tmp_path, conn):
    import ingest_run
    for n in ("crypto_a", "crypto_b"):
        ingest_run.ingest(_mk_route_project(tmp_path, n), conn)
    conn.commit()


def test_route_abort_ingests_orfs_stage_symptom_and_learns_recipe(tmp_path, monkeypatch):
    monkeypatch.setenv("R2G_JOURNAL_DB", str(tmp_path / "journal.sqlite"))
    db = tmp_path / "knowledge.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    _seed(tmp_path, conn)
    # run_violations carries the route symptom (orfs_stage/route), NOT timing
    sig = conn.execute(
        "SELECT signature_json FROM run_violations LIMIT 1").fetchone()[0]
    assert json.loads(sig)["check"] == "orfs_stage"
    assert json.loads(sig)["class"] == "route"
    conn.close()

    learn_heuristics.learn(db, tmp_path / "heuristics.json")
    conn = knowledge_db.connect(db)
    cands = recipe_lifecycle.pending_candidates(conn)
    assert any(c["strategy"] == "route_relief" for c in cands), \
        "learner must enqueue a route_relief candidate from the route fix_log"
    conn.close()


def _fake_route_flow(tmp_path):
    """A fake run_orfs: the route-relief'd arm (auto-block lowered util) routes
    clean (orfs pass); the control arm times out at route (orfs fail)."""
    flow = tmp_path / "fake_route_flow.sh"
    flow.write_text(
        "#!/bin/bash\n"
        "proj=\"$1\"\n"
        "run=\"$proj/backend/RUN_x\"\n"
        "mkdir -p \"$run/reports_orfs\"\n"
        "head=' {\"stage\":\"synth\",\"status\":0}\\n{\"stage\":\"floorplan\",\"status\":0}"
        "\\n{\"stage\":\"place\",\"status\":0}\\n{\"stage\":\"cts\",\"status\":0}'\n"
        "if grep -q 'r2g signoff-fix' \"$proj/constraints/config.mk\" 2>/dev/null; then\n"
        "  printf '%b\\n{\"stage\":\"route\",\"status\":0}\\n{\"stage\":\"finish\",\"status\":0}\\n' \"$head\" > \"$run/stage_log.jsonl\"\n"
        "  : > \"$run/reports_orfs/5_route_drc.rpt\"\n"
        "  exit 0\n"
        "else\n"
        "  printf '%b\\n{\"stage\":\"route\",\"status\":124}\\n' \"$head\" > \"$run/stage_log.jsonl\"\n"
        "  exit 124\n"
        "fi\n")
    flow.chmod(0o755)
    return str(flow)


def test_route_ab_drain_fires_trial_and_transitions_recipe(tmp_path, monkeypatch):
    """ab_drain plans + runs + judges a ROUTE trial: the route_relief arm routes,
    the control times out -> ab_trials gains a row and route_relief transitions out
    of 'candidate'. The whole reason the route-congestion campaign can 'learn'."""
    import ingest_run
    monkeypatch.setenv("R2G_JOURNAL_DB", str(tmp_path / "journal.sqlite"))
    monkeypatch.setenv("R2G_LOOP_RUN_FLOW", _fake_route_flow(tmp_path))
    db = tmp_path / "knowledge.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    _seed(tmp_path, conn)
    conn.close()
    learn_heuristics.learn(db, tmp_path / "heuristics.json")

    monkeypatch.setattr(engineer_loop, "_ingest",
                        lambda e: ingest_run.ingest(Path(e["project_path"]),
                                                    knowledge_db.connect(db)))
    led_path = tmp_path / "ledger.jsonl"
    engineer_loop.ab_drain(led_path, n_ab_designs=1, db_path=db)

    conn = knowledge_db.connect(db)
    trials = conn.execute(
        "SELECT verdict, strategy FROM ab_trials WHERE strategy='route_relief'").fetchall()
    assert trials, "route A/B trial must record an ab_trials row"
    verdict, strategy = trials[0]
    assert verdict == "win", f"route_relief should WIN (arm B routes, control times out); got {verdict}"
    status = conn.execute(
        "SELECT status FROM recipe_status WHERE strategy='route_relief'").fetchone()[0]
    assert status in ("promoted", "shadow")
    conn.close()
