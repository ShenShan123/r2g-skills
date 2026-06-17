"""A backend route abort must be keyed under the orfs_stage/route symptom — and
that symptom_id must EQUAL the one a route fix_log row produces, or the A/B loop
can never match a route-congestion run to its route_relief recipe.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import ingest_run
import knowledge_db
import symptom


def _route_abort_project(tmp_path: Path) -> Path:
    proj = tmp_path / "ra"
    (proj / "constraints").mkdir(parents=True)
    (proj / "reports").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = ra\nexport PLATFORM = sky130hd\n"
        "export CORE_UTILIZATION = 25\n")
    (proj / "reports" / "ppa.json").write_text(json.dumps({"summary": {}, "geometry": {}}))
    # backend stage_log: route killed by the wall-clock timeout (124) -> fail@route
    run = proj / "backend" / "RUN_2026-06-17_00-00-00"
    run.mkdir(parents=True)
    stages = [{"stage": s, "status": 0, "elapsed_s": 1}
              for s in ("synth", "floorplan", "place", "cts")]
    stages.append({"stage": "route", "status": 124, "elapsed_s": 5400})
    (run / "stage_log.jsonl").write_text(
        "\n".join(json.dumps(s) for s in stages) + "\n")
    return proj


def test_route_abort_gets_orfs_stage_symptom(tmp_path, tmp_knowledge_dir):
    proj = _route_abort_project(tmp_path)
    conn = knowledge_db.connect(tmp_knowledge_dir / "knowledge.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    ingest_run.ingest(proj, conn, families_path=tmp_knowledge_dir / "families.json")

    # The run was a route abort...
    orfs_status, fail_stage = conn.execute(
        "SELECT orfs_status, orfs_fail_stage FROM runs").fetchone()
    assert orfs_status == "fail"
    assert fail_stage == "route"

    # ...and its run_violations symptom keys under orfs_stage/route (NOT timing).
    sid, sig_json = conn.execute(
        "SELECT symptom_id, signature_json FROM run_violations").fetchone()
    sig = json.loads(sig_json)
    assert sig["check"] == "orfs_stage"
    assert sig["class"] == "route"

    # CRUX: it equals the symptom a route fix_log row produces — so plan_trial can
    # match the failing run to the route_relief recipe learned from the fix.
    fix_sig, fix_sid = symptom.from_fix_log_row(
        {"check": "orfs_stage", "violation_class": "route"})
    assert fix_sid == sid
    conn.close()
