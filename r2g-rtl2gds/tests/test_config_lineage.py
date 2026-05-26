"""Tests for config lineage tracking."""
from __future__ import annotations

import json

import ingest_run
import knowledge_db


def _open_db(tmp_knowledge_dir):
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    return conn


def _make_project(tmp_path, name, config_overrides=None):
    """Create a minimal project directory with config.mk and all required artifacts."""
    project = tmp_path / name
    (project / "constraints").mkdir(parents=True)
    (project / "reports").mkdir(parents=True)
    (project / "backend").mkdir(parents=True)

    config_lines = [
        "export DESIGN_NAME = aes128_core",
        "export PLATFORM = nangate45",
        "export CORE_UTILIZATION = 30",
        "export PLACE_DENSITY_LB_ADDON = 0.20",
    ]
    if config_overrides:
        for key, val in config_overrides.items():
            config_lines = [
                l for l in config_lines if not l.strip().startswith(f"export {key}")
            ]
            config_lines.append(f"export {key} = {val}")
    (project / "constraints" / "config.mk").write_text("\n".join(config_lines) + "\n")

    (project / "reports" / "ppa.json").write_text(json.dumps({
        "summary": {"timing": {"setup_wns": 0.1, "setup_tns": 0.0},
                     "power": {"total_power_w": 0.01},
                     "area": {"design_area_um2": 5000.0}},
        "geometry": {"die_area_um2": 5000.0, "instance_count": 12000},
    }))
    (project / "reports" / "drc.json").write_text(
        json.dumps({"status": "clean", "total_violations": 0}))
    (project / "reports" / "lvs.json").write_text(
        json.dumps({"status": "clean"}))
    (project / "reports" / "rcx.json").write_text(
        json.dumps({"status": "complete"}))
    (project / "reports" / "timing_check.json").write_text(
        json.dumps({"tier": "clean"}))
    (project / "reports" / "diagnosis.json").write_text(
        json.dumps({"issues": []}))

    stages = [
        {"stage": "synth", "status": "pass", "elapsed_s": 60},
        {"stage": "floorplan", "status": "pass", "elapsed_s": 30},
        {"stage": "place", "status": "pass", "elapsed_s": 120},
        {"stage": "cts", "status": "pass", "elapsed_s": 90},
        {"stage": "route", "status": "pass", "elapsed_s": 200},
        {"stage": "finish", "status": "pass", "elapsed_s": 50},
    ]
    (project / "backend" / "stage_log.jsonl").write_text(
        "\n".join(json.dumps(s) for s in stages) + "\n")

    return project


def test_config_lineage_table_exists(tmp_knowledge_dir):
    conn = _open_db(tmp_knowledge_dir)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    assert "config_lineage" in tables
    conn.close()


def test_diff_config_rows_detects_changes():
    old = {"CORE_UTILIZATION": "40", "PLACE_DENSITY_LB_ADDON": "0.20",
           "SYNTH_HIERARCHICAL": "1"}
    new = {"CORE_UTILIZATION": "25", "PLACE_DENSITY_LB_ADDON": "0.20",
           "SKIP_CTS_REPAIR_TIMING": "1"}
    diff = knowledge_db.diff_config_rows(old, new)
    assert diff["changed"] == {"CORE_UTILIZATION": {"old": "40", "new": "25"}}
    assert diff["added"] == {"SKIP_CTS_REPAIR_TIMING": "1"}
    assert diff["removed"] == {"SYNTH_HIERARCHICAL": "1"}


def test_diff_config_rows_empty_when_identical():
    cfg = {"CORE_UTILIZATION": "30", "PLACE_DENSITY_LB_ADDON": "0.20"}
    diff = knowledge_db.diff_config_rows(cfg, cfg)
    assert diff["changed"] == {}
    assert diff["added"] == {}
    assert diff["removed"] == {}


def test_lineage_recorded_when_config_changes(tmp_knowledge_dir, tmp_path):
    """Changing CORE_UTILIZATION between runs should produce a lineage row."""
    conn = _open_db(tmp_knowledge_dir)

    proj_v1 = _make_project(tmp_path, "run_v1")
    run_id_1 = ingest_run.ingest(proj_v1, conn,
                                  families_path=tmp_knowledge_dir / "families.json")

    proj_v2 = _make_project(tmp_path, "run_v2",
                             config_overrides={"CORE_UTILIZATION": "25"})
    run_id_2 = ingest_run.ingest(proj_v2, conn,
                                  families_path=tmp_knowledge_dir / "families.json")

    rows = conn.execute(
        "SELECT current_run_id, previous_run_id, diff_json, current_outcome "
        "FROM config_lineage WHERE design_name = 'aes128_core'"
    ).fetchall()
    assert len(rows) == 1
    cur_id, prev_id, diff_str, outcome = rows[0]
    assert cur_id == run_id_2
    assert prev_id == run_id_1
    diff = json.loads(diff_str)
    assert "CORE_UTILIZATION" in diff["changed"]
    assert diff["changed"]["CORE_UTILIZATION"]["old"] == "30"
    assert diff["changed"]["CORE_UTILIZATION"]["new"] == "25"
    assert outcome == "pass"
    conn.close()


def test_no_lineage_for_first_run(tmp_knowledge_dir, tmp_path):
    """First run of a design/platform has no previous run — no lineage row."""
    conn = _open_db(tmp_knowledge_dir)
    proj = _make_project(tmp_path, "first_run")
    ingest_run.ingest(proj, conn,
                       families_path=tmp_knowledge_dir / "families.json")
    count = conn.execute("SELECT COUNT(*) FROM config_lineage").fetchone()[0]
    assert count == 0
    conn.close()


def test_no_lineage_when_config_unchanged(tmp_knowledge_dir, tmp_path):
    """Identical config between runs should not produce a lineage row."""
    conn = _open_db(tmp_knowledge_dir)
    proj_v1 = _make_project(tmp_path, "same_v1")
    ingest_run.ingest(proj_v1, conn,
                       families_path=tmp_knowledge_dir / "families.json")
    proj_v2 = _make_project(tmp_path, "same_v2")
    ingest_run.ingest(proj_v2, conn,
                       families_path=tmp_knowledge_dir / "families.json")
    count = conn.execute("SELECT COUNT(*) FROM config_lineage").fetchone()[0]
    assert count == 0
    conn.close()
