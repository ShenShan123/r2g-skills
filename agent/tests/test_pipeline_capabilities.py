"""Admission through the real pipeline/store; synthetic host observations only."""
from contextlib import nullcontext
from copy import deepcopy
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from r2g_agent import capabilities, pipeline, task_skills
from r2g_agent.input_snapshots import digest
from r2g_agent.jobs import CANCELLED, FAILED, RECOVERY_REQUIRED, RUNNING, JobStore


@pytest.fixture
def run(tmp_path, monkeypatch):
    """Use a private store without initializing knowledge or worker threads."""
    store = JobStore(tmp_path / "jobs.sqlite")
    source = tmp_path / "source"
    source.mkdir()
    (source / "top.v").write_text("module top; endmodule\n")
    project = tmp_path / "project"
    jid = store.create_job("capability gate fixture", {"params": {}}, [{"name": "top"}])
    store.set_job(jid, RUNNING)
    store.set_design(jid, "top", status=RUNNING, stage="prepare", project=str(project),
                     detail={"owner": pipeline.OWNER, "source": str(source)})
    pipe = object.__new__(pipeline.Pipeline)
    pipe.store = store
    pipe._shutdown = threading.Event()
    pipe.min_free_gb = 0
    pipe.workers = 1
    pipe.keep_intermediate = True
    pipe._sh = Mock(side_effect=AssertionError("EDA must not start in this fixture"))
    pipe._ingest = Mock(side_effect=AssertionError("knowledge must not be written"))

    # This is an explicit host-observation fixture, not a measured toolchain.
    req = capabilities.requirements()
    observed = {
        "schema_version": 1,
        "platform": {"name": "nangate45", "agent_supported": True,
                     "role": "predictive_research", "manufacturing_qualified": False},
        "environment": {"status": "resolved"},
        "tools": {name: {"status": "present", "usable": True, "sha256": "a" * 64}
                  for name in req["tools"] + ["image_python"]},
        "assets": {name: {"status": "present"} for name in req["assets"]},
        "orfs": {"makefile": {"status": "present"},
                 "defaults_helper": {"status": "present", "executable": True}},
        "combinations": [{"status": "unverified", "evidence": []}],
        "verified_combinations": [],
        "inventory_id": "b" * 64,
    }
    inventory = Mock(side_effect=lambda *a, **kw: deepcopy(observed))
    monkeypatch.setattr(capabilities, "inventory", inventory)

    def create(*args, **kwargs):
        (project / "reports").mkdir(parents=True, exist_ok=True)
        return {"profile_changed": [], "top": "top", "top_source": "fixture",
                "files": ["top.v"], "frontend_fixes": [], "missing_data_files": []}

    monkeypatch.setattr(pipeline.proj, "create", Mock(side_effect=create))
    monkeypatch.setattr(pipeline.housekeeping, "wait_for_disk",
                        lambda *a, should_stop, **kw: should_stop())
    monkeypatch.setattr(pipeline.housekeeping, "design_slot", lambda *a, **kw: nullcontext())
    monkeypatch.setattr(pipeline.housekeeping, "drop_orfs_copies", Mock(return_value=0))
    return SimpleNamespace(store=store, pipe=pipe, jid=jid, source=source, project=project,
                           observed=observed, inventory=inventory)


def invoke(run, output="graph"):
    run.pipe._run_design(run.jid, "top", run.source, "top", run.project,
                         run.observed["platform"]["name"], output)


def read_report(run, name):
    return json.loads((run.project / "reports" / name).read_text())


@pytest.mark.parametrize("output", ["graph", "image"])
def test_ready_records_actual_inventory_and_bound_contract_without_awarding_pass(run, output):
    assert run.pipe._capability_gate(run.jid, "top", run.project, "nangate45", output)

    run.inventory.assert_called_once_with("nangate45")
    assert read_report(run, "toolchain_capabilities.json") == run.observed
    receipt = read_report(run, "task_contract.json")
    contract = task_skills.load_skill("dataset_delivery")
    assert receipt["contract"] == contract
    assert receipt["contract_sha256"] == digest(contract)
    assert (receipt["skill_id"], receipt["version"]) == (contract["id"], contract["version"])
    admission = receipt["admission"]
    assert admission["verdict"] == "READY"
    assert admission["inventory_id"] == run.observed["inventory_id"]
    assert admission["requirements"]["output"] == output
    assert admission["combination_verification"] == "UNVERIFIED"
    design = run.store.design(run.jid, "top")
    assert design["status"] == RUNNING and design["stage"] == "prepare"
    assert design["detail"]["capability_admission"] == admission
    assert design["detail"]["task_contract_sha256"] == digest(contract)
    assert not ({"signoff", "verify"} & design["detail"].keys())
    events = [e for e in run.store.events(run.jid) if e["kind"] == "ok"]
    assert len(events) == 1 and events[0]["stage"] == "prepare"
    assert events[0]["data"]["combination_verification"] == "UNVERIFIED"
    assert not any((run.project / "reports" / name).exists()
                   for name in ("drc.json", "lvs.json", "rcx.json", "signoff_gate.json"))
    assert run.store.active_executions(run.jid) == []
    run.pipe._sh.assert_not_called()
    run.pipe._ingest.assert_not_called()


