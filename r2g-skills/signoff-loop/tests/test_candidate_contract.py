"""Shared candidate contract and technical-failure propagation (2026-10-17).

Defect (T2a T1, failure-patterns "Memory candidate broke the executor contract, and the
failure was suppressed"): RECIPE candidates lacked `recheck`, diagnose --next raised
KeyError, fix_signoff read the empty output as an ordinary stop under `fix_one ... || true`,
and the episode continued to LLM turns. Every candidate producer (catalogue, TEHM, RECIPE)
must satisfy one contract; an invalid candidate or a diagnose crash is a TECHNICAL failure
that is recorded, never executed, and surfaced, not scored as a normal repair failure.
"""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "knowledge"))
import candidate_contract as C  # noqa: E402
import technical_failure as TF  # noqa: E402
import diagnose_signoff_fix as D  # noqa: E402

FIX_SIGNOFF = SKILL / "scripts" / "flow" / "fix_signoff.sh"
GOOD = {"id": "s", "config_edits": {"CORE_UTILIZATION": "40"}, "rerun_from": "floorplan",
        "recheck": "drc", "auto_apply": True}


@pytest.mark.parametrize("change,needle", [
    ({"recheck": None}, "recheck"), ({"recheck": "orfs_stage"}, "recheck"), ({"id": ""}, "id"),
    ({"rerun_from": "signoff"}, "rerun_from"), ({"config_edits": "x"}, "config_edits"),
    ({"sdc_edits": []}, "sdc_edits"),
])
def test_the_contract_names_every_missing_or_invalid_field(change, needle):
    st = {**GOOD, **change}
    assert any(needle in e for e in C.contract_errors(st))


def test_a_valid_candidate_and_a_no_rerun_candidate_pass():
    assert C.contract_errors(GOOD) == []
    assert C.contract_errors({**GOOD, "rerun_from": None, "config_edits": {}}) == []   # recheck-only
    assert C.contract_errors({k: v for k, v in GOOD.items() if k != "recheck"}) != []


@pytest.mark.parametrize("evidence,expected", [
    ("drc", "drc"), ("lvs", "lvs"), ("timing", "timing"), ("orfs_stage", "route"), ("route", "route"),
    (None, None), ("", None), ("UNKNOWN", None), ("place", None)])
def test_recheck_maps_only_from_recorded_evidence(evidence, expected):
    assert C.recheck_from_evidence(evidence) == expected


def test_technical_failures_are_appended_and_read_back(tmp_path):
    (tmp_path / "reports").mkdir()
    assert TF.load(tmp_path) == []
    TF.record(tmp_path, "invalid_candidate", source="recipe", detail="missing recheck", check="drc")
    TF.record(tmp_path, "diagnose_crash", source="fix_signoff", detail="rc 1", check="route")
    rows = TF.load(tmp_path)
    assert [r["kind"] for r in rows] == ["invalid_candidate", "diagnose_crash"]
    with pytest.raises(ValueError):
        TF.record(tmp_path, "something_else", source="x", detail="y")


# ---------------------------------------------------------------- diagnose --next

def _project(tmp_path):
    p = tmp_path / "project"
    (p / "constraints").mkdir(parents=True)
    (p / "reports").mkdir()
    (p / "constraints/config.mk").write_text("export CORE_UTILIZATION = 60\nexport PLATFORM = sky130hd\n")
    (p / "reports/drc.json").write_text(json.dumps({"status": "fail", "total_violations": 4,
                                                    "categories": {"m3.2": {"count": 4}}}))
    return p


def _catalogue(monkeypatch, strategies):
    monkeypatch.setattr(D, "build_plan", lambda *a, **k: {
        "check": "drc", "status": "fail", "strategies": list(strategies), "residual_reason": None})
    monkeypatch.setattr(D, "_current_situation", lambda *a, **k: {"check": "drc", "violation_class": "m3.2"})


