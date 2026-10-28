"""A whole-flow run_orfs.sh call must outlive any single stage's bound.

run_orfs.sh bounds each stage with ORFS_TIMEOUT and archives the run when one
expires. SKY130 C01134 spent ~6 h in detailed route; the agent bounded the whole
call by one stage budget, killed it before the archive, route.json read
"unknown" and the fixed-area relief never ran.
"""
from r2g_agent.jobs import JobStore
from r2g_agent.pipeline import Pipeline


def _run(tmp_path, script_name):
    store = JobStore()
    jid = store.create_job("t", {}, [{"name": "dut"}])
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    script = tmp_path / script_name
    script.write_text("#!/bin/bash\nsleep 2\nexit 0\n")
    script.chmod(0o755)
    pipeline = Pipeline(store, ingest=False, min_free_gb=0, stage_timeout=1)
    try:
        return pipeline._sh(jid, "dut", "flow", ["bash", str(script)], project)
    finally:
        pipeline.pool.shutdown()


def test_run_orfs_call_is_not_cut_at_one_stage_budget(tmp_path):
    assert _run(tmp_path, "run_orfs.sh") == 0


def test_other_commands_keep_the_stage_bound(tmp_path):
    assert _run(tmp_path, "other.sh") == 124


def test_signoff_repair_allows_the_full_antenna_ladder():
    # nangate45 C01972: three rounds ended before antenna_ratio_overconstrain (r3).
    from r2g_agent import pipeline
    assert pipeline.REPAIR_MAX_ITERS >= 4
    src = open(pipeline.__file__).read()
    assert '"--max-iters", str(REPAIR_MAX_ITERS)' in src