@pytest.mark.parametrize("failure,verdict,reason", [
    ("tool_missing", "BLOCKED", "tool:yosys"),
    ("deck_missing", "BLOCKED", "asset:drc"),
    ("tool_unverified", "UNVERIFIED", "tool:openroad"),
    ("deck_unverified", "UNVERIFIED", "asset:lvs"),
    ("environment_unverified", "UNVERIFIED", "resolved_environment"),
    ("platform_unsupported", "BLOCKED", "platform/output is not registered for this agent admission policy"),
])
def test_missing_unknown_or_unsupported_prerequisites_stop_before_synthesis(run, failure, verdict, reason):
    if failure == "tool_missing":
        run.observed["tools"]["yosys"] = {"status": "missing", "usable": False}
    elif failure == "deck_missing":
        run.observed["assets"]["drc"] = {"status": "missing"}
    elif failure == "tool_unverified":
        run.observed["tools"]["openroad"]["usable"] = False
    elif failure == "deck_unverified":
        run.observed["assets"]["lvs"] = {"status": "unverified"}
    elif failure == "environment_unverified":
        run.observed["environment"]["status"] = "unverified"
    else:
        run.observed["platform"].update(name="asap7", agent_supported=False)

    invoke(run)

    design = run.store.design(run.jid, "top")
    assert design["status"] == FAILED
    assert design["stage"] == design["detail"]["failed_stage"] == "prepare"
    admission = design["detail"]["capability_admission"]
    assert admission["verdict"] == verdict
    assert reason in admission["missing"] + admission["unverified"] + admission["unsupported"]
    assert read_report(run, "toolchain_capabilities.json") == run.observed
    assert read_report(run, "task_contract.json")["admission"] == admission
    events = run.store.events(run.jid)
    failures = [e for e in events if e["kind"] == "fail"]
    assert len(failures) == 1
    assert failures[0]["stage"] == "prepare"
    assert failures[0]["data"]["failure_class"] == "environment"
    assert failures[0]["data"]["backend_started"] is False
    assert failures[0]["data"]["capability_admission"] == admission
    assert not any(e["stage"] in ("synth", "flow", "signoff", "graph", "verify", "done") for e in events)
    assert (run.source / "top.v").read_text() == "module top; endmodule\n"
    run.pipe._sh.assert_not_called()
    run.pipe._ingest.assert_not_called()


def test_ready_allows_initial_synthesis_but_cannot_mask_a_synthesis_failure(run, monkeypatch):
    run.pipe._sh.side_effect = None
    run.pipe._sh.return_value = 1
    run.pipe._ingest.side_effect = None
    monkeypatch.setattr(pipeline.frontend, "match_synth_failure", lambda *a: [])
    invoke(run)

    run.pipe._sh.assert_called_once()
    call = run.pipe._sh.call_args
    assert call.args[2] == "synth" and call.kwargs["env"] == {"ORFS_STAGES": "synth"}
    design = run.store.design(run.jid, "top")
    assert design["detail"]["capability_admission"]["verdict"] == "READY"
    assert design["status"] == FAILED and design["detail"]["failed_stage"] == "synth"
    assert not any(e["stage"] == "done" for e in run.store.events(run.jid))
    assert not ({"signoff", "verify"} & design["detail"].keys())


def test_admission_is_refreshed_instead_of_reusing_a_previous_ready_report(run):
    assert run.pipe._capability_gate(run.jid, "top", run.project, "nangate45", "graph")
    run.observed["inventory_id"] = "c" * 64
    run.observed["tools"]["yosys"]["usable"] = False
    assert not run.pipe._capability_gate(run.jid, "top", run.project, "nangate45", "graph")
    assert run.inventory.call_count == 2
    assert read_report(run, "toolchain_capabilities.json") == run.observed
    receipt = read_report(run, "task_contract.json")
    assert receipt["admission"]["inventory_id"] == "c" * 64
    assert receipt["admission"]["verdict"] == "UNVERIFIED"
    assert run.store.design(run.jid, "top")["status"] == FAILED
    run.pipe._sh.assert_not_called()


def test_cancellation_before_admission_does_not_probe_or_write_reports(run):
    run.store.request_cancel(run.jid)
    with pytest.raises(pipeline.Cancelled):
        run.pipe._capability_gate(run.jid, "top", run.project, "nangate45", "graph")
    invoke(run)
    assert run.store.design(run.jid, "top")["status"] == CANCELLED
    assert not run.project.exists()
    run.inventory.assert_not_called()
    run.pipe._sh.assert_not_called()


@pytest.mark.parametrize("ready", [True, False])
def test_cancellation_during_inventory_wins_over_ready_and_environment_failure(run, ready):
    if not ready:
        run.observed["assets"]["rcx"] = {"status": "missing"}

    def inventory(*args, **kwargs):
        run.store.request_cancel(run.jid)
        return deepcopy(run.observed)

    run.inventory.side_effect = inventory
    invoke(run)
    design = run.store.design(run.jid, "top")
    assert design["status"] == CANCELLED
    assert "capability_admission" not in design["detail"]
    events = run.store.events(run.jid)
    assert not any(e["kind"] == "fail" or e["stage"] == "synth" for e in events)
    assert sum(e["kind"] == "cancelled" for e in events) == 1
    run.pipe._sh.assert_not_called()
    run.pipe._ingest.assert_not_called()


def test_shutdown_during_inventory_preserves_recovery_instead_of_starting_work(run):
    def inventory(*args, **kwargs):
        run.pipe._shutdown.set()
        return deepcopy(run.observed)

    run.inventory.side_effect = inventory
    invoke(run)
    assert run.store.design(run.jid, "top")["status"] == RECOVERY_REQUIRED
    assert run.store.job(run.jid)["status"] == RECOVERY_REQUIRED
    assert not any(e["kind"] == "fail" or e["stage"] == "synth" for e in run.store.events(run.jid))
    run.pipe._sh.assert_not_called()
    run.pipe._ingest.assert_not_called()
