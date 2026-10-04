"""Memory-strategy safety gates and the pre-escalation memory hook
(R2G memory redesign B4/B5/B6, 2026-10-01).

B4  a memory strategy passes exclusion, the already-applied check and ONE hard knob
    policy (knowledge/knob_policy.py) — never clamped, dropped with a logged reason;
B5  vetoes drop catalogue AND memory strategies, scoped by situation;
B6  a backend residual asks memory once before escalating, records the attempt.
"""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import pytest

import diagnose_signoff_fix as dsf
import knob_policy

SIT = {"v": "sit-v1", "check": "orfs_stage", "violation_class": "place",
       "error_code": "FLW-0024", "timeout": False, "platform": "sky130hd",
       "die_mode": "auto", "util_band": "31-60", "count_band": None}


# -- B4 knob policy -------------------------------------------------------------

def test_knob_policy_hard_rules():
    assert knob_policy.edit_violation("CORE_UTILIZATION", "40") is None
    assert "outside" in knob_policy.edit_violation("CORE_UTILIZATION", "95")
    assert "outside" in knob_policy.edit_violation("PLACE_DENSITY_LB_ADDON", "0.05")
    assert "not a whitelisted" in knob_policy.edit_violation("CLOCK_PERIOD", "20")
    assert knob_policy.edit_violation("DIE_AREA", "0 0 300 300") is None
    assert "x0 y0" in knob_policy.edit_violation("DIE_AREA", "300x300")
    assert knob_policy.edit_violation("MAX_ROUTING_LAYER", "met5") is None
    assert knob_policy.edits_violations({}) == ["empty edit map"]


def test_memory_strategies_gated_with_reasons(capsys):
    cfg = {"CORE_UTILIZATION": "40"}
    sts = [{"id": "tehm_a", "config_edits": {"CORE_UTILIZATION": "30"}},
           {"id": "tehm_b", "config_edits": {"CORE_UTILIZATION": "30"}},   # excluded
           {"id": "tehm_c", "config_edits": {"CORE_UTILIZATION": "40"}},   # in effect
           {"id": "tehm_d", "config_edits": {"CLOCK_PERIOD": "20"}},       # policy
           {"id": "tehm_e", "config_edits": {"PLACE_DENSITY_LB_ADDON": "0.02"}}]
    kept = dsf._gate_memory_strategies(sts, cfg, {"tehm_b"})
    assert [s["id"] for s in kept] == ["tehm_a"]
    err = capsys.readouterr().err
    for frag in ("tehm_b dropped: already tried", "tehm_c dropped: edits already",
                 "tehm_d dropped: knob policy", "tehm_e dropped: knob policy"):
        assert frag in err


# -- B5 vetoes -------------------------------------------------------------------

def test_vetoes_drop_catalogue_and_memory_strategies(capsys):
    plan = [{"id": "density_relief"}, {"id": "route_relief"},
            {"id": "tehm_x", "transformation_family": "DENSITY_RELIEF"}]
    ev = {"failures": 2, "passes": 0, "designs": ["a", "b"], "transitions": []}
    kept = dsf._apply_vetoes(plan, {"density_relief": ev})
    assert [s["id"] for s in kept] == ["route_relief"]
    assert "vetoed in this situation" in capsys.readouterr().err


def test_current_situation_prefers_the_logged_snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv("R2G_LOG_SITUATION", json.dumps(SIT))
    assert dsf._current_situation(tmp_path, "drc", {}, {}) == SIT


# -- B6 pre-escalation hook ----------------------------------------------------------

@pytest.fixture
def loop(monkeypatch, tmp_path):
    import engineer_loop as el
    proj = tmp_path / "gcd"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = gcd\nexport PLATFORM = sky130hd\n"
        "export CORE_UTILIZATION = 40\n"
        "# >>> r2g signoff-fix (auto) >>>\nexport PLACE_DENSITY_LB_ADDON = 0.20\n"
        "# <<< r2g signoff-fix (auto) <<<\n")
    calls = {"retries": 0, "strategies": [], "vetoes": {}}
    fake = types.ModuleType("runtime_router")
    fake.signoff_strategies = lambda **kw: calls["strategies"]
    fake.signoff_vetoes = lambda **kw: calls["vetoes"]
    monkeypatch.setitem(sys.modules, "runtime_router", fake)
    monkeypatch.setenv("R2G_MEMORY_BACKEND", "tehm")
    monkeypatch.setattr(el.situation, "from_project", lambda *a, **k: dict(SIT))
    monkeypatch.setattr(el, "_ingest", lambda e: None)
    state = {"fail_stage": None}
    monkeypatch.setattr(el, "_fail_stage", lambda e: state["fail_stage"])

    def _retry(led, entry, conn, **kw):
        calls["retries"] += 1
        return "clean" if state["fail_stage"] is None else "escalated"
    monkeypatch.setattr(el, "process_one", _retry)
    led = types.SimpleNamespace(set_state=lambda *a: None)
    return el, led, {"design": "gcd", "project_path": str(proj)}, calls, state


