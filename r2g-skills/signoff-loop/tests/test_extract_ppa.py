"""Tests for extract_ppa.detect_orfs_progress — ORFS stage-progress detection.

The campaign driver (run_sky130_design.sh) labels a backend abort's residual as
`orfs_<fail_stage>` from this function's `orfs_fail_stage`. It must agree with the
knowledge store (`ingest_run._derive_orfs_status`), which reads the authoritative
`stage_log.jsonl`. The historical ODB-probe heuristic mis-attributed aborts (a
`place` failure with no collected ODBs probed as `synth`) — these tests pin the
stage_log-first behavior and the ODB-probe fallback.
"""
from __future__ import annotations

import json
from pathlib import Path

import extract_ppa


def _write_stage_log(run_dir: Path, rows: list[dict]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "stage_log.jsonl").open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def test_stage_log_attributes_failure_to_failing_stage(tmp_path):
    """synth+floorplan pass (exit 0), place aborts (exit 2) -> fail_stage='place',
    NOT 'synth'. This is the iccad2015_unit14_in1 / DPL-0036 production case."""
    run = tmp_path / "RUN_x"
    _write_stage_log(run, [
        {"stage": "synth", "status": 0},
        {"stage": "floorplan", "status": 0},
        {"stage": "place", "status": 2},
    ])
    out = extract_ppa.detect_orfs_progress(run)
    assert out["orfs_status"] == "fail"
    assert out["orfs_fail_stage"] == "place"
    assert out["orfs_last_stage"] == "floorplan"


def test_stage_log_all_pass_is_complete(tmp_path):
    run = tmp_path / "RUN_x"
    _write_stage_log(run, [
        {"stage": s, "status": 0}
        for s in ("synth", "floorplan", "place", "cts", "route", "finish")
    ])
    out = extract_ppa.detect_orfs_progress(run)
    assert out["orfs_status"] == "complete"
    assert out["orfs_fail_stage"] is None
    assert out["orfs_last_stage"] == "finish"


def test_stage_log_partial_no_explicit_fail(tmp_path):
    """Incomplete log with no failed stage -> partial, first not-done stage."""
    run = tmp_path / "RUN_x"
    _write_stage_log(run, [
        {"stage": "synth", "status": 0},
        {"stage": "floorplan", "status": 0},
    ])
    out = extract_ppa.detect_orfs_progress(run)
    assert out["orfs_status"] == "partial"
    assert out["orfs_fail_stage"] == "place"


def test_stage_log_string_status_form(tmp_path):
    """Legacy string status form ('pass'/'fail') maps the same as int exit codes."""
    run = tmp_path / "RUN_x"
    _write_stage_log(run, [
        {"stage": "synth", "status": "pass"},
        {"stage": "floorplan", "status": "fail"},
    ])
    out = extract_ppa.detect_orfs_progress(run)
    assert out["orfs_status"] == "fail"
    assert out["orfs_fail_stage"] == "floorplan"


def test_falls_back_to_odb_probe_without_stage_log(tmp_path):
    """No stage_log -> probe collected ODBs. finish ODB present -> complete."""
    run = tmp_path / "RUN_x"
    results = run / "results"
    results.mkdir(parents=True)
    for odb in ("1_synth.odb", "2_floorplan.odb", "3_place.odb",
                "4_cts.odb", "5_route.odb", "6_final.odb"):
        (results / odb).write_text("")
    out = extract_ppa.detect_orfs_progress(run)
    assert out["orfs_status"] == "complete"
    assert out["orfs_last_stage"] == "finish"


def test_odb_probe_fallback_partial(tmp_path):
    """No stage_log, only synth/floorplan ODBs present -> partial, fail_stage='place'."""
    run = tmp_path / "RUN_x"
    results = run / "results"
    results.mkdir(parents=True)
    for odb in ("1_synth.odb", "2_floorplan.odb"):
        (results / odb).write_text("")
    out = extract_ppa.detect_orfs_progress(run)
    assert out["orfs_status"] == "partial"
    assert out["orfs_fail_stage"] == "place"
