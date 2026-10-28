"""The task ledger cannot turn a finished turn or model claim into delivery."""
import copy
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tarfile

import pytest

from r2g_agent import artifact_snapshots, design_checks, task_state, validation
from r2g_agent.input_snapshots import capture
from r2g_agent.package import package_job
from r2g_agent.task_state import TaskStateStore


@pytest.fixture
def ledger(tmp_path):
    return TaskStateStore(tmp_path / "sessions.sqlite")


def _task(ledger, *, tid="t1", job_id="j1", profile="dataset_delivery"):
    return ledger.ensure("s1", tid, "交付这个设计的数据集", job_id, constraints=("保留 RTL 功能",), profile=profile)


def _job(tmp_path, *, status="done"):
    return {"id": "j1", "status": status, "updated": 123,
            "spec": {"output": "graph", "platform": "nangate45"}, "result": {},
            "designs": [{"name": "top", "status": "passed" if status == "done" else status,
                         "stage": "verify", "project": str(tmp_path / "project"), "detail": {}}]}


@pytest.fixture
def deliverable(tmp_path, monkeypatch):
    """Synthetic raw-validation adapter; the archive writer/reader are real."""
    project = tmp_path / "project"
    contents = {"rtl/top.v": "module top; endmodule", "constraints/c.sdc": "",
                "dataset/top.json": '{"nodes": 1}', "lvs/raw.fixture": "independent synthetic audit"}
    for name in ("drc.json", "lvs.json", "rcx.json", "route.json", "timing_check.json",
                 "signoff_manifest.json", "signoff_gate.json", "graph_dataset.json", "verify.json"):
        contents["reports/" + name] = "{}"
    for name, body in contents.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    checked = {"schema_version": 1, "policy_sha256": "a" * 64, "verdict": "PASS",
               "verification_level": "synthetic_test_adapter",
               "three_layer": {key: {"verdict": "PASS", "reasons": []} for key in task_state.LAYERS},
               "checks": {key: {"verdict": "PASS", "method": "synthetic_test_adapter", "reasons": []}
                          for key in ("drc", "lvs", "rcx", "dataset")},
               "input_snapshot": capture(project), "output_snapshot": artifact_snapshots.capture(project, "graph"),
               "evidence_files": [{"path": "lvs/raw.fixture", "sha256": hashlib.sha256(
                   (project / "lvs/raw.fixture").read_bytes()).hexdigest()}]}
    monkeypatch.setattr(validation, "review", lambda path: copy.deepcopy(checked))
    job = _job(tmp_path)
    job["designs"][0]["detail"]["validation"] = copy.deepcopy(checked)
    package = package_job(SimpleNamespace(job=lambda _: job), "j1")
    job["result"] = {"passed": 1, "total": 1, "package": str(package)}
    return job, checked, package


def test_task_goal_constraints_are_durable_and_immutable(ledger):
    task = _task(ledger)
    reopened = TaskStateStore(ledger.path)
    assert _task(reopened)["id"] == task["id"]
    assert reopened.get(task["id"])["constraints"] == ["保留 RTL 功能"]
    with pytest.raises(ValueError, match="another task contract"):
        reopened.ensure("s1", "t1", "偷偷把任务改成仅编译", profile="compile_only")
    assert task["status"] == "ready"
    assert task["pending_evidence"] and task["job_id"] == "j1"


def test_new_turn_and_new_job_do_not_inherit_old_failure_or_goal(ledger, tmp_path):
    old = _task(ledger)
    job = _job(tmp_path, status="failed")
    ledger.observe(old["id"], job)
    new = ledger.ensure("s1", "t2", "新的 UART 任务", "j2")
    assert new["id"] != old["id"] and new["goal"] == "新的 UART 任务"
    assert new["status"] == "ready" and new["observation"] is None
    assert ledger.get_for_turn("unbound") is None
    assert ledger.get_for_turn("t2")["id"] == new["id"]
    assert ledger.get_for_job("s1", "j1")["id"] == old["id"]
    assert ledger.get_for_job("another-session", "j1") is None
    ledger.bind_turn(old["id"], "j1-review")
    assert ledger.get_for_turn("j1-review")["goal"] == old["goal"]
    with pytest.raises(ValueError, match="another task"):
        ledger.bind_turn(new["id"], "j1-review")
    with pytest.raises(ValueError, match="another task"):
        ledger.bind_job(new["id"], "j1")


def test_running_job_does_not_reread_final_reports_or_trust_cached_pass(ledger, tmp_path, monkeypatch):
    task = _task(ledger)
    job = _job(tmp_path, status="running")
    job["designs"][0]["detail"]["validation"] = {"verdict": "PASS"}
    monkeypatch.setattr(validation, "review", lambda _: pytest.fail("active jobs do not need final audit reads"))
    state = ledger.observe(task["id"], job)
    assert state["status"] == "waiting_job"
    assert all(c["verdict"] == "UNKNOWN" for c in state["conditions"])
    assert {c["id"] for c in state["pending_evidence"]} >= {
        "design:top:technical", "design:top:quantitative", "delivery"}


@pytest.mark.parametrize("verdict", ["FAIL", "UNKNOWN", "STALE"])
def test_fresh_technical_evidence_outweighs_old_pass_and_model_claims(ledger, deliverable, verdict):
    job, checked, _ = deliverable
    task = _task(ledger)
    checked["three_layer"]["technical"]["verdict"] = verdict
    checked["three_layer"]["technical"]["reasons"] = ["must_connect_unresolved"]
    state = ledger.observe(task["id"], job, evidence={"verdict": "PASS", "complete": True})
    assert state["status"] == "blocked"
    pending = next(c for c in state["pending_evidence"] if c["id"] == "design:top:technical")
    assert pending["verdict"] == verdict and pending["design"] == "top"
    assert pending["source"] == "host:validation.review" and pending["refs"][0]["job_id"] == "j1"


