"""Finish-stage max-slew / max-capacitance repair (failure-patterns "finish-stage
max-slew/max-cap"; acceptance check A8).

check_timing.py reports the design-rule violator counts beside the setup tier; the
loop treats setup-met-but-DRV-dirty as timing 'drv'; diagnose offers drv_route_margin
(ladder 10 / 20 / 40 % over-fix of the global-route-stage repair_design, one strategy id
per step); run_orfs.sh applies
the margins to the route stage only.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts" / "reports"))
sys.path.insert(0, str(SKILL / "scripts" / "loop"))
sys.path.insert(0, str(SKILL / "knowledge"))
import check_timing  # noqa: E402
import diagnose_signoff_fix as dsf  # noqa: E402
import engineer_loop  # noqa: E402

DRV_FAIL = {"max_slew_violations": 2, "max_cap_violations": 1, "total": 3, "status": "fail"}


def test_drv_summary_counts_and_unknown() -> None:
    assert check_timing.drv_summary({"max_slew_violations": 0, "max_cap_violations": 0})["status"] == "clean"
    s = check_timing.drv_summary({"max_slew_violations": 2, "max_cap_violations": 1})
    assert (s["status"], s["total"]) == ("fail", 3)
    assert check_timing.drv_summary({"max_slew_violations": 2})["status"] == "unknown"


def test_check_timing_writes_drv_beside_the_setup_tier(tmp_path: Path) -> None:
    proj = tmp_path / "p"
    (proj / "reports").mkdir(parents=True)
    (proj / "reports" / "ppa.json").write_text(json.dumps({"summary": {"timing": {
        "setup_wns": 0.2, "setup_tns": 0, "hold_wns": 0.1, "hold_tns": 0,
        "max_slew_violations": 2, "max_cap_violations": 1}}}))
    subprocess.run([sys.executable, str(SKILL / "scripts" / "reports" / "check_timing.py"), str(proj)],
                   capture_output=True, text=True)
    out = json.loads((proj / "reports" / "timing_check.json").read_text())
    assert out["tier"] == "clean" and out["drv"]["status"] == "fail" and out["drv"]["total"] == 3


def _plan(tier: str, cfg: dict, routing_clean: bool = True, exclude=frozenset()) -> dict:
    return dsf._timing_plan({"tier": tier, "wns_ns": 0.1 if tier == "clean" else -0.5,
                             "drv": DRV_FAIL}, {"PLATFORM": "sky130hd", **cfg}, set(exclude),
                            routing_clean=routing_clean)


def test_setup_met_drv_dirty_offers_the_route_margin_ladder() -> None:
    p = _plan("clean", {})
    assert p["status"] == "drv" and p["violation_count"] == 3
    (s,) = p["strategies"]
    assert s["id"] == "drv_route_margin_10" and s["rerun_from"] == "route"
    assert s["config_edits"] == {"R2G_ROUTE_SLEW_MARGIN": "10", "R2G_ROUTE_CAP_MARGIN": "10"}
    # an accepted step that did not clear: the config carries it, the next step follows
    assert _plan("clean", {"R2G_ROUTE_SLEW_MARGIN": "10"},
                 exclude={"drv_route_margin_10"})["strategies"][0]["id"] == "drv_route_margin_20"
    # a rejected step was rolled back (no margin in config) but stays excluded: skip it
    assert _plan("clean", {}, exclude={"drv_route_margin_10"})["strategies"][0]["id"] == "drv_route_margin_20"
    done = _plan("clean", {"R2G_ROUTE_SLEW_MARGIN": "20"},
                 exclude={"drv_route_margin_10", "drv_route_margin_20", "drv_route_margin_40"})
    assert done["strategies"] == [] and done["residual_reason"] == "drv_route_margin_exhausted"


def test_no_route_margin_without_clean_routing_and_last_behind_setup_repairs() -> None:
    assert _plan("clean", {}, routing_clean=False)["strategies"] == []
    ids = [s["id"] for s in _plan("moderate", {})["strategies"]]
    assert ids and ids[-1] == "drv_route_margin_10"


def test_loop_reports_drv_as_not_clean(tmp_path: Path) -> None:
    rep = tmp_path / "reports"
    rep.mkdir()
    (rep / "timing_check.json").write_text(json.dumps({"tier": "clean", "wns": 0.1, "drv": DRV_FAIL}))
    st = engineer_loop._signoff_status({"project_path": str(tmp_path)})
    assert st["timing"] == "drv" and not engineer_loop._all_signoff_clean(st)
    (rep / "timing_check.json").write_text(json.dumps(
        {"tier": "clean", "wns": 0.1, "drv": {**DRV_FAIL, "status": "clean", "total": 0}}))
    assert engineer_loop._signoff_status({"project_path": str(tmp_path)})["timing"] == "clean"


def _route_margins_seen(tmp_path: Path, stages: str, config_extra: str) -> str:
    """run_orfs.sh against a fake ORFS checkout; a stub make records the margins it sees."""
    skill = tmp_path / "skill"
    (skill / "scripts").mkdir(parents=True)
    shutil.copytree(SKILL / "scripts" / "flow", skill / "scripts" / "flow")
    (skill / "knowledge").mkdir()
    flow = tmp_path / "orfs" / "flow"
    (flow / "platforms" / "nangate45").mkdir(parents=True)
    (flow / "Makefile").write_text("# fake\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    seen = tmp_path / "make_env.txt"
    make = bindir / "make"
    make.write_text("#!/usr/bin/env bash\n"
                    f'echo "${{@: -1}}|${{SLEW_MARGIN:-}}|${{CAP_MARGIN:-}}" >> "{seen}"\nexit 2\n')
    make.chmod(make.stat().st_mode | stat.S_IXUSR)
    proj = tmp_path / "proj"
    (proj / "constraints").mkdir(parents=True)
    (proj / "rtl").mkdir()
    (proj / "rtl" / "w.v").write_text("module w(); endmodule\n")
    sdc = proj / "constraints" / "constraint.sdc"
    sdc.write_text("create_clock -period 10\n")
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = w\nexport PLATFORM = nangate45\n"
        f"export VERILOG_FILES = {proj / 'rtl' / 'w.v'}\nexport SDC_FILE = {sdc}\n" + config_extra)
    env = {k: v for k, v in os.environ.items()
           if k not in ("SLEW_MARGIN", "CAP_MARGIN", "R2G_ENV_FILE")}
    env.update(ORFS_ROOT=str(tmp_path / "orfs"), PATH=f"{bindir}:{os.environ['PATH']}",
               R2G_LOCK_DIR=str(tmp_path), R2G_JOURNAL_DB=str(tmp_path / "j.sqlite"),
               ORFS_STAGES=stages)
    subprocess.run(["bash", str(skill / "scripts" / "flow" / "run_orfs.sh"), str(proj), "nangate45", "v1"],
                   env=env, capture_output=True, text=True, timeout=120)
    assert seen.exists(), "the stub make never ran"
    return [ln for ln in seen.read_text().splitlines() if ln.split("|")[0] in ("synth", "route")][-1]


def test_route_margins_reach_only_the_route_stage(tmp_path: Path) -> None:
    knobs = "export R2G_ROUTE_SLEW_MARGIN = 20\nexport R2G_ROUTE_CAP_MARGIN = 20\n"
    assert _route_margins_seen(tmp_path / "r", "route", knobs) == "route|20|20"
    assert _route_margins_seen(tmp_path / "s", "synth", knobs) == "synth||"
    assert _route_margins_seen(tmp_path / "n", "route", "") == "route||"
