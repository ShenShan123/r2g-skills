"""Tests for extract_ppa.py staged-timing readers."""
from __future__ import annotations
import json
import extract_ppa


def _write(logs, name, payload):
    logs.mkdir(parents=True, exist_ok=True)
    (logs / name).write_text(json.dumps(payload), encoding="utf-8")


def test_parse_stage_metrics_floorplan_and_place(tmp_path):
    run_dir = tmp_path / "RUN_x"
    logs = run_dir / "logs"
    _write(logs, "2_1_floorplan.json",
           {"floorplan__timing__setup__ws": 5.88, "floorplan__timing__setup__tns": 0})
    _write(logs, "3_5_place_dp.json",
           {"detailedplace__timing__setup__ws": 5.81, "detailedplace__timing__setup__tns": -0.2})

    fp = extract_ppa.parse_stage_metrics(run_dir, "floorplan")
    pl = extract_ppa.parse_stage_metrics(run_dir, "place")
    assert fp == {"setup_wns": 5.88, "setup_tns": 0}
    assert pl == {"setup_wns": 5.81, "setup_tns": -0.2}


def test_parse_stage_metrics_place_falls_back_to_3_4(tmp_path):
    run_dir = tmp_path / "RUN_x"
    logs = run_dir / "logs"
    _write(logs, "3_4_place_resized.json",
           {"placeopt__timing__setup__ws": 7.0, "placeopt__timing__setup__tns": 0})
    # No 3_5 file present -> must fall back to 3_4 placeopt keys.
    assert extract_ppa.parse_stage_metrics(run_dir, "place") == {"setup_wns": 7.0, "setup_tns": 0}


def test_parse_stage_metrics_missing_returns_empty(tmp_path):
    run_dir = tmp_path / "RUN_x"
    (run_dir / "logs").mkdir(parents=True)
    assert extract_ppa.parse_stage_metrics(run_dir, "place") == {}


def test_collect_timing_staged(tmp_path):
    run_dir = tmp_path / "RUN_x"
    logs = run_dir / "logs"
    _write(logs, "2_1_floorplan.json", {"floorplan__timing__setup__ws": 5.88})
    _write(logs, "3_5_place_dp.json", {"detailedplace__timing__setup__ws": 5.81})
    staged = extract_ppa.collect_timing_staged(run_dir)
    assert staged == {"floorplan_setup_ws": 5.88, "place_setup_ws": 5.81}