def _rows(entry):
    p = Path(entry["project_path"]) / "reports" / "fix_log.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def test_hook_applies_one_rule_merges_block_and_records(loop):
    el, led, entry, calls, state = loop
    calls["strategies"] = [{"id": "tehm_r1", "rule_id": "r1",
                            "transformation_family": "CORE_UTIL_RELIEF",
                            "config_edits": {"CORE_UTILIZATION": "30"}}]
    assert el._memory_recovery(led, entry, None, "place") == "clean"
    cfg = (Path(entry["project_path"]) / "constraints" / "config.mk").read_text()
    assert "export CORE_UTILIZATION = 30" in cfg
    assert "export PLACE_DENSITY_LB_ADDON = 0.20" in cfg      # earlier auto edit kept
    row = _rows(entry)[-1]
    assert row["strategy"] == "core_util_relief" and row["memory_rule"] == "r1"
    assert row["verdict"] == "cleared" and row["situation"] == SIT
    assert json.loads(row["config_delta"])["CORE_UTILIZATION"] == {"before": "40", "after": "30"}
    # once per visit: a second residual on the same entry escalates
    assert el._memory_recovery(led, entry, None, "place") is None
    assert calls["retries"] == 1


def test_hook_failed_retry_is_recorded_as_negative_evidence(loop):
    el, led, entry, calls, state = loop
    state["fail_stage"] = "place"
    calls["strategies"] = [{"id": "tehm_r1", "rule_id": "r1",
                            "config_edits": {"CORE_UTILIZATION": "30"}}]
    el._memory_recovery(led, entry, None, "place")
    row = _rows(entry)[-1]
    assert row["verdict"] == "no_change" and row["after"] == 1


@pytest.mark.parametrize("case", ["legacy", "ab_arm", "vetoed", "policy", "none"])
def test_hook_escalates_when_memory_has_nothing_usable(loop, monkeypatch, case):
    el, led, entry, calls, state = loop
    calls["strategies"] = [{"id": "tehm_r1", "rule_id": "r1",
                            "transformation_family": "CORE_UTIL_RELIEF",
                            "config_edits": {"CORE_UTILIZATION": "30"}}]
    if case == "legacy":
        monkeypatch.setenv("R2G_MEMORY_BACKEND", "legacy")
    elif case == "ab_arm":
        entry["kind"] = "ab_arm"
    elif case == "vetoed":
        calls["vetoes"] = {"core_util_relief": {"failures": 2}}
    elif case == "policy":
        calls["strategies"][0]["config_edits"] = {"CORE_UTILIZATION": "2"}
    else:
        calls["strategies"] = []
    assert el._memory_recovery(led, entry, None, "place") is None
    assert calls["retries"] == 0 and not _rows(entry)


# -- diagnose --list/--apply under the TEHM backend (integration of B3/B4/B5) ----------

