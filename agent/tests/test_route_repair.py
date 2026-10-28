import json
import os
from pathlib import Path
import subprocess

import pytest

from r2g_agent import diagnosis, pipeline, project
from r2g_agent.jobs import JobStore


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if isinstance(value, dict) else value)


def test_diagnosis_loads_flow_environment_and_quotes_paths(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts with spaces"
    write(scripts / "flow/_env.sh", "export R2G_TEST_READER=discovered\n")
    write(scripts / "reports/diagnose_signoff_fix.py",
          "import os,sys,json\nprint(json.dumps([os.environ['R2G_TEST_READER'],sys.argv[1:]]))\n")
    monkeypatch.setattr(diagnosis, "signoff_scripts", lambda: scripts)
    target = tmp_path / "literal $(touch injected); project"
    output = tmp_path / "selection file"
    for dest in (None, output):
        p = subprocess.run(diagnosis.command(target, "route", "--next", dest),
                           env={**os.environ, "PATH": os.environ["PATH"]},
                           cwd=tmp_path, capture_output=True, text=True, check=True)
        got = json.loads(output.read_text() if dest else p.stdout)
        assert got == ["discovered", [str(target), "--check", "route", "--next"]]
    assert not (tmp_path / "injected").exists()


@pytest.mark.parametrize("outcome", ["clean", "no_recipe", "early_failure", "failed",
                                     "stale", "no_gds", "changed_rtl", "changed_sdc",
                                     "bad_route", "failed_metadata"])
def test_route_repair_requires_fresh_complete_flow(tmp_path, monkeypatch, outcome):
    src = tmp_path / "src"
    write(src / "top.v", "module top(input clk, output q); assign q=clk; endmodule\n")
    pdir = tmp_path / "project"
    project.create(src, pdir, "sky130hd", top="top")
    before = pdir / "backend/RUN_001"
    write(before / "run-meta.json", {"make_status": 2})
    store = JobStore()
    jid = store.create_job("route", {}, [{"name": "top"}])
    pipe = pipeline.Pipeline(store, ingest=False)
    calls = []

    def run(_jid, _name, stage, cmd, actual, **kwargs):
        calls.append((stage, cmd, kwargs))
        if any(str(x).endswith("extract_route.py") for x in cmd):
            write(pdir / "reports/route.json", {"status": "unknown" if outcome == "early_failure" else "fail",
                                               "backend_run": before.name})
        elif "r2g-diagnose" in cmd:
            write(pdir / "reports/route_repair_selection.tsv",
                  "STOP\tfail\tno_auto_strategy\n" if outcome == "no_recipe" else
                  "fixed_area_route_relief\tfloorplan\troute\n")
        else:
            assert stage == "repair" and cmd[-4:] == ["--check", "route", "--max-iters", "1"]
            assert "--rank-first" not in cmd and "--force" not in cmd
            after = before if outcome == "stale" else pdir / "backend/RUN_002"
            write(after / "run-meta.json", {"make_status": 2 if outcome == "failed_metadata" else 0})
            if outcome != "no_gds":
                write(after / "final/top.gds", "synthetic layout for dispatch test only")
            write(pdir / "reports/route.json", {"status": "fail" if outcome == "bad_route" else "clean",
                  "backend_run": after.name, "completed": True})
            if outcome == "changed_rtl":
                write(pdir / "rtl/top.v", "module top(); endmodule\n")
            if outcome == "changed_sdc":
                write(pdir / "constraints/constraint.sdc", "changed clock")
            return 1 if outcome == "failed" else 0
        return 0

    monkeypatch.setattr(pipe, "_sh", run)
    try:
        assert pipe._repair_failed_route(jid, "top", pdir, "sky130hd", 8) == (outcome == "clean")
        assert sum(stage == "repair" for stage, _, _ in calls) == (0 if outcome in ("early_failure", "no_recipe") else 1)
        assert store.design(jid, "top")["status"] != "passed"
        if outcome == "clean":
            assert json.loads((pdir / "reports/route_repair_result.json").read_text())["signoff_pending"] is True
    finally:
        pipe.pool.shutdown()


def test_repaired_flow_still_requires_signoff_before_dataset(tmp_path, monkeypatch):
    src = tmp_path / "src"
    write(src / "top.v", "module top(input clk, output q); assign q=clk; endmodule\n")
    store = JobStore()
    jid = store.create_job("route dispatch", {}, [{"name": "top"}])
    pipe = pipeline.Pipeline(store, ingest=False)
    pdir = tmp_path / "project"
    calls = []

    def run(_jid, _name, stage, cmd, actual, **kwargs):
        calls.append(stage)
        if stage == "synth":
            r = actual / "backend/RUN_001"
            write(r / "reports_orfs/synth_stat.txt", "=== top ===\n 109 173.166 109 173.166 cells\n   Chip area for module '\\top': 173.166000\n")
            write(r / "results/1_2_yosys.v", "module top(clk,q);\ninput clk;\noutput q;\nendmodule\n")
            return 0
        if stage == "flow":
            write(actual / "backend/RUN_002/flow.log", "[ERROR GRT-0116] congestion")
            return 2
        if stage == "signoff":
            write(actual / "reports/drc.json", {"status": "clean", "total_violations": 0})
            write(actual / "reports/lvs.json", {"status": "mismatch", "mismatch_count": 1})
            return 2
        assert stage == "repair"
        return 2

    tried = []
    def restored(*args):
        tried.append(True)
        return True  # This test isolates the caller's signoff/dataset boundary.
    monkeypatch.setattr(pipe, "_sh", run)
    monkeypatch.setattr(pipe, "_repair_failed_route", restored)
    try:
        pipe._run_design_inner(jid, "top", src, "top", pdir, "sky130hd")
        assert tried == [True]
        assert calls == ["synth", "flow", "signoff", "repair"]
        assert store.design(jid, "top")["status"] == "failed"
        assert store.design(jid, "top")["detail"]["failed_stage"] == "repair"
    finally:
        pipe.pool.shutdown()
