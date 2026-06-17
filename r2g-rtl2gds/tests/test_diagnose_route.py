"""Tests for the route-stage (backend-abort) fix plan: route_relief.

A route abort (orfs-fail-route) never reaches signoff DRC, so it flows through a
new check='route' path that emits route_relief (lower CORE_UTILIZATION, rerun from
floorplan). Mirrors density_relief but keyed to the route stage. This wiring is
what lets the A/B loop SEE a route-congestion symptom.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import diagnose_signoff_fix as d

MOD = Path(__file__).resolve().parents[1] / "scripts" / "reports" / "diagnose_signoff_fix.py"


def _route(status, count=None):
    return {"status": status, "total_violations": count}


def test_clean_route_no_strategies():
    plan = d.build_plan({}, {}, {"CORE_UTILIZATION": "25"}, check="route",
                        route=_route("clean", 0))
    assert plan["status"] == "clean"
    assert plan["strategies"] == []


def test_route_fail_yields_route_relief():
    cfg = {"CORE_UTILIZATION": "25", "PLATFORM": "sky130hd"}
    plan = d.build_plan({}, {}, cfg, check="route", route=_route("fail", 5247))
    ids = [s["id"] for s in plan["strategies"]]
    assert ids == ["route_relief"]
    s = plan["strategies"][0]
    assert s["config_edits"]["CORE_UTILIZATION"] == "17"   # 25 - _UTIL_STEP(8)
    assert s["rerun_from"] == "floorplan"
    assert s["recheck"] == "route"
    assert s["auto_apply"] is True


def test_route_timeout_also_relieves():
    cfg = {"CORE_UTILIZATION": "20"}
    plan = d.build_plan({}, {}, cfg, check="route", route=_route("timeout", None))
    assert [s["id"] for s in plan["strategies"]] == ["route_relief"]
    assert plan["strategies"][0]["config_edits"]["CORE_UTILIZATION"] == "12"


def test_route_die_area_design_is_honest_residual():
    # No CORE_UTILIZATION knob (DIE_AREA-sized) -> route_relief no-op, honest residual
    cfg = {"DIE_AREA": "0 0 400 400", "PLATFORM": "sky130hd"}
    plan = d.build_plan({}, {}, cfg, check="route", route=_route("fail", 3585))
    assert plan["strategies"] == []
    assert plan["status"] == "residual"
    assert "DIE_AREA" in plan["residual_reason"]


def test_route_util_at_floor_is_residual():
    cfg = {"CORE_UTILIZATION": "8"}      # already at _UTIL_FLOOR
    plan = d.build_plan({}, {}, cfg, check="route", route=_route("fail", 100))
    assert plan["strategies"] == []
    assert plan["status"] == "residual"


def test_route_unknown_no_fix():
    # route.json with no route-stage outcome (abort earlier) -> no strategy
    plan = d.build_plan({}, {}, {"CORE_UTILIZATION": "25"}, check="route",
                        route=_route("unknown"))
    assert plan["strategies"] == []


def _cli(proj: Path, *args):
    return subprocess.run([sys.executable, str(MOD), str(proj), *args],
                          capture_output=True, text=True)


def test_cli_next_and_apply_route(tmp_path):
    (tmp_path / "constraints").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = foo\nexport PLATFORM = sky130hd\n"
        "export CORE_UTILIZATION = 25\n", encoding="utf-8")
    (tmp_path / "reports" / "route.json").write_text(
        json.dumps({"status": "timeout", "total_violations": 5247}), encoding="utf-8")
    # --next emits the driver line
    r = _cli(tmp_path, "--check", "route", "--next")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "route_relief\tfloorplan\troute"
    # --apply writes the lowered util into the marked auto-block
    r = _cli(tmp_path, "--check", "route", "--apply", "route_relief")
    assert r.returncode == 0, r.stderr
    cfg_text = (tmp_path / "constraints" / "config.mk").read_text()
    assert "export CORE_UTILIZATION = 17" in cfg_text
    assert json.loads(r.stdout)["applied"] == "route_relief"
