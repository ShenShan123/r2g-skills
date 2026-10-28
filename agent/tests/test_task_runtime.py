"""Durable task/stage integration without a real model or EDA subprocess."""
from types import SimpleNamespace

import pytest

from r2g_agent import agent_loop, capabilities, task_runtime, tools
from r2g_agent.debug_actions import DependencyDebugController
from r2g_agent.event_bridge import BridgeWorker
from r2g_agent.execution import HostExecutor
from r2g_agent.jobs import JobStore
from r2g_agent.task_state import TaskStateStore


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    sessions, jobs = agent_loop.SessionStore(), JobStore()
    caps = {"inventory_id": "test-inventory", "platform": {"name": "nangate45", "agent_supported": True},
            "tools": {name: {"usable": True, "sha256": "d" * 64} for name in ("iverilog", "yosys", "vvp")}}
    monkeypatch.setattr(capabilities, "inventory", lambda *args, **kwargs: caps)
    monkeypatch.setattr(tools.RT, "store", jobs)
    monkeypatch.setattr(tools.RT, "pipeline", SimpleNamespace())
    project = tmp_path / "project"
    (project / "rtl").mkdir(parents=True)
    (project / "constraints").mkdir()
    (project / "rtl" / "top.v").write_text("module top; child u(); endmodule")
    sid = sessions.create()
    parent, _ = sessions.turns.create(sid, "root", {"message": "交付 top 数据集；保留源文件"})
    assert sessions.turns.claim(parent["id"])
    before = {"sha256": "a" * 64, "rtl_sha256": "b" * 64, "constraints_sha256": "c" * 64}
    jid, _ = jobs.create_submission("parent", {"platform": "nangate45", "output": "graph"}, [{
        "name": "top", "project": str(project), "detail": {"top": "top", "failed_stage": "synth",
        "input_bindings": {"synth": {"before": before, "after": dict(before), "unchanged": True, "exit_code": 1}}}}], {
        "request_key": "parent-submission", "request_hash": "a" * 64, "source_snapshot": "a" * 64,
        "session_id": sid, "turn_id": parent["id"], "action_id": "origin-action"})
    sessions.turns.link_job(parent["id"], jid)
    task_runtime.task_for_turn(sessions, sessions.turns.get(parent["id"]))
    jobs.set_design(jid, "top", status="failed", stage="synth",
        event=("synth", "fail", "Unknown module type: child", {}))
    jobs.set_job(jid, "failed")
    sessions.turns.finish(parent["id"], "complete", {"type": "done"})
    return SimpleNamespace(sessions=sessions, jobs=jobs, sid=sid, jid=jid, parent=parent,
        project=project, executor=HostExecutor(sessions, jobs))


def debug_turn(runtime, *, key="debug", max_model_calls=4, max_actions=12):
    turn, _ = runtime.sessions.turns.create(runtime.sid, key, {"message": "审查失败并进行受限诊断"},
        kind="debug_review", job_id=runtime.jid, max_model_calls=max_model_calls, max_actions=max_actions)
    return turn


def execute(runtime, turn, name, arguments):
    assert runtime.sessions.turns.claim(turn["id"])
    call = {"id": "call-1", "name": name, "arguments": arguments}
    action = runtime.sessions.turns.begin_action(turn["id"], "model-call", call)
    assert runtime.sessions.turns.start_action(action["id"])
    context = runtime.executor.host_context("chat", session_id=runtime.sid, turn_id=turn["id"],
        action_id=action["id"], allow_session_tools=True)
    return runtime.executor.execute_prepared(name, arguments, context, messages=[])


def test_debug_review_cannot_resubmit_job_and_denial_precedes_source_reads(runtime, monkeypatch):
    turn = debug_turn(runtime)
    monkeypatch.setattr(runtime.executor, "_snapshot", lambda *args: pytest.fail("denial must precede source reads"))
    monkeypatch.setattr(tools, "call", lambda *args, **kwargs: pytest.fail("denied job must not execute"))
    receipt = execute(runtime, turn, "start_job", {"designs": [{"path": "/unapproved/source"}]})
    assert receipt["result"]["error_code"] == "debug_action_scope"
    assert runtime.sessions.turns.linked_jobs(runtime.sid) == [runtime.jid]


@pytest.mark.parametrize("name,arguments,error", [
    ("job_status", {"job_id": "another-job"}, "debug_job_scope"),
    ("read_rtl", {"job_id": "another-job", "design": "top"}, "debug_job_scope"),
    ("check_design", {"path": "/unapproved/source", "top": "top"}, "stage_action_denied"),
    ("inspect_design", {"path": "/unapproved/source", "top": "top"}, "stage_action_denied"),
])
def test_debug_review_rejects_cross_job_and_cross_path_before_execution(runtime, monkeypatch, name, arguments, error):
    turn = debug_turn(runtime)
    monkeypatch.setattr(runtime.executor, "_snapshot", lambda *args: pytest.fail("scope denial must precede source reads"))
    monkeypatch.setattr(tools, "call", lambda *args, **kwargs: pytest.fail("out-of-scope tool must not execute"))
    receipt = execute(runtime, turn, name, arguments)
    assert receipt["result"]["error_code"] == error


