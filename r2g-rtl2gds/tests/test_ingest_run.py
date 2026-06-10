"""Tests for ingest_run.py: read artifacts → SQLite row."""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import ingest_run
import knowledge_db


def _stage(fixtures_dir: Path, name: str, tmp_path: Path) -> Path:
    """Copy a fixture project into tmp_path so mtimes are fresh."""
    dst = tmp_path / name
    shutil.copytree(fixtures_dir / name, dst)
    return dst


def _open_db(tmp_knowledge_dir: Path) -> sqlite3.Connection:
    conn = knowledge_db.connect(tmp_knowledge_dir / "knowledge.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    return conn


def test_ingest_success_run_writes_row(fixtures_dir, tmp_knowledge_dir, tmp_path):
    project = _stage(fixtures_dir, "sample_run_success", tmp_path)
    conn = _open_db(tmp_knowledge_dir)

    run_id = ingest_run.ingest(project, conn,
                               families_path=tmp_knowledge_dir / "families.json")
    assert run_id

    row = conn.execute(
        "SELECT design_name, design_family, platform, orfs_status, "
        "core_utilization, place_density_lb_addon, cell_count, "
        "wns_ns, timing_tier, drc_status, lvs_status, rcx_status, "
        "total_elapsed_s "
        "FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    assert row is not None
    (design_name, design_family, platform, orfs_status, core_util, pdens,
     cell_count, wns, tier, drc, lvs, rcx, elapsed) = row
    assert design_name == "aes128_core"
    assert design_family == "aes_xcrypt"
    assert platform == "nangate45"
    assert orfs_status == "pass"
    assert core_util == 25.0
    assert abs(pdens - 0.20) < 1e-9
    assert cell_count == 12412
    assert abs(wns - (-0.05)) < 1e-9
    assert tier == "minor"
    # Status values come straight from extract_{drc,lvs,rcx}.py, which use
    # 'clean' for DRC/LVS success and 'complete' for RCX success.
    assert drc == "clean"
    assert lvs == "clean"
    assert rcx == "complete"
    assert elapsed and elapsed > 800.0  # sum of stage times
    conn.close()


def test_ingest_failure_run_writes_row_and_failure_event(
    fixtures_dir, tmp_knowledge_dir, tmp_path,
):
    project = _stage(fixtures_dir, "sample_run_fail_pdn", tmp_path)
    conn = _open_db(tmp_knowledge_dir)

    run_id = ingest_run.ingest(project, conn,
                               families_path=tmp_knowledge_dir / "families.json")

    row = conn.execute(
        "SELECT orfs_status, orfs_fail_stage, design_family, cell_count, "
        "drc_status, lvs_status, rcx_status "
        "FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    orfs_status, fail_stage, fam, cell_count, drc, lvs, rcx = row
    assert orfs_status == "fail"
    assert fail_stage == "floorplan"
    assert fam == "bp_multi_top"
    assert cell_count == 198432
    # Signoff stages never ran
    assert drc in (None, "skipped")
    assert lvs in (None, "skipped")
    assert rcx in (None, "skipped")

    events = conn.execute(
        "SELECT stage, signature FROM failure_events WHERE run_id = ? ORDER BY signature",
        (run_id,),
    ).fetchall()
    assert ("floorplan", "pdn-0179") in events
    conn.close()


def test_ingest_is_idempotent(fixtures_dir, tmp_knowledge_dir, tmp_path):
    project = _stage(fixtures_dir, "sample_run_success", tmp_path)
    conn = _open_db(tmp_knowledge_dir)
    id1 = ingest_run.ingest(project, conn,
                            families_path=tmp_knowledge_dir / "families.json")
    id2 = ingest_run.ingest(project, conn,
                            families_path=tmp_knowledge_dir / "families.json")
    assert id1 == id2
    (count,) = conn.execute("SELECT COUNT(*) FROM runs").fetchone()
    assert count == 1
    conn.close()


def _mk_lineage_project(tmp_path, name, cu="20", drc="clean", subdir=None):
    base = tmp_path / (subdir or name)
    (base / "constraints").mkdir(parents=True, exist_ok=True)
    (base / "reports").mkdir(exist_ok=True)
    (base / "constraints" / "config.mk").write_text(
        f"export DESIGN_NAME = {name}\nexport PLATFORM = nangate45\n"
        f"export CORE_UTILIZATION = {cu}\n")
    (base / "reports" / "ppa.json").write_text(json.dumps({"summary": {}, "geometry": {}}))
    (base / "reports" / "drc.json").write_text(
        json.dumps({"status": drc, "total_violations": 0, "categories": {}}))
    (base / "reports" / "lvs.json").write_text(json.dumps({"status": "clean"}))
    return base


def test_lineage_outcome_is_structured(tmp_path, tmp_knowledge_dir):
    conn = knowledge_db.connect(tmp_knowledge_dir / "knowledge.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    fam = tmp_knowledge_dir / "families.json"
    p1 = _mk_lineage_project(tmp_path, "d1", cu="20", drc="clean")
    ingest_run.ingest(p1, conn, families_path=fam)
    p2 = _mk_lineage_project(tmp_path, "d1", cu="25", drc="clean", subdir="run2")
    ingest_run.ingest(p2, conn, families_path=fam)
    row = conn.execute("SELECT current_outcome FROM config_lineage").fetchone()
    assert row is not None
    outcome = json.loads(row[0])
    assert set(outcome) >= {"is_success", "wns_ns", "drc_violations", "total_elapsed_s"}
    assert outcome["is_success"] is True   # clean DRC -> relaxed success
    # idempotent: re-ingest must NOT add a second lineage row
    ingest_run.ingest(p2, conn, families_path=fam)
    assert conn.execute("SELECT COUNT(*) FROM config_lineage").fetchone()[0] == 1
    conn.close()


def test_run_violations_get_symptom(tmp_path, tmp_knowledge_dir):
    proj = tmp_path / "rv"
    (proj / "constraints").mkdir(parents=True); (proj / "reports").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = rv\nexport PLATFORM = nangate45\n")
    (proj / "reports" / "ppa.json").write_text(json.dumps({"summary": {}, "geometry": {}}))
    (proj / "reports" / "lvs.json").write_text(json.dumps({
        "status": "fail", "mismatch_class": "symmetric_matcher",
        "net_mismatches_schematic_only": 2, "net_mismatches_layout_only": 2,
        "device_mismatches": 0}))
    (proj / "reports" / "drc.json").write_text(json.dumps(
        {"status": "clean", "total_violations": 0, "categories": {}}))
    conn = knowledge_db.connect(tmp_knowledge_dir / "knowledge.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    ingest_run.ingest(proj, conn, families_path=tmp_knowledge_dir / "families.json")
    sid, sig = conn.execute(
        "SELECT symptom_id, signature_json FROM run_violations").fetchone()
    assert sid and len(sid) == 16
    assert json.loads(sig)["class"] == "symmetric_matcher"
    assert json.loads(sig)["predicates"]["nets_balanced"] is True
    conn.close()
