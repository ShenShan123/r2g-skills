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


# --- regression: stage_log.jsonl `status` is the int exit code in production ---
# run_orfs.sh writes {"status": 0} (shell exit code), NOT {"status": "pass"}.
# Before the _norm_stage_status fix, _derive_orfs_status compared the int to the
# strings "pass"/"fail", so 0 == "pass" was always False and EVERY run (clean or
# aborted) collapsed to ('partial', None) — which also suppressed the
# orfs-fail-<stage> failure_event. These tests pin the real on-disk format.
_SIX = ["synth", "floorplan", "place", "cts", "route", "finish"]


def test_derive_orfs_status_int_exitcodes_all_pass():
    stages = [{"stage": s, "status": 0, "elapsed_s": 1} for s in _SIX]
    assert ingest_run._derive_orfs_status(stages) == ("pass", None)


def test_derive_orfs_status_int_exitcode_failure_attributes_stage():
    stages = [{"stage": "synth", "status": 0},
              {"stage": "floorplan", "status": 0},
              {"stage": "place", "status": 2}]  # PPL-0024 aborts place with exit 2
    assert ingest_run._derive_orfs_status(stages) == ("fail", "place")


def test_derive_orfs_status_string_form_still_supported():
    stages = [{"stage": s, "status": "pass"} for s in _SIX]
    assert ingest_run._derive_orfs_status(stages) == ("pass", None)


def test_norm_stage_status_accepts_both_forms():
    n = ingest_run._norm_stage_status
    assert n(0) == "pass" and n(2) == "fail" and n(137) == "fail"
    assert n("pass") == "pass" and n("fail") == "fail"
    assert n(True) == "pass" and n(False) == "fail"
    assert n("weird") is None and n(None) is None


def test_ingest_orfs_abort_records_failure_event_with_errcode(tmp_knowledge_dir, tmp_path):
    """An ORFS stage abort must land in failure_events with the tool's own error
    code as the signature (regression for the orfs_status int/str bug that made
    orfs_status never 'fail', silently suppressing every backend-failure event)."""
    proj = tmp_path / "demux_fail"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demux\nexport PLATFORM = sky130hd\n")
    run = proj / "backend" / "RUN_2026-06-12_00-00-00"
    run.mkdir(parents=True)
    # production stage_log: int exit codes, place aborts with exit 2
    (run / "stage_log.jsonl").write_text(
        '{"stage": "synth", "status": 0, "elapsed_s": 5}\n'
        '{"stage": "floorplan", "status": 0, "elapsed_s": 5}\n'
        '{"stage": "place", "status": 2, "elapsed_s": 10}\n')
    (run / "flow.log").write_text(
        "[INFO] placing\n"
        "[ERROR PPL-0024] Number of IO pins (1521) exceeds maximum number of "
        "available positions (718).\n"
        "ERROR: Stage 'place' failed (exit code 2)\n")

    conn = _open_db(tmp_knowledge_dir)
    run_id = ingest_run.ingest(proj, conn,
                               families_path=tmp_knowledge_dir / "families.json")
    status, fail_stage = conn.execute(
        "SELECT orfs_status, orfs_fail_stage FROM runs WHERE run_id=?", (run_id,)
    ).fetchone()
    assert (status, fail_stage) == ("fail", "place")
    sig, detail = conn.execute(
        "SELECT signature, detail FROM failure_events WHERE run_id=? "
        "AND signature LIKE 'orfs-fail-%'", (run_id,)).fetchone()
    assert sig == "orfs-fail-place-PPL-0024"
    assert "PPL-0024" in detail


def test_ingest_reads_clk_period_from_sdc_and_staged_slacks(tmp_path, tmp_knowledge_dir):
    import ingest_run, knowledge_db, json as _json
    proj = tmp_path / "design_cases" / "demo"
    (proj / "constraints").mkdir(parents=True)
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n", encoding="utf-8")
    # Period lives in the SDC, NOT config.mk (this is the bug being fixed).
    (proj / "constraints" / "constraint.sdc").write_text(
        "set clk_period 3.5\ncreate_clock -period $clk_period [get_ports clk]\n", encoding="utf-8")
    (proj / "reports" / "ppa.json").write_text(_json.dumps({
        "summary": {
            "timing": {"setup_wns": 0.4, "setup_tns": 0.0},
            "timing_staged": {"floorplan_setup_ws": 0.9,
                              "place_setup_ws": 0.5,
                              "finish_setup_ws": 0.4},
        }
    }), encoding="utf-8")

    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    rid = ingest_run.ingest(proj, conn, families_path=tmp_knowledge_dir / "families.json")
    r = conn.execute(
        "SELECT clock_period_ns, floorplan_setup_ws, place_setup_ws, finish_setup_ws "
        "FROM runs WHERE run_id=?", (rid,)).fetchone()
    assert r == (3.5, 0.9, 0.5, 0.4)
    conn.close()


