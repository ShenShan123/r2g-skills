"""Acceptance check v1 (scripts/reports/acceptance_check.py; memory/evaluation/r2g_acceptance_checklist_v1_20261004.md)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "reports"))
import acceptance_check as ac  # noqa: E402


def _project(tmp_path, **over):
    p = tmp_path / "proj"
    (p / "reports").mkdir(parents=True)
    (p / "constraints").mkdir()
    (p / "constraints" / "constraint.sdc").write_text("set clk_period 10.0\n")
    files = {
        "signoff_gate.json": {"checks": {"orfs": {"status": "complete"}, "antenna": {"status": "clean"},
                                         "binding": {"status": "bound"}, "report_binding": {"status": "bound"},
                                         "artifact_digest": {"status": "bound"}}},
        "drc.json": {"status": "clean", "total_violations": 0},
        "lvs.json": {"status": "clean"},
        "route.json": {"status": "clean", "total_violations": 0, "completed": True},
        "rcx.json": {"status": "complete", "spef_file": "x.spef", "net_count": 10},
        "ppa.json": {"summary": {"timing": {"setup_wns": 0.2, "setup_tns": 0, "hold_wns": 0.1, "hold_tns": 0,
                                            "max_cap_violations": 0, "max_slew_violations": 0}}},
    }
    files.update(over)
    for name, body in files.items():
        if body is not None:
            (p / "reports" / name).write_text(json.dumps(body))
    return p


def test_all_checks_pass(tmp_path):
    r = ac.evaluate(_project(tmp_path), 10.0)
    assert r["accepted"] and not r["failed"] and not r["unknown"] and len(r["checks"]) == 11


def test_each_strictness_rule(tmp_path):
    t = {"setup_wns": 0.2, "setup_tns": 0, "hold_wns": 0.1, "hold_tns": 0, "max_cap_violations": 0,
         "max_slew_violations": 0}
    cases = {
        "A2_drc_full_deck": {"drc.json": {"status": "clean_beol", "total_violations": 0}},
        "A3_lvs": {"lvs.json": {"status": "mismatch"}},
        "A6_setup": {"ppa.json": {"summary": {"timing": {**t, "setup_wns": -0.01, "setup_tns": -0.05}}}},
        "A7_hold": {"ppa.json": {"summary": {"timing": {**t, "hold_wns": -0.02, "hold_tns": -0.3}}}},
        "A8_drv_limits": {"ppa.json": {"summary": {"timing": {**t, "max_slew_violations": 3}}}},
    }
    for i, (check, over) in enumerate(cases.items()):
        r = ac.evaluate(_project(tmp_path / str(i), **over), 10.0)
        assert not r["accepted"] and r["failed"] == [check], (check, r["failed"])


def test_relaxed_clock_and_missing_inputs_never_pass(tmp_path):
    p = _project(tmp_path / "a")
    assert ac.evaluate(p, 8.0)["failed"] == ["A9_task_clock"]
    assert ac.evaluate(p, None)["unknown"] == ["A9_task_clock"] and not ac.evaluate(p, None)["accepted"]
    r = ac.evaluate(_project(tmp_path / "b", **{"rcx.json": None, "ppa.json": None}), 10.0)
    assert not r["accepted"] and {"A6_setup", "A7_hold", "A8_drv_limits", "A10_rcx"} <= set(r["unknown"])