def test_a_tehm_candidate_without_recheck_is_a_technical_failure_not_executed(tmp_path, monkeypatch, capsys):
    project = _project(tmp_path)
    monkeypatch.setenv("R2G_MEMORY_BACKEND", "tehm")
    monkeypatch.delenv("R2G_RECIPE_FILE", raising=False)
    bad = {k: v for k, v in GOOD.items() if k != "recheck"}
    bad["id"] = "tehm_bad"
    monkeypatch.setitem(sys.modules, "runtime_router", SimpleNamespace(
        signoff_strategies=lambda **kw: [bad], signoff_vetoes=lambda **kw: {}))
    _catalogue(monkeypatch, [{**GOOD, "id": "catalogue_ok"}])
    assert D.main([str(project), "--check", "drc", "--next"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("TECHNICAL\tinvalid_candidate\t") and "tehm_bad" in out


def test_a_crashed_tehm_consultation_is_a_technical_failure_not_a_silent_skip(tmp_path, monkeypatch, capsys):
    project = _project(tmp_path)
    monkeypatch.setenv("R2G_MEMORY_BACKEND", "tehm")
    monkeypatch.delenv("R2G_RECIPE_FILE", raising=False)

    def boom(**kw):
        raise KeyError("recheck")
    monkeypatch.setitem(sys.modules, "runtime_router", SimpleNamespace(
        signoff_strategies=boom, signoff_vetoes=lambda **kw: {}))
    _catalogue(monkeypatch, [{**GOOD, "id": "catalogue_ok"}])
    assert D.main([str(project), "--check", "drc", "--next"]) == 0
    assert capsys.readouterr().out.startswith("TECHNICAL\tconsultation_crash\t")


def test_no_memory_match_lets_the_catalogue_continue(tmp_path, monkeypatch, capsys):
    project = _project(tmp_path)
    monkeypatch.setenv("R2G_MEMORY_BACKEND", "tehm")
    monkeypatch.delenv("R2G_RECIPE_FILE", raising=False)
    monkeypatch.setitem(sys.modules, "runtime_router", SimpleNamespace(
        signoff_strategies=lambda **kw: [], signoff_vetoes=lambda **kw: {}))
    _catalogue(monkeypatch, [{**GOOD, "id": "catalogue_ok"}])
    assert D.main([str(project), "--check", "drc", "--next"]) == 0
    assert capsys.readouterr().out.strip() == "catalogue_ok\tfloorplan\tdrc"


def test_an_invalid_catalogue_strategy_is_also_a_technical_failure(tmp_path, monkeypatch, capsys):
    project = _project(tmp_path)
    monkeypatch.setenv("R2G_MEMORY_BACKEND", "none")
    monkeypatch.delenv("R2G_RECIPE_FILE", raising=False)
    _catalogue(monkeypatch, [{**GOOD, "id": "catalogue_bad", "recheck": None}])
    assert D.main([str(project), "--check", "drc", "--next"]) == 0
    assert capsys.readouterr().out.startswith("TECHNICAL\tinvalid_candidate\t")


# ---------------------------------------------------------------- fix_signoff propagation

def _stub(path, body):
    path.write_text("#!/usr/bin/env bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _fix(tmp_path, diagnose_body):
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints").mkdir()
    (proj / "constraints/config.mk").write_text("export DESIGN_NAME = demo\nexport PLATFORM = sky130hd\n"
                                                "export CORE_UTILIZATION = 25\n")
    (proj / "reports/route.json").write_text(json.dumps({"status": "fail", "total_violations": 9}))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls.txt"
    _stub(bindir / "diagnose.py", 'echo "$*" >> %s\n%s' % (calls, diagnose_body))
    _stub(bindir / "noop.sh", "exit 0")
    _stub(bindir / "extract_route.py", 'python3 - "$@" <<\'PY\'\nimport json,sys\n'
          'open(sys.argv[2],"w").write(json.dumps({"status":"fail","total_violations":9}))\nPY')
    env = dict(os.environ, R2G_DIAGNOSE=str(bindir / "diagnose.py"), R2G_RUN_ORFS=str(bindir / "noop.sh"),
               R2G_EXTRACT_ROUTE=str(bindir / "extract_route.py"))
    r = subprocess.run(["bash", str(FIX_SIGNOFF), str(proj), "sky130hd", "--check", "route", "--max-iters", "2"],
                       env=env, capture_output=True, text=True)
    return r, proj, (calls.read_text() if calls.exists() else "")


def test_fix_signoff_surfaces_an_invalid_candidate_and_applies_nothing(tmp_path):
    r, proj, calls = _fix(tmp_path, 'if [[ "$*" == *"--next"* ]]; then echo -e "TECHNICAL\\tinvalid_candidate\\trecipe_x: missing recheck"; fi')
    assert r.returncode == C.TECHNICAL_EXIT, r.stdout + r.stderr
    assert "--apply" not in calls
    rows = TF.load(proj)
    assert len(rows) == 1 and rows[0]["kind"] == "invalid_candidate" and rows[0]["check"] == "route"


def test_fix_signoff_surfaces_a_diagnose_crash_instead_of_an_ordinary_stop(tmp_path):
    r, proj, calls = _fix(tmp_path, 'if [[ "$*" == *"--next"* ]]; then echo "KeyError: recheck" >&2; exit 1; fi')
    assert r.returncode == C.TECHNICAL_EXIT, r.stdout + r.stderr
    assert "--apply" not in calls
    assert [x["kind"] for x in TF.load(proj)] == ["diagnose_crash"]


def test_an_ordinary_stop_is_still_an_ordinary_residual(tmp_path):
    r, proj, calls = _fix(tmp_path, 'if [[ "$*" == *"--next"* ]]; then echo -e "STOP\\tfail\\tno_auto_strategy"; fi')
    assert r.returncode == 2
    assert TF.load(proj) == []


def test_the_engineer_loop_stops_a_design_on_a_technical_failure(tmp_path):
    sys.path.insert(0, str(SKILL / "scripts" / "loop"))
    import engineer_loop as E
    states = []
    led = SimpleNamespace(set_state=lambda design, state, **kw: states.append((design, state, kw.get("reason"))))
    proj = tmp_path / "p"
    (proj / "reports").mkdir(parents=True)
    entry = {"project_path": str(proj), "design": "d"}
    assert E._technical_stop(led, None, entry, 0) is False and states == []
    assert E._technical_stop(led, None, entry, 2) is False                      # ordinary residual
    assert E._technical_stop(led, None, entry, C.TECHNICAL_EXIT) is True
    TF.record(proj, "diagnose_crash", source="fix_signoff", detail="rc 1")
    assert E._technical_stop(led, None, entry, 0) is True                       # marker alone stops too
    assert states[-1] == ("d", "escalated", "technical_failure")


# ---------------------------------------------------------------- presence, not just type (review 2026-10-17)

@pytest.mark.parametrize("field", C.REQUIRED_FIELDS)
def test_removing_any_required_field_is_reported(field):
    st = {k: v for k, v in GOOD.items() if k != field}
    assert any(field in e and "missing" in e for e in C.contract_errors(st)), C.contract_errors(st)


def test_an_incomplete_candidate_is_never_valid():
    assert C.contract_errors({"id": "incomplete", "recheck": "drc"})


@pytest.mark.parametrize("st,ok", [
    ({**GOOD, "config_edits": {}}, True),                                  # empty edits allowed
    ({**GOOD, "rerun_from": None, "config_edits": {}}, True),              # recheck-only (lvs_resolve_unknown)
    ({**GOOD, "rerun_from": "", "config_edits": {}}, True),
    ({**GOOD, "rerun_from": None}, False),                                 # an edit with no rerun cannot be measured
    ({**GOOD, "rerun_from": None, "config_edits": {}, "sdc_edits": {"x": "1"}}, False),
    ({**GOOD, "sdc_edits": {}}, True),                                     # optional, may be empty
])
def test_which_fields_may_be_empty(st, ok):
    assert (C.contract_errors(st) == []) is ok, C.contract_errors(st)


@pytest.mark.parametrize("field", C.REQUIRED_FIELDS)
@pytest.mark.parametrize("producer", ["tehm", "catalogue"])
def test_the_consumer_refuses_a_candidate_missing_each_required_field(tmp_path, monkeypatch, capsys, field, producer):
    project = _project(tmp_path)
    monkeypatch.delenv("R2G_RECIPE_FILE", raising=False)
    broken = {k: v for k, v in GOOD.items() if k != field}
    broken.setdefault("id", "broken_" + producer)       # keep it selectable when id itself is the removed field
    if field == "id":
        broken.pop("id")
    if producer == "tehm":
        monkeypatch.setenv("R2G_MEMORY_BACKEND", "tehm")
        monkeypatch.setitem(sys.modules, "runtime_router", SimpleNamespace(
            signoff_strategies=lambda **kw: [broken], signoff_vetoes=lambda **kw: {}))
        _catalogue(monkeypatch, [{**GOOD, "id": "catalogue_ok"}])
    else:
        monkeypatch.setenv("R2G_MEMORY_BACKEND", "none")
        _catalogue(monkeypatch, [broken])
    rc = D.main([str(project), "--check", "drc", "--next"])
    out = capsys.readouterr().out
    assert rc == 0 and out.startswith("TECHNICAL\tinvalid_candidate\t"), out


# ---------------------------------------------------------------- place-abort semantics (directed)

def _place_aborted(tmp_path):
    proj = tmp_path / "proj"
    run = proj / "backend" / "RUN_2026-01-01_00-00-00_1_a"
    run.mkdir(parents=True)
    (proj / "reports").mkdir()
    (proj / "constraints").mkdir()
    (proj / "constraints/config.mk").write_text("export DESIGN_NAME = demo\nexport PLATFORM = sky130hd\n"
                                                "export CORE_UTILIZATION = 70\n")
    (run / "stage_log.jsonl").write_text("".join(json.dumps(r) + "\n" for r in (
        {"stage": "synth", "status": 0}, {"stage": "floorplan", "status": 0}, {"stage": "place", "status": 2})))
    (run / "flow.log").write_text("[ERROR FLW-0024] Place density exceeds 1.0\n")
    return proj, run


def test_a_place_abort_is_not_a_route_symptom_for_the_route_checker(tmp_path):
    proj, _ = _place_aborted(tmp_path)
    out = subprocess.run([sys.executable, str(SKILL / "scripts/extract/extract_route.py"), str(proj),
                          str(proj / "reports/route.json")], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    route = json.loads((proj / "reports/route.json").read_text())
    assert route["status"] == "unknown" and route["total_violations"] is None


def test_a_place_keyed_recipe_is_never_selected_by_the_route_recheck_path(tmp_path):
    import situation as SIT
    sit_route = SIT.canonical("route", error_code="FLW-0024", platform="sky130hd", die_mode="auto", util=70)
    sys.path.insert(0, str(SKILL.parents[1] / "memory/scripts"))
    import t2a_adapters as A
    place = {k: sit_route.get(k) for k in A.RECIPE_KEYS}
    place["violation_class"] = "place"
    path = tmp_path / "RECIPE.json"
    path.write_text(json.dumps({"status": "FROZEN", "recipes": [{
        "id": "recipe_place", "situation": place, "config_edits": {"CORE_UTILIZATION": "55"},
        "rerun_from": "floorplan", "unit_ids": ["u"], "first_time": ""}]}))
    assert sit_route["violation_class"] == "route"
    assert A.recipe_strategies(path, sit_route, {"CORE_UTILIZATION": "70"}) == []


def test_a_persisting_place_abort_is_never_judged_cleared_by_a_route_recheck(tmp_path):
    proj, run = _place_aborted(tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _stub(bindir / "diagnose.py",
          'if [[ "$*" == *"--next"* ]]; then\n'
          '  if [[ -f %s/applied ]]; then echo -e "STOP\\tunknown\\tdone"; else echo -e "recipe_place\\tfloorplan\\troute"; fi\n'
          'elif [[ "$*" == *"--apply"* ]]; then touch %s/applied;\n'
          '  echo "{\\"applied\\":\\"recipe_place\\",\\"config_edits\\":{\\"CORE_UTILIZATION\\":\\"55\\"}}"; fi'
          % (tmp_path, tmp_path))
    _stub(bindir / "noop.sh", "exit 0")                  # the rerun leaves the place abort in place
    env = dict(os.environ, R2G_DIAGNOSE=str(bindir / "diagnose.py"), R2G_RUN_ORFS=str(bindir / "noop.sh"),
               R2G_EXTRACT_ROUTE=str(SKILL / "scripts/extract/extract_route.py"))
    r = subprocess.run(["bash", str(FIX_SIGNOFF), str(proj), "sky130hd", "--check", "route", "--max-iters", "2"],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 2, r.stdout + r.stderr       # a residual, never "clean"
    rows = [json.loads(l) for l in (proj / "reports/fix_log.jsonl").read_text().splitlines() if l.strip()]
    assert rows[0]["strategy"] == "recipe_place" and rows[0]["verdict"] == "recheck_unparsed", rows
    assert not any(x.get("verdict") == "cleared" for x in rows), rows


@pytest.mark.parametrize("route_rpt,verdict,rc", [
    ("", "cleared", 0),                                          # place abort gone, route clean
    ("violation type: Short\n" * 3, "applied", 2)])             # place abort gone, route residual
def test_a_route_recheck_measures_a_place_abort_only_once_the_flow_reaches_route(tmp_path, route_rpt, verdict, rc):
    """orfs_stage/place -> recheck route: a numeric route count exists only when the rerun
    logged a route stage, i.e. place passed. So "cleared" implies the place abort is gone."""
    proj, _ = _place_aborted(tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _stub(bindir / "diagnose.py",
          'if [[ "$*" == *"--next"* ]]; then\n'
          '  if [[ -f %s/applied ]]; then echo -e "STOP\\tunknown\\tdone"; else echo -e "recipe_place\\tfloorplan\\troute"; fi\n'
          'elif [[ "$*" == *"--apply"* ]]; then touch %s/applied; echo "{}"; fi' % (tmp_path, tmp_path))
    rerun = proj / "backend" / "RUN_2026-01-02_00-00-00_1_b"
    _stub(bindir / "rerun.sh",
          'mkdir -p %s/reports_orfs\n'
          'printf \'{"stage":"place","status":0}\\n{"stage":"cts","status":0}\\n{"stage":"route","status":0}\\n\' > %s/stage_log.jsonl\n'
          'printf %%s %s > %s/reports_orfs/5_route_drc.rpt' % (rerun, rerun, json.dumps(route_rpt), rerun))
    env = dict(os.environ, R2G_DIAGNOSE=str(bindir / "diagnose.py"), R2G_RUN_ORFS=str(bindir / "rerun.sh"),
               R2G_EXTRACT_ROUTE=str(SKILL / "scripts/extract/extract_route.py"))
    r = subprocess.run(["bash", str(FIX_SIGNOFF), str(proj), "sky130hd", "--check", "route", "--max-iters", "2"],
                       env=env, capture_output=True, text=True)
    rows = [json.loads(l) for l in (proj / "reports/fix_log.jsonl").read_text().splitlines() if l.strip()]
    assert rows[0]["strategy"] == "recipe_place" and rows[0]["before"] is None, rows
    assert rows[0]["verdict"] == verdict and r.returncode == rc, (rows, r.stdout + r.stderr)
