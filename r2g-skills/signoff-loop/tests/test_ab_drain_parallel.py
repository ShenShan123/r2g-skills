"""ab_drain runs arm flows CONCURRENTLY (R2G_AB_WORKERS) without corrupting the
ledger or the verdict — the arms are independent ORFS flows, so the multi-core
host should run them in parallel (2026-06-17, user-requested speedup).
"""
import json
import threading
from pathlib import Path

import engineer_loop
import knowledge_db
import learn_heuristics


def test_ledger_is_thread_safe(tmp_path):
    led = engineer_loop.Ledger(tmp_path / "l.jsonl")
    names = [f"d{i}" for i in range(40)]
    for n in names:
        led.add({"design": n, "project_path": f"/x/{n}", "platform": "sky130hd"})

    def flip(n):
        led.set_state(n, "flow")
        led.set_state(n, "clean")

    threads = [threading.Thread(target=flip, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # every design ends 'clean'; the JSONL has no torn/half lines
    assert all(led.state(n) == "clean" for n in names)
    lines = [l for l in (tmp_path / "l.jsonl").read_text().splitlines() if l.strip()]
    for l in lines:
        json.loads(l)            # raises if any append interleaved


def _mk_route_project(tmp_path: Path, name: str) -> Path:
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
    stages = [{"stage": s, "status": 0} for s in ("synth", "floorplan", "place", "cts")]
    stages.append({"stage": "route", "status": 124})
    (run / "stage_log.jsonl").write_text("\n".join(json.dumps(s) for s in stages) + "\n")
    (p / "reports" / "fix_log.jsonl").write_text(json.dumps({
        "fix_session_id": f"s_{name}", "check": "orfs_stage",
        "violation_class": "route", "iter": 1, "strategy": "route_relief",
        "before": 5247, "after": 0, "verdict": "cleared"}) + "\n")
    return p


def _fake_route_flow(tmp_path):
    flow = tmp_path / "fake_route_flow.sh"
    flow.write_text(
        "#!/bin/bash\nproj=\"$1\"\nrun=\"$proj/backend/RUN_x\"\nmkdir -p \"$run/reports_orfs\"\n"
        "head=' {\"stage\":\"synth\",\"status\":0}\\n{\"stage\":\"floorplan\",\"status\":0}"
        "\\n{\"stage\":\"place\",\"status\":0}\\n{\"stage\":\"cts\",\"status\":0}'\n"
        "if grep -q 'r2g signoff-fix' \"$proj/constraints/config.mk\" 2>/dev/null; then\n"
        "  printf '%b\\n{\"stage\":\"route\",\"status\":0}\\n{\"stage\":\"finish\",\"status\":0}\\n' \"$head\" > \"$run/stage_log.jsonl\"\n"
        "  : > \"$run/reports_orfs/5_route_drc.rpt\"; exit 0\n"
        "else\n"
        "  printf '%b\\n{\"stage\":\"route\",\"status\":124}\\n' \"$head\" > \"$run/stage_log.jsonl\"; exit 124\nfi\n")
    flow.chmod(0o755)
    return str(flow)


def test_parallel_ab_drain_records_route_verdict(tmp_path, monkeypatch):
    import ingest_run
    monkeypatch.setenv("R2G_JOURNAL_DB", str(tmp_path / "journal.sqlite"))
    monkeypatch.setenv("R2G_LOOP_RUN_FLOW", _fake_route_flow(tmp_path))
    db = tmp_path / "knowledge.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    for n in ("crypto_a", "crypto_b"):
        ingest_run.ingest(_mk_route_project(tmp_path, n), conn)
    conn.commit(); conn.close()
    learn_heuristics.learn(db, tmp_path / "heuristics.json")

    monkeypatch.setattr(engineer_loop, "_ingest",
                        lambda e: ingest_run.ingest(Path(e["project_path"]),
                                                    knowledge_db.connect(db)))
    # workers=4 -> the (2-design x 2-arm) trial runs its 4 arms concurrently.
    engineer_loop.ab_drain(tmp_path / "ledger.jsonl", n_ab_designs=2, db_path=db,
                           max_workers=4)
    conn = knowledge_db.connect(db)
    trials = conn.execute(
        "SELECT verdict FROM ab_trials WHERE strategy='route_relief'").fetchall()
    assert trials, "parallel drain must still record the route ab_trials row"
    assert trials[0][0] == "win"
    conn.close()