def test_fresh_checks_and_actual_bound_archive_complete_task(ledger, deliverable):
    job, _, _ = deliverable
    task = _task(ledger)
    state = ledger.observe(task["id"], job)
    assert state["status"] == "complete" and state["pending_evidence"] == []
    assert all(c["verdict"] == "PASS" for c in state["conditions"])
    assert TaskStateStore(ledger.path).get(task["id"])["status"] == "complete"
    with ledger.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM engineering_task_observations").fetchone()[0] == 1


def test_existing_archive_without_manifest_is_not_delivery(ledger, deliverable):
    job, _, package = deliverable
    package.write_bytes(b"model says the dataset passed")
    state = ledger.observe(_task(ledger)["id"], job)
    assert state["status"] == "blocked"
    assert state["conditions"][-1]["verdict"] == "STALE"


def test_archive_dataset_bytes_are_checked_against_manifest(ledger, deliverable):
    job, _, package = deliverable
    with tarfile.open(package, "r:gz") as source:
        entries = [(member, source.extractfile(member).read()) for member in source]
    with tarfile.open(package, "w:gz") as output:
        for member, data in entries:
            if member.name.endswith("/dataset/top.json"):
                data = b'{"nodes": 0}'
                member.size = len(data)
            output.addfile(member, io.BytesIO(data))
    state = ledger.observe(_task(ledger)["id"], job)
    assert state["status"] == "blocked" and state["conditions"][-1]["verdict"] == "STALE"


def test_partial_job_never_satisfies_all_requested_designs(ledger, deliverable):
    job, _, _ = deliverable
    job["designs"].append({"name": "broken", "status": "failed", "stage": "synth", "detail": {}})
    state = ledger.observe(_task(ledger)["id"], job)
    assert state["status"] == "blocked"
    assert state["conditions"][0]["verdict"] == "FAIL"
    assert any(c["design"] == "broken" for c in state["pending_evidence"])


def test_child_job_resets_old_verdict_without_replacing_goal(ledger, deliverable, tmp_path):
    job, _, _ = deliverable
    task = _task(ledger)
    assert ledger.observe(task["id"], job)["status"] == "complete"
    assert ledger.bind_job(task["id"], "j1")["status"] == "complete"
    bound = ledger.bind_job(task["id"], "j-child")
    assert bound["status"] == "waiting_job" and bound["goal"] == task["goal"]
    with pytest.raises(ValueError, match="current bound job"):
        ledger.observe(task["id"], job)
    assert ledger.get_for_job("s1", "j1")["id"] == task["id"]
    assert ledger.get_for_job("s1", "j-child")["id"] == task["id"]


def _real_compile(tmp_path):
    import shutil
    if not shutil.which("iverilog"):
        pytest.skip("Icarus is unavailable")
    source = tmp_path / "compile-source"
    source.mkdir()
    (source / "top.v").write_text("module top(input a, output y); assign y = a; endmodule")
    receipt = design_checks.check_design(str(source), "top", "compile")
    assert receipt["status"] == "passed"
    return source, receipt


def test_compile_profile_uses_real_source_bound_receipt_without_claiming_flow(ledger, tmp_path):
    _, receipt = _real_compile(tmp_path)
    task = _task(ledger, job_id=None, profile="compile_only")
    state = ledger.observe(task["id"], evidence={"diagnostic": receipt})
    assert state["status"] == "complete"
    assert state["conditions"][0]["verdict"] == "PASS"
    assert "no simulation, signoff or dataset" in state["observation"]["scope"]


def test_compile_success_becomes_stale_after_source_mutation(ledger, tmp_path):
    source, receipt = _real_compile(tmp_path)
    (source / "top.v").write_text("module top; BROKEN endmodule")
    task = _task(ledger, job_id=None, profile="compile_only")
    state = ledger.observe(task["id"], evidence={"diagnostic": receipt})
    assert state["status"] == "blocked" and state["conditions"][0]["verdict"] == "STALE"


def test_compile_model_claim_without_persisted_receipt_is_unknown(ledger):
    task = _task(ledger, job_id=None, profile="compile_only")
    state = ledger.observe(task["id"], evidence={"diagnostic": {"status": "passed", "exit_code": 0}})
    assert state["status"] == "blocked" and state["conditions"][0]["verdict"] == "UNKNOWN"


def test_compile_evidence_mutating_while_read_is_stale(ledger, tmp_path, monkeypatch):
    _, receipt = _real_compile(tmp_path)
    def changed(*args):
        raise ValueError("evidence_changed_during_read")
    monkeypatch.setattr(task_state, "_sha", changed)
    state = ledger.observe(_task(ledger, job_id=None, profile="compile_only")["id"],
                           evidence={"diagnostic": receipt})
    assert state["conditions"][0]["verdict"] == "STALE"


def test_bounded_view_keeps_exact_constraints_and_reports_omitted_evidence(ledger, tmp_path):
    task = _task(ledger)
    job = _job(tmp_path, status="running")
    job["designs"] = [{"name": "top" + str(i), "status": "running", "stage": "synth"} for i in range(20)]
    ledger.observe(task["id"], job)
    view = ledger.view(task["id"])
    assert len(view["conditions"]) == 24 and view["omitted_conditions"] > 0
    assert view["constraints"] == ["保留 RTL 功能"]
    assert view["completion_authority"].startswith("host evidence readers")
