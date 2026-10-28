"""Verify accounting context reaches nested stage commands without running EDA."""
import json
import sys

from r2g_agent.jobs import JobStore
from r2g_agent.pipeline import Pipeline


def test_stage_propagates_accounting_identity(tmp_path, monkeypatch):
    journal = tmp_path / "audit.jsonl"
    monkeypatch.setenv("R2G_ORFS_AUDIT_FILE", str(journal))
    store = JobStore()
    jid = store.create_job("audit", {}, [{"name": "dut"}])
    project = tmp_path / "project"
    project.mkdir()
    result = project / "context.json"
    command = [sys.executable, "-c",
               "import os,json,pathlib; pathlib.Path(%r).write_text(json.dumps("
               "{k:v for k,v in os.environ.items() if k.startswith('R2G_ORFS_')}))" % str(result)]
    pipeline = Pipeline(store, ingest=False, min_free_gb=0)
    try:
        assert pipeline._sh(jid, "dut", "repair", command, project) == 0
    finally:
        pipeline.pool.shutdown()
    context = json.loads(result.read_text())
    assert context["R2G_ORFS_AUDIT_FILE"] == str(journal)
    assert context["R2G_ORFS_AUDIT_JOB"] == jid
    assert context["R2G_ORFS_AUDIT_DESIGN"] == "dut"
    assert context["R2G_ORFS_AUDIT_STAGE"] == "repair"
    assert context["R2G_ORFS_AUDIT_PROJECT"] == str(project)
    assert context["R2G_ORFS_AUDIT_EXECUTION"]
    assert context["R2G_ORFS_REAL_MAKE"]
