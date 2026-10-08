"""Baseline repairs of 2026-10-05 (r2g without memory must not stop where an engineer would not):

1. FLW-0024 on an already auto-sized die: the live loop lowers CORE_UTILIZATION (x0.6, at most
   twice) instead of escalating after one flow.
2. Global-route congestion: route_relief is a ladder (x0.8 for an abort, -8 for a completed
   route with violations) and fix_signoff continues past a rerun that is still congested.
3. Local-interconnect spacing DRC (li/licon/mcon): cell_pad_relief_2 -> _4 (one id per rung, so a
   rolled-back rung is not retried), before density_relief when the local class dominates.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts" / "reports"))
sys.path.insert(0, str(SKILL / "scripts" / "loop"))
sys.path.insert(0, str(SKILL / "knowledge"))
import diagnose_signoff_fix as dsf  # noqa: E402
import engineer_loop  # noqa: E402

FIX_SIGNOFF = SKILL / "scripts" / "flow" / "fix_signoff.sh"


# --- 2. route ladder (diagnose) --------------------------------------------------------------
def test_route_abort_steps_x08_and_repeats():
    cfg = {"CORE_UTILIZATION": "79"}
    p = dsf._route_plan({"status": "fail", "total_violations": None}, cfg, {"route_relief"})
    s = p["strategies"][0]
    assert s["config_edits"] == {"CORE_UTILIZATION": "63"} and s.get("repeatable")
    nxt = dsf._route_plan({"status": "fail", "total_violations": None},
                          {"CORE_UTILIZATION": "63"}, {"route_relief"})["strategies"]
    assert nxt[0]["config_edits"] == {"CORE_UTILIZATION": "50"}


def test_completed_route_keeps_gentle_step_and_timeout_jumps_once():
    done = dsf._route_plan({"status": "fail", "total_violations": 40}, {"CORE_UTILIZATION": "25"}, set())
    assert done["strategies"][0]["config_edits"] == {"CORE_UTILIZATION": "17"}
    t = dsf._route_plan({"status": "timeout", "total_violations": 9}, {"CORE_UTILIZATION": "25"}, set())
    assert t["strategies"][0]["config_edits"] == {"CORE_UTILIZATION": str(dsf._UTIL_FLOOR)}
    assert not t["strategies"][0].get("repeatable")
    assert dsf._route_plan({"status": "timeout", "total_violations": 9},
                           {"CORE_UTILIZATION": "25"}, {"route_relief"})["strategies"] == []


# --- 2. route ladder (fix_signoff continues past a still-congested rerun) -------------------
def _stub(path: Path, body: str):
    path.write_text("#!/usr/bin/env bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_fix_signoff_continues_when_the_rerun_is_still_congested(tmp_path):
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = sky130hd\nexport CORE_UTILIZATION = 79\n")
    (proj / "reports" / "route.json").write_text(json.dumps({"status": "fail", "total_violations": None}))
    bindir, state = tmp_path / "bin", tmp_path / "state"
    bindir.mkdir()
    state.mkdir()
    _stub(bindir / "diagnose.py",
          f'if [[ "$*" == *"--next"* ]]; then\n'
          f'  if [[ -f {state}/clean ]]; then echo -e "STOP\\tclean\\tdone";\n'
          f'  else echo -e "route_relief\\tfloorplan\\troute"; fi\n'
          f'elif [[ "$*" == *"--apply"* ]]; then\n'
          f'  echo "{{\\"applied\\":\\"route_relief\\",\\"config_edits\\":{{\\"CORE_UTILIZATION\\":\\"63\\"}}}}"; fi')
    # first rerun aborts again (still congested), the second routes
    _stub(bindir / "run_orfs.sh",
          f'n=$(cat {state}/runs 2>/dev/null || echo 0); n=$((n+1)); echo $n > {state}/runs\n'
          f'[[ $n -ge 2 ]] && {{ touch {state}/clean; exit 0; }}; exit 2')
    _stub(bindir / "extract_route.py",
          f'python3 - "$@" <<\'PY\'\nimport json,os,sys\n'
          f'clean=os.path.exists("{state}/clean")\n'
          f'open(sys.argv[2],"w").write(json.dumps({{"status":"clean","total_violations":0}} if clean '
          f'else {{"status":"fail","total_violations":None}}))\nPY')
    env = dict(os.environ, R2G_DIAGNOSE=str(bindir / "diagnose.py"), R2G_RUN_ORFS=str(bindir / "run_orfs.sh"),
               R2G_EXTRACT_ROUTE=str(bindir / "extract_route.py"))
    r = subprocess.run(["bash", str(FIX_SIGNOFF), str(proj), "sky130hd", "--check", "route", "--max-iters", "4"],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "continuing the relief ladder" in r.stderr
    rows = [json.loads(l) for l in (proj / "reports" / "fix_log.jsonl").read_text().splitlines() if l.strip()]
    assert any(str(x.get("verdict", x.get("result", ""))) == "rerun_failed_still_congested"
               or "rerun_failed_still_congested" in json.dumps(x) for x in rows)


# --- 3. cell_pad_relief ----------------------------------------------------------------------
def _drc(cats):
    return {"status": "fail", "total_violations": sum(cats.values()),
            "categories": {k: {"count": v} for k, v in cats.items()}}


def test_local_interconnect_drc_pads_cells_first_then_steps_and_stops():
    plan = lambda cfg, ex=frozenset(): dsf._drc_plan(_drc({"li.3": 6}), {"CORE_UTILIZATION": "13", **cfg}, set(ex))
    s = plan({})["strategies"][0]
    assert s["id"] == "cell_pad_relief_2" and s["rerun_from"] == "floorplan"
    assert s["config_edits"]["CELL_PAD_IN_SITES_DETAIL_PLACEMENT"] == "2"
    # accepted rung in the config -> next rung
    assert plan({"CELL_PAD_IN_SITES_DETAIL_PLACEMENT": "2"}, {"cell_pad_relief_2"})["strategies"][0]["id"] == "cell_pad_relief_4"
    # rejected rung (rolled back: no padding in the config) stays excluded -> next rung, never a retry
    assert plan({}, {"cell_pad_relief_2"})["strategies"][0]["id"] == "cell_pad_relief_4"
    done = plan({"CELL_PAD_IN_SITES_DETAIL_PLACEMENT": "4"})["strategies"]
    assert not any(x["id"].startswith("cell_pad_relief") for x in done)


def test_completed_route_step_is_not_retried_after_rollback():
    p = dsf._route_plan({"status": "fail", "total_violations": 40}, {"CORE_UTILIZATION": "25"}, {"route_relief"})
    assert p["strategies"] == []


def test_routing_dominant_drc_keeps_density_relief_first_and_metal_only_gets_no_padding():
    ids = [s["id"] for s in dsf._drc_plan(_drc({"m3.2": 108, "li.3": 8}), {"CORE_UTILIZATION": "19"}, set())["strategies"]]
    assert ids == ["density_relief", "cell_pad_relief_2"]
    ids = [s["id"] for s in dsf._drc_plan(_drc({"m3.2": 12}), {"CORE_UTILIZATION": "19"}, set())["strategies"]]
    assert not any(i.startswith("cell_pad_relief") for i in ids)


# --- 1. live FLW-0024 relief on an auto-sized die --------------------------------------------
def test_flw0024_on_auto_sized_die_lowers_util_twice_then_escalates(tmp_path, monkeypatch):
    import knowledge_db
    conn = knowledge_db.connect(tmp_path / "k.sqlite")
    knowledge_db.ensure_schema(conn)
    proj = tmp_path / "dense"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text("export DESIGN_NAME = d\nexport CORE_UTILIZATION = 89\n")
    led = engineer_loop.Ledger(tmp_path / "l.jsonl")
    led.add({"design": "dense", "project_path": str(proj), "platform": "sky130hd"})
    flows, recorded = [], []
    monkeypatch.setattr(engineer_loop, "_run_flow", lambda e: flows.append(1) or 1)   # always aborts
    monkeypatch.setattr(engineer_loop, "_ingest", lambda e: None)
    monkeypatch.setattr(engineer_loop, "_fail_stage", lambda e: "place")
    monkeypatch.setattr(engineer_loop, "_is_flw0024", lambda e: True)
    monkeypatch.setattr(engineer_loop, "_is_ppl0024", lambda e: False)
    monkeypatch.setattr(engineer_loop, "_memory_recovery", lambda *a, **k: None)
    monkeypatch.setattr(engineer_loop, "_record_resize_fix",
                        lambda e, *, cleared, **_kw: recorded.append(cleared))
    out = engineer_loop.process_one(led, led.pending()[0], conn=conn)
    assert out == "escalated" and len(flows) == 3 and recorded == [False, False]
    assert "CORE_UTILIZATION = 32" in (proj / "constraints" / "config.mk").read_text()   # 89 -> 53 -> 32


def test_route_layer_relief_first_on_sparse_die_and_after_the_ladder_on_dense():
    abort = {"status": "fail", "total_violations": None}
    sparse = [x["id"] for x in dsf._route_plan(abort, {"CORE_UTILIZATION": "16"}, set())["strategies"]]
    assert sparse[0] == "route_layer_relief"
    dense = [x["id"] for x in dsf._route_plan(abort, {"CORE_UTILIZATION": "79"}, set())["strategies"]]
    assert dense == ["route_relief", "route_layer_relief"]
    floor = dsf._route_plan(abort, {"CORE_UTILIZATION": str(dsf._UTIL_FLOOR)}, set())["strategies"]
    assert [x["id"] for x in floor] == ["route_layer_relief"]
    s = floor[0]
    assert s["config_edits"] == {"ROUTING_LAYER_ADJUSTMENT": "0.10"} and s["rerun_from"] == "route"
    done = dsf._route_plan(abort, {"CORE_UTILIZATION": str(dsf._UTIL_FLOOR)}, {"route_layer_relief"})
    assert done["strategies"] == [] and done["status"] == "residual"


def test_apply_sees_the_same_exclusions_as_next(tmp_path):
    """A rolled-back rung is excluded at --next; --apply must rebuild the same plan (R-A2)."""
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = sky130hs\nexport CORE_UTILIZATION = 8\n")
    (proj / "reports" / "route.json").write_text(json.dumps({"status": "fail", "total_violations": None}))
    bindir, state = tmp_path / "bin", tmp_path / "state"
    bindir.mkdir()
    state.mkdir()
    _stub(bindir / "diagnose.py",
          f'if [[ "$*" == *"--next"* ]]; then\n'
          f'  if [[ -f {state}/applied ]]; then echo -e "STOP\\tclean\\tdone";\n'
          f'  else echo -e "route_layer_relief\\troute\\troute"; fi\n'
          f'elif [[ "$*" == *"--apply"* ]]; then\n'
          f'  [[ "$*" == *"--exclude"* ]] || {{ echo "apply without --exclude" >&2; exit 2; }}\n'
          f'  touch {state}/applied\n'
          f'  echo "{{\\"applied\\":\\"route_layer_relief\\",\\"config_edits\\":{{}}}}"; fi')
    _stub(bindir / "run_orfs.sh", "exit 0")
    _stub(bindir / "extract_route.py",
          'python3 - "$@" <<\'PY\'\nimport json,sys\nopen(sys.argv[2],"w").write(json.dumps({"status":"clean","total_violations":0}))\nPY')
    env = dict(os.environ, R2G_DIAGNOSE=str(bindir / "diagnose.py"), R2G_RUN_ORFS=str(bindir / "run_orfs.sh"),
               R2G_EXTRACT_ROUTE=str(bindir / "extract_route.py"))
    r = subprocess.run(["bash", str(FIX_SIGNOFF), str(proj), "sky130hs", "--check", "route", "--max-iters", "2"],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0 and "apply without --exclude" not in r.stderr, r.stdout + r.stderr


def test_real_diagnose_next_and_apply_agree_after_a_rolled_back_rung(tmp_path):
    """End to end on the real CLI: li.3 residue, padding rung 2 already tried and rolled back."""
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints").mkdir()
    cfg = proj / "constraints" / "config.mk"
    cfg.write_text("export DESIGN_NAME = demo\nexport PLATFORM = sky130hs\nexport CORE_UTILIZATION = 13\n")
    (proj / "reports" / "drc.json").write_text(json.dumps(_drc({"li.3": 6})))
    diag = [sys.executable, str(SKILL / "scripts" / "reports" / "diagnose_signoff_fix.py"), str(proj),
            "--check", "drc", "--exclude", "cell_pad_relief_2"]
    env = dict(os.environ, R2G_MEMORY_BACKEND="none", R2G_KNOWLEDGE_DB=str(tmp_path / "k.sqlite"))
    nxt = subprocess.run(diag + ["--next"], capture_output=True, text=True, env=env)
    assert nxt.stdout.split("\t")[0] == "cell_pad_relief_4", nxt.stdout + nxt.stderr
    app = subprocess.run(diag + ["--apply", "cell_pad_relief_4"], capture_output=True, text=True, env=env)
    assert app.returncode == 0, app.stdout + app.stderr
    assert "CELL_PAD_IN_SITES_DETAIL_PLACEMENT = 4" in cfg.read_text()


def test_real_diagnose_drv_ladder_skips_a_rolled_back_rung(tmp_path):
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints").mkdir()
    cfg = proj / "constraints" / "config.mk"
    cfg.write_text("export DESIGN_NAME = demo\nexport PLATFORM = sky130hd\nexport CORE_UTILIZATION = 40\n")
    (proj / "reports" / "route.json").write_text(json.dumps({"status": "clean", "total_violations": 0}))
    (proj / "reports" / "timing_check.json").write_text(json.dumps(
        {"tier": "clean", "wns": 0.1, "wns_ns": 0.1,
         "drv": {"max_slew_violations": 2, "max_cap_violations": 1, "total": 3, "status": "fail"}}))
    diag = [sys.executable, str(SKILL / "scripts" / "reports" / "diagnose_signoff_fix.py"), str(proj),
            "--check", "timing", "--exclude", "drv_route_margin_10"]
    env = dict(os.environ, R2G_MEMORY_BACKEND="none", R2G_KNOWLEDGE_DB=str(tmp_path / "k.sqlite"))
    nxt = subprocess.run(diag + ["--next"], capture_output=True, text=True, env=env)
    assert nxt.stdout.split("\t")[0] == "drv_route_margin_20", nxt.stdout + nxt.stderr
    app = subprocess.run(diag + ["--apply", "drv_route_margin_20"], capture_output=True, text=True, env=env)
    assert app.returncode == 0, app.stdout + app.stderr
    assert "R2G_ROUTE_SLEW_MARGIN = 20" in cfg.read_text()


# --- memory containment (R-A3): a failed memory step never takes away the catalogue's repair ---
def _containment_project(tmp_path, steps):
    """steps: strategy ids diagnose offers in order; a run_orfs stub fails for ids in FAIL, and the
    extract stub reports clean once a strategy named 'cat_fix' has run."""
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = sky130hs\nexport CORE_UTILIZATION = 11\n")
    (proj / "reports" / "route.json").write_text(json.dumps({"status": "fail", "total_violations": None}))
    bindir, state = tmp_path / "bin", tmp_path / "state"
    bindir.mkdir()
    state.mkdir()
    queue = " ".join(steps)
    # --next: next untried id from the queue (memory ids hidden while R2G_MEMORY_BACKEND=none);
    # every call's backend is recorded
    _stub(bindir / "diagnose.py",
          f'echo "$R2G_MEMORY_BACKEND" >> {state}/backends\n'
          f'if [[ "$*" == *"--next"* ]]; then\n'
          f'  [[ -f {state}/clean ]] && {{ echo -e "STOP\\tclean\\tdone"; exit 0; }}\n'
          f'  for s in {queue}; do\n'
          f'    [[ "$s" == tehm_* && "$R2G_MEMORY_BACKEND" == none ]] && continue\n'
          f'    grep -qx "$s" {state}/tried 2>/dev/null && continue\n'
          f'    echo "$s" >> {state}/tried; echo -e "$s\\tfloorplan\\troute"; exit 0\n'
          f'  done; echo -e "STOP\\tresidual\\tnone"\n'
          f'elif [[ "$*" == *"--apply"* ]]; then\n'
          f'  s=$(tail -1 {state}/tried); echo "$s" > {state}/current\n'
          f'  [[ "$s" == tehm_* ]] && echo "export MEMORY_EDIT_$s = 1" >> {proj}/constraints/config.mk\n'
          f'  echo "{{\\"applied\\":\\"$s\\",\\"config_edits\\":{{}}}}"; fi')
    _stub(bindir / "run_orfs.sh",
          f's=$(cat {state}/current); [[ "$s" == tehm_fail* ]] && exit 2\n'
          f'[[ "$s" == cat_fix ]] && touch {state}/clean; exit 0')
    _stub(bindir / "extract_route.py",
          f'python3 - "$@" <<\'PY\'\nimport json,os,sys\n'
          f'clean=os.path.exists("{state}/clean")\n'
          f'open(sys.argv[2],"w").write(json.dumps({{"status":"clean","total_violations":0}} if clean '
          f'else {{"status":"fail","total_violations":None}}))\nPY')
    env = dict(os.environ, R2G_DIAGNOSE=str(bindir / "diagnose.py"), R2G_RUN_ORFS=str(bindir / "run_orfs.sh"),
               R2G_EXTRACT_ROUTE=str(bindir / "extract_route.py"), R2G_MEMORY_BACKEND="tehm")
    r = subprocess.run(["bash", str(FIX_SIGNOFF), str(proj), "sky130hs", "--check", "route", "--max-iters", "6"],
                       env=env, capture_output=True, text=True)
    return r, state


def test_failed_memory_rerun_is_rolled_back_and_the_catalogue_still_runs(tmp_path):
    r, state = _containment_project(tmp_path, ["tehm_fail_a", "cat_fix"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "aborting this check" not in r.stderr and "memory step tehm_fail_a failed" in r.stdout
    assert (state / "clean").exists()
    # the failed memory step's config edit is rolled back: the catalogue runs on the pre-memory config
    assert "MEMORY_EDIT" not in (tmp_path / "proj" / "constraints" / "config.mk").read_text()


def test_memory_is_suspended_after_two_failed_steps(tmp_path):
    r, state = _containment_project(tmp_path, ["tehm_fail_a", "tehm_fail_b", "tehm_fail_c", "cat_fix"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "memory suspended for the rest of this fix session" in r.stdout
    tried = (state / "tried").read_text().split()
    assert "tehm_fail_c" not in tried and tried[-1] == "cat_fix"       # third memory step never offered
    assert (state / "backends").read_text().split()[-1] == "none"


def test_catalogue_only_session_is_unchanged(tmp_path):
    r, state = _containment_project(tmp_path, ["cat_fix"])
    assert r.returncode == 0 and "memory" not in r.stdout