def test_last_budgeted_diagnostic_action_is_allowed_but_not_reexecuted(runtime, monkeypatch):
    turn = debug_turn(runtime, max_actions=1)
    calls = []
    monkeypatch.setattr(tools, "call", lambda name, arguments, **kwargs:
                        calls.append((name, arguments)) or {"status": "passed", "scope": "compile_only"})
    arguments = {"path": str(runtime.project / "rtl"), "top": "top"}
    first = execute(runtime, turn, "check_design", arguments)
    assert first["result"]["status"] == "passed"
    action = runtime.sessions.turns.actions(turn["id"])[0]
    context = runtime.executor.host_context("chat", session_id=runtime.sid, turn_id=turn["id"],
        action_id=action["id"], allow_session_tools=True)
    again = runtime.executor.execute_prepared("check_design", arguments, context, messages=[])
    assert again["replayed"] and again["result"] == first["result"]
    assert len(calls) == 1


def test_new_user_task_and_job_do_not_inherit_old_failure(runtime):
    old = task_runtime.snapshot(runtime.sessions, runtime.jobs, runtime.sessions.turns.get(runtime.parent["id"]))
    assert old["task"]["status"] == "blocked"
    turn, _ = runtime.sessions.turns.create(runtime.sid, "new-request", {"message": "换成另一个设计，先只读检查"})
    new = task_runtime.task_for_turn(runtime.sessions, turn)
    assert new["id"] != old["task"]["id"]
    assert new["goal"] == "换成另一个设计，先只读检查"
    jid = runtime.jobs.create_job("new", {"platform": "nangate45", "output": "graph"}, [{"name": "new_top"}])
    runtime.sessions.turns.link_job(turn["id"], jid)
    now = task_runtime.snapshot(runtime.sessions, runtime.jobs, runtime.sessions.turns.get(turn["id"]))
    assert now["task"]["job_id"] == jid and now["task"]["status"] == "waiting_job"
    assert now["stage_actions"]["job_id"] == jid
    assert {d["design"] for d in now["stage_actions"]["designs"]} == {"new_top"}
    assert all("Unknown module" not in str(c) for c in now["task"]["pending_evidence"])


def test_background_review_inherits_original_goal_and_exposes_task_status(runtime):
    turn = debug_turn(runtime)
    now = task_runtime.snapshot(runtime.sessions, runtime.jobs, turn)
    assert now["task"]["goal"] == "交付 top 数据集；保留源文件"
    receipt = execute(runtime, turn, "task_status", {})
    assert receipt["result"]["id"] == now["task"]["id"]
    assert receipt["result"]["pending_evidence"]
    assert receipt["result"]["status"] != "complete"


def test_old_parent_review_cannot_rewind_active_child_task(runtime):
    ledger = TaskStateStore(runtime.sessions.path)
    task = ledger.get_for_turn(runtime.parent["id"])
    child = runtime.jobs.create_job("child", {"platform": "nangate45", "output": "graph"}, [{"name": "top"}])
    ledger.bind_job(task["id"], child)
    late = debug_turn(runtime, key="late-parent-review")
    view = task_runtime.snapshot(runtime.sessions, runtime.jobs, late)
    assert ledger.get(task["id"])["job_id"] == child
    assert view["task"]["job_id"] == child
    assert view["task"]["status"] != "complete"


def test_failed_job_bridge_creates_one_budgeted_debug_turn_across_crash_window(runtime, monkeypatch):
    authorizations = []
    monkeypatch.setattr(DependencyDebugController, "authorize", lambda self, sid, jid:
                        authorizations.append((sid, jid)) or {"status": "approved"})
    bridge = BridgeWorker(runtime.sessions, runtime.jobs, lambda: pytest.fail("test must not request a model"))
    monkeypatch.setattr(bridge, "launch", lambda turn: None)
    reviewed = runtime.jobs.reviewed
    monkeypatch.setattr(runtime.jobs, "reviewed", lambda *args:
                        (_ for _ in ()).throw(RuntimeError("crash after durable enqueue")))
    with pytest.raises(RuntimeError, match="durable enqueue"):
        bridge.tick()
    turn = runtime.sessions.turns.by_key("job-review:" + runtime.jid)
    assert turn["kind"] == "debug_review" and turn["max_model_calls"] == 4 and turn["max_actions"] == 12
    monkeypatch.setattr(runtime.jobs, "reviewed", reviewed)
    bridge.tick()
    bridge.tick()
    assert runtime.jobs.review_outbox() == []
    assert runtime.sessions.turns.by_key("job-review:" + runtime.jid)["id"] == turn["id"]
    assert authorizations == [(runtime.sid, runtime.jid)]
    assert runtime.sessions.model_calls(runtime.sid) == []


def test_debug_loop_has_fixed_model_budget_and_removes_unrestricted_tools(runtime, monkeypatch):
    turn = debug_turn(runtime)
    seen = []
    class Model:
        def stream(self, messages, specs, system=""):
            seen.append(({s["name"] for s in specs}, system))
            yield {"type": "tool_call", "id": "call-" + str(len(seen)), "name": "job_status",
                   "arguments": {"job_id": runtime.jid}}
            yield {"type": "done", "stop_reason": "tool_calls", "metadata": {"stream_complete": True}}
    list(agent_loop.run_turn(Model(), runtime.sessions, runtime.sid, turn["payload"]["message"], turn_id=turn["id"]))
    assert len(seen) == len(runtime.sessions.model_calls(runtime.sid)) == 4
    assert len(runtime.sessions.turns.actions(turn["id"])) == 4
    for names, system in seen:
        assert "start_job" not in names and "start_batch" not in names and "fetch_rtl" not in names
        assert "task_status" in names and "execute_dependency_debug" in names
        assert "[task_state]" in system
    assert runtime.sessions.turns.get(turn["id"])["status"] == "complete"
    task = TaskStateStore(runtime.sessions.path).get_for_turn(turn["id"])
    assert task["status"] == "blocked"  # A finished conversation is not a finished task.