def test_diagnose_tehm_path_passes_situation_gates_and_vetoes(tmp_path, monkeypatch, capsys):
    p = tmp_path / "proj"
    (p / "reports").mkdir(parents=True)
    (p / "constraints").mkdir()
    (p / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = t\nexport PLATFORM = sky130hs\nexport CORE_UTILIZATION = 12\n")
    (p / "reports" / "drc.json").write_text(json.dumps(
        {"status": "fail", "total_violations": 4, "categories": {"li.3": {"count": 4}}}))
    seen = {}
    fake = types.ModuleType("runtime_router")

    def _strategies(**kw):
        seen["situation"] = kw.get("situation")
        return [{"id": "tehm_ok", "source": "tehm_rule", "rule_id": "r_ok",
                 "transformation_family": "CORE_UTIL_RELIEF", "auto_apply": True,
                 "config_edits": {"CORE_UTILIZATION": "8"}, "rerun_from": "floorplan",
                 "recheck": "drc"},
                {"id": "tehm_bad", "source": "tehm_rule", "rule_id": "r_bad",
                 "auto_apply": True, "config_edits": {"PLACE_DENSITY_LB_ADDON": "0.05"},
                 "rerun_from": "place", "recheck": "drc"}]
    fake.signoff_strategies = _strategies
    fake.signoff_vetoes = lambda **kw: {"density_relief": {
        "failures": 2, "passes": 0, "designs": ["a", "b"], "transitions": []}}
    monkeypatch.setitem(sys.modules, "runtime_router", fake)
    monkeypatch.setenv("R2G_MEMORY_BACKEND", "tehm")
    monkeypatch.delenv("R2G_LOG_SITUATION", raising=False)

    assert dsf.main([str(p), "--check", "drc", "--list"]) == 0
    ids = [st["id"] for st in json.loads(capsys.readouterr().out)["strategies"]]
    assert ids[0] == "tehm_ok" and "tehm_bad" not in ids      # policy-dropped
    assert "density_relief" not in ids                          # vetoed catalogue entry
    assert seen["situation"]["violation_class"] == "li.3"
    assert seen["situation"]["util_band"] == "le12"

    assert dsf.main([str(p), "--check", "drc", "--apply", "tehm_ok"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["applied"] == "tehm_ok" and out["config_before"] == {"CORE_UTILIZATION": "12"}
    assert "export CORE_UTILIZATION = 8" in (p / "constraints" / "config.mk").read_text()


# -- Phase D amendment D-A1: unset knobs + memory attribution ------------------------------

def test_unset_constant_is_shared_with_memory():
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "memory"))
    from contracts import CONFIG_UNSET
    assert knob_policy.UNSET == CONFIG_UNSET == dsf.UNSET
    assert knob_policy.edit_violation("DIE_AREA", knob_policy.UNSET) is None
    assert "not a whitelisted" in knob_policy.edit_violation("CLOCK_PERIOD", knob_policy.UNSET)


def test_apply_edits_unset_removes_every_assignment():
    text = ("export DIE_AREA = 0 0 200 200\nexport CORE_AREA = 10 10 190 190\n"
            "# >>> r2g signoff-fix (auto) >>>\nexport DIE_AREA = 0 0 300 300\n"
            "# <<< r2g signoff-fix (auto) <<<\n")
    out = dsf.apply_edits(text, {"DIE_AREA": dsf.UNSET, "CORE_AREA": dsf.UNSET,
                                 "CORE_UTILIZATION": "40"})
    cfg = dsf.parse_config(out)
    assert "DIE_AREA" not in cfg and "CORE_AREA" not in cfg and cfg["CORE_UTILIZATION"] == "40"
    assert dsf._applied(cfg, {"DIE_AREA": dsf.UNSET, "CORE_UTILIZATION": "40"})


def test_diagnose_apply_reports_memory_attribution_and_lands_unset(tmp_path, monkeypatch, capsys):
    p = tmp_path / "proj"
    (p / "reports").mkdir(parents=True)
    (p / "constraints").mkdir()
    (p / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = t\nexport PLATFORM = sky130hd\nexport DIE_AREA = 0 0 200 200\n"
        "export CORE_AREA = 10 10 190 190\n")
    (p / "reports" / "drc.json").write_text(json.dumps(
        {"status": "fail", "total_violations": 4, "categories": {"li.3": {"count": 4}}}))
    fake = types.ModuleType("runtime_router")
    fake.signoff_strategies = lambda **kw: [{
        "id": "tehm_c1", "source": "tehm_rule", "rule_id": "r1", "auto_apply": True,
        "transformation_family": "LLM_EDIT_CORE_AREA_CORE_UTILIZATION_DIE_AREA",
        "memory_trial": True, "witness_selected": ["CORE_UTILIZATION"],
        "config_edits": {"CORE_UTILIZATION": "40", "DIE_AREA": "<unset>", "CORE_AREA": "<unset>"},
        "rerun_from": "floorplan", "recheck": "drc"}]
    fake.signoff_vetoes = lambda **kw: {}
    monkeypatch.setitem(sys.modules, "runtime_router", fake)
    monkeypatch.setenv("R2G_MEMORY_BACKEND", "tehm")
    assert dsf.main([str(p), "--check", "drc", "--apply", "tehm_c1"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["memory_rule"] == "r1" and out["memory_trial"] is True
    assert out["strategy_family"].startswith("LLM_EDIT_")
    assert out["witness_selected"] == ["CORE_UTILIZATION"]
    cfg = dsf.parse_config((p / "constraints" / "config.mk").read_text())
    assert "DIE_AREA" not in cfg and "CORE_AREA" not in cfg and cfg["CORE_UTILIZATION"] == "40"


def test_diagnose_subprocess_reaches_the_real_tehm_router(tmp_path):
    """No sys.modules stub: diagnose runs as fix_signoff.sh runs it (a subprocess) and
    must import memory/runtime_router. An off-by-one parents[] made it fail closed with
    'No module named runtime_router' on every TEHM run (Phase D2 smoke, 2026-10-01)."""
    import subprocess
    p = tmp_path / "proj"
    (p / "reports").mkdir(parents=True)
    (p / "constraints").mkdir()
    (p / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = t\nexport PLATFORM = sky130hs\nexport CORE_UTILIZATION = 12\n")
    (p / "reports" / "drc.json").write_text(json.dumps(
        {"status": "fail", "total_violations": 4, "categories": {"li.3": {"count": 4}}}))
    env = dict(os.environ, R2G_MEMORY_BACKEND="tehm", TEHM_DB=str(tmp_path / "t.sqlite"),
               TEHM_ARTIFACTS_ROOT=str(tmp_path / "art"))
    r = subprocess.run([sys.executable, str(Path(dsf.__file__)), str(p), "--check", "drc", "--list"],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "TEHM consultation skipped" not in r.stderr, r.stderr[-2000:]
