"""Tests for extract_route.py: route-stage outcome -> reports/route.json.

A route abort is the backend-stage analogue of a signoff DRC violation; the
extractor must emit the same {status, total_violations} contract the fix loop
consumes, distinguishing clean / residual-fail / wall-clock timeout / unknown.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "extract" / "extract_route.py"


def _mk_run(proj: Path, *, route_status, residual_report=None, drt_log_tail=None):
    """Materialize a fake backend run with a stage_log + optional route artifacts."""
    run = proj / "backend" / "RUN_2026-06-17_00-00-00"
    (run / "reports_orfs").mkdir(parents=True, exist_ok=True)
    stages = [{"stage": "synth", "status": 0}, {"stage": "floorplan", "status": 0},
              {"stage": "place", "status": 0}, {"stage": "cts", "status": 0}]
    if route_status is not None:
        stages.append({"stage": "route", "status": route_status})
    (run / "stage_log.jsonl").write_text(
        "\n".join(json.dumps(s) for s in stages) + "\n", encoding="utf-8")
    if residual_report is not None:
        (run / "reports_orfs" / "5_route_drc.rpt").write_text(residual_report, encoding="utf-8")
    if drt_log_tail is not None:
        ld = run / "logs" / "sky130hd" / "d" / "v"
        ld.mkdir(parents=True, exist_ok=True)
        (ld / "5_2_route.log").write_text(drt_log_tail, encoding="utf-8")
    return run


def _run(proj: Path) -> dict:
    out = proj / "reports" / "route.json"
    r = subprocess.run([sys.executable, str(SCRIPT), str(proj), str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(out.read_text())


def test_clean_route(tmp_path):
    # route stage exit 0 + EMPTY 5_route_drc.rpt -> clean, 0 violations
    _mk_run(tmp_path, route_status=0, residual_report="")
    res = _run(tmp_path)
    assert res["status"] == "clean"
    assert res["total_violations"] == 0
    assert res["completed"] is True


def test_residual_fail(tmp_path):
    # route exit 0 but the route DRC report has markers -> fail with a count
    rpt = ("violation type: Metal Spacing\n  bbox: ...\n"
           "violation type: Cut Spacing\n  bbox: ...\n")
    _mk_run(tmp_path, route_status=0, residual_report=rpt)
    res = _run(tmp_path)
    assert res["status"] == "fail"
    assert res["total_violations"] == 2


def test_timeout(tmp_path):
    # route stage killed by the wall-clock timeout (124) -> 'timeout', residual
    # recovered from the DRT log grind snapshot
    log = ("Completing 40% with 5247 violations.\n"
           "    elapsed time = 00:10:47\n"
           "Completing 50% with 4811 violations.\n")
    _mk_run(tmp_path, route_status=124, drt_log_tail=log)
    res = _run(tmp_path)
    assert res["status"] == "timeout"
    assert res["total_violations"] == 4811
    assert res["completed"] is False


def test_no_route_stage_is_unknown(tmp_path):
    # aborted before route (e.g. place killed) -> unknown so the route fixer no-ops
    _mk_run(tmp_path, route_status=None)
    res = _run(tmp_path)
    assert res["status"] == "unknown"


def test_no_backend_is_unknown(tmp_path):
    res = _run(tmp_path)
    assert res["status"] == "unknown"
    assert res["total_violations"] is None
