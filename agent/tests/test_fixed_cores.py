"""Fixed experiment thread budgets propagate through all pipeline subprocesses."""
import json
import sys

import pytest

from r2g_agent.jobs import JobStore
from r2g_agent.pipeline import Pipeline


@pytest.mark.parametrize("setting,small,large", [(None, 4, 8), ("0", 4, 8), ("1", 8, 8)])
def test_small_design_cap_is_only_disabled_by_opt_in(monkeypatch, setting, small, large):
    if setting is None:
        monkeypatch.delenv("R2G_FIXED_CORES", raising=False)
    else:
        monkeypatch.setenv("R2G_FIXED_CORES", setting)
    pipeline = Pipeline(JobStore(), ingest=False, cores_per_design=8)
    try:
        assert pipeline._flow_cores(100) == small
        assert pipeline._flow_cores(4999) == small
        assert pipeline._flow_cores(5000) == large
    finally:
        pipeline.pool.shutdown()


@pytest.mark.parametrize("stage", ["synth", "flow", "signoff", "repair"])
def test_fixed_budget_reaches_stage_children(tmp_path, monkeypatch, stage):
    monkeypatch.setenv("R2G_FIXED_CORES", "1")
    store = JobStore()
    jid = store.create_job("fixed thread test", {}, [{"name": "dut"}])
    project = tmp_path / stage
    project.mkdir()
    result = project / "thread_budget.json"
    keys = ["NUM_CORES", "OMP_NUM_THREADS"]
    command = [sys.executable, "-c", "import os,json,pathlib; pathlib.Path(%r).write_text("
               "json.dumps({k:os.environ.get(k) for k in %r}))" % (str(result), keys)]
    pipeline = Pipeline(store, ingest=False, cores_per_design=8, min_free_gb=0)
    try:
        flow_budget = str(pipeline._flow_cores(100))
        stage_env = {"NUM_CORES": flow_budget, "OMP_NUM_THREADS": flow_budget} if stage == "flow" else None
        assert pipeline._sh(jid, "dut", stage, command, project, env=stage_env) == 0
    finally:
        pipeline.pool.shutdown()
    assert json.loads(result.read_text()) == {key: "8" for key in keys}