def test_backfill_updates_staged_slacks_from_logs(tmp_path, tmp_knowledge_dir):
    import ingest_run, knowledge_db, json as _json
    cases = tmp_path / "design_cases"
    proj = cases / "demo"
    (proj / "constraints").mkdir(parents=True)
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n", encoding="utf-8")
    (proj / "constraints" / "constraint.sdc").write_text("set clk_period 4.0\n", encoding="utf-8")
    # An OLD ppa.json without timing_staged (pre-feature run).
    (proj / "reports" / "ppa.json").write_text(_json.dumps(
        {"summary": {"timing": {"setup_wns": 0.6}}}), encoding="utf-8")
    logs = proj / "backend" / "RUN_2026-01-01_00-00-00" / "logs"
    logs.mkdir(parents=True)
    (logs / "2_1_floorplan.json").write_text(
        _json.dumps({"floorplan__timing__setup__ws": 1.2}), encoding="utf-8")
    (logs / "3_5_place_dp.json").write_text(
        _json.dumps({"detailedplace__timing__setup__ws": 0.8}), encoding="utf-8")

    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    # First ingest the (old) run; staged columns are NULL because ppa.json lacks them.
    ingest_run.ingest(proj, conn, families_path=tmp_knowledge_dir / "families.json")
    assert conn.execute("SELECT place_setup_ws FROM runs").fetchone()[0] is None
    # Backfill from the preserved logs.
    n = ingest_run.backfill(cases, conn)
    assert n == 1
    r = conn.execute(
        "SELECT clock_period_ns, floorplan_setup_ws, place_setup_ws, finish_setup_ws "
        "FROM runs").fetchone()
    assert r == (4.0, 1.2, 0.8, 0.6)  # finish backfilled from existing wns_ns
    conn.close()


def test_backfill_filters_unconstrained_sentinel(tmp_path, tmp_knowledge_dir):
    import ingest_run, knowledge_db, json as _json
    cases = tmp_path / "design_cases"
    proj = cases / "demo"
    (proj / "constraints").mkdir(parents=True)
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n", encoding="utf-8")
    (proj / "constraints" / "constraint.sdc").write_text("set clk_period 4.0\n", encoding="utf-8")
    (proj / "reports" / "ppa.json").write_text(_json.dumps(
        {"summary": {"timing": {"setup_wns": 0.6}}}), encoding="utf-8")
    logs = proj / "backend" / "RUN_2026-01-01_00-00-00" / "logs"
    logs.mkdir(parents=True)
    (logs / "3_5_place_dp.json").write_text(
        _json.dumps({"detailedplace__timing__setup__ws": 1e39}), encoding="utf-8")
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    ingest_run.ingest(proj, conn, families_path=tmp_knowledge_dir / "families.json")
    ingest_run.backfill(cases, conn)
    assert conn.execute("SELECT place_setup_ws FROM runs").fetchone()[0] is None
    conn.close()


def test_main_backfill_runs_without_a_project_arg(tmp_path, tmp_knowledge_dir, monkeypatch, capsys):
    """`ingest_run.py --backfill <dir>` must work standalone — the --help text
    promises a self-contained 'backfill ... then exit' mode, so requiring a
    dummy `project` positional is a usability bug."""
    import ingest_run
    cases = tmp_path / "design_cases"
    cases.mkdir()
    monkeypatch.setattr("sys.argv", [
        "ingest_run.py", "--backfill", str(cases),
        "--db", str(tmp_knowledge_dir / "runs.sqlite"),
        "--schema", str(tmp_knowledge_dir / "schema.sql"),
    ])
    rc = ingest_run.main()
    assert rc == 0
    assert "Backfilled" in capsys.readouterr().out
