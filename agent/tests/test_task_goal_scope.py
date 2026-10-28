"""A reading focus inherited from the web UI cannot redefine a new task goal."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from r2g_agent import agent_loop, server, task_runtime
from r2g_agent.event_bridge import BridgeWorker
from r2g_agent.jobs import JobStore
from r2g_agent.task_state import TaskStateStore


@pytest.fixture
def scope(monkeypatch):
    sessions, jobs = agent_loop.SessionStore(), JobStore()
    ledger = TaskStateStore(sessions.path)
    sid = sessions.create()
    parent, _ = sessions.turns.create(sid, "old-root", {"message": "交付 counter8；禁止修改 RTL 与验收规则"})
    assert sessions.turns.claim(parent["id"])
    jid = jobs.create_job("old-counter8", {"output": "graph"}, [{"name": "counter8"}])
    sessions.turns.link_job(parent["id"], jid)
    old = ledger.ensure(sid, parent["id"], parent["payload"]["message"], jid,
                        constraints=("禁止修改 RTL", "UNKNOWN/STALE 不是 PASS"))
    jobs.set_design(jid, "counter8", status="failed", stage="signoff")
    jobs.set_job(jid, "failed")
    ledger.observe(old["id"], jobs.job(jid))
    sessions.turns.finish(parent["id"], "complete", {"type": "done"})
    bridge = BridgeWorker(sessions, jobs, lambda: pytest.fail("no real model in metadata regression"), model_reviews=False)
    monkeypatch.setattr(server, "STATE", {"sessions": sessions, "bridge": bridge})
    return SimpleNamespace(sessions=sessions, jobs=jobs, ledger=ledger, sid=sid, old_job=jid,
                           old_task=ledger.get(old["id"]), parent=parent)


def focused_user_turn(scope, *, message="换一个 UART 设计并交付数据集", key="new-user"):
    # Exercise the actual HTTP handler's latest_job fallback, without consuming
    # the SSE generator or launching a model worker.
    async def body():
        return {"session_id": scope.sid, "request_id": key, "message": message}
    asyncio.run(server.chat(SimpleNamespace(json=body)))
    turn = scope.sessions.turns.by_key(key)
    assert turn["job_id"] == scope.old_job
    assert task_runtime.task_for_turn(scope.sessions, turn)["id"] == scope.old_task["id"]
    return turn


def completed_submission(scope, turn, *, tool="start_job", complete=True, job_id=None):
    assert scope.sessions.turns.claim(turn["id"])
    call = {"id": "submit", "name": tool, "arguments": {"designs": [{"example": "uart"}]}}
    action = scope.sessions.turns.begin_action(turn["id"], "model-1", call)
    assert scope.sessions.turns.start_action(action["id"])
    jid = job_id or scope.jobs.create_job("new-uart", {"output": "graph"}, [{"name": "uart"}])
    if complete:
        scope.sessions.turns.complete_action(action["id"], {"job_id": jid}, {}, [], "UART", job_id=jid)
    return action, jid


@pytest.mark.parametrize("tool", ["start_job", "start_batch"])
def test_successful_new_submission_forks_web_focus_into_immutable_new_goal(scope, tool):
    turn = focused_user_turn(scope)
    before = deepcopy(scope.ledger.get(scope.old_task["id"]))
    action, jid = completed_submission(scope, turn, tool=tool)
    forked = scope.ledger.fork_for_submission(turn["id"], jid, action["id"])
    assert forked["id"] != before["id"] and forked["root_turn_id"] == turn["id"]
    assert forked["goal"] == turn["payload"]["message"]
    assert forked["constraints"] == before["constraints"] and forked["profile"] == before["profile"]
    assert forked["job_id"] == jid and forked["status"] == "waiting_job"
    assert forked["observation"] is None and forked["pending_evidence"][0]["refs"][0]["job_id"] == jid
    assert scope.ledger.get_for_turn(turn["id"])["id"] == forked["id"]
    assert scope.ledger.get_for_turn(scope.parent["id"])["id"] == before["id"]
    assert scope.ledger.get_for_job(scope.sid, scope.old_job)["id"] == before["id"]
    assert scope.ledger.get_for_job(scope.sid, jid)["id"] == forked["id"]
    assert scope.ledger.get(before["id"]) == before
    assert scope.ledger.fork_for_submission(turn["id"], jid, action["id"])["id"] == forked["id"]
    with scope.ledger.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM engineering_tasks").fetchone()[0] == 2


def test_completed_action_recovers_task_fork_after_post_execution_hook_crash(scope):
    turn = focused_user_turn(scope)
    _, jid = completed_submission(scope, turn)
    # No executor hook ran after complete_action committed. The next context
    # read must reconcile from the durable completed action and exact job link.
    reloaded = agent_loop.SessionStore(scope.sessions.path)
    view = task_runtime.task_for_turn(reloaded, reloaded.turns.get(turn["id"]))
    assert view["id"] != scope.old_task["id"]
    assert view["goal"] == turn["payload"]["message"] and view["job_id"] == jid
    assert scope.ledger.get(scope.old_task["id"])["job_id"] == scope.old_job


def test_reading_old_job_without_new_completed_submission_keeps_original_task(scope):
    turn = focused_user_turn(scope, message="继续分析当前任务的失败证据")
    ledger = TaskStateStore(scope.sessions.path)
    task = task_runtime.task_for_turn(scope.sessions, turn)
    assert task["id"] == scope.old_task["id"] and task["goal"] == scope.old_task["goal"]
    assert ledger.get_for_job(scope.sid, scope.old_job)["id"] == task["id"]


def test_unfinished_submission_cannot_change_goal_or_job(scope):
    turn = focused_user_turn(scope)
    action, jid = completed_submission(scope, turn, complete=False)
    with pytest.raises(ValueError):
        scope.ledger.fork_for_submission(turn["id"], jid, action["id"])
    assert scope.ledger.get_for_turn(turn["id"])["id"] == scope.old_task["id"]
    assert scope.ledger.get(scope.old_task["id"])["job_id"] == scope.old_job


@pytest.mark.parametrize("kind,tool", [
    ("debug_review", "start_job"), ("repair_review", "start_batch"),
    ("user", "execute_rtl_repair"), ("user", "execute_dependency_debug"),
])
def test_debug_and_repair_children_cannot_rewrite_task_goal(scope, kind, tool):
    turn, _ = scope.sessions.turns.create(scope.sid, "child", {"message": "修复当前任务"},
                                        kind=kind, job_id=scope.old_job)
    scope.ledger.bind_turn(scope.old_task["id"], turn["id"])
    action, jid = completed_submission(scope, turn, tool=tool)
    with pytest.raises(ValueError):
        scope.ledger.fork_for_submission(turn["id"], jid, action["id"])
    assert scope.ledger.get_for_turn(turn["id"])["goal"] == scope.old_task["goal"]
    scope.ledger.bind_job(scope.old_task["id"], jid)
    assert scope.ledger.get_for_job(scope.sid, jid)["id"] == scope.old_task["id"]


def test_first_submission_keeps_its_existing_root_and_contract(scope):
    turn, _ = scope.sessions.turns.create(scope.sid, "standalone", {"message": "交付 UART"})
    task = scope.ledger.ensure(scope.sid, turn["id"], turn["payload"]["message"],
                               constraints=("保留原始输入",))
    action, jid = completed_submission(scope, turn)
    bound = scope.ledger.fork_for_submission(turn["id"], jid, action["id"])
    assert bound["id"] == task["id"] and bound["constraints"] == task["constraints"]
    assert bound["job_id"] == jid and bound["goal"] == "交付 UART"


def test_different_job_or_action_does_not_authorize_task_fork(scope):
    turn = focused_user_turn(scope)
    action, _ = completed_submission(scope, turn)
    with pytest.raises(ValueError):
        scope.ledger.fork_for_submission(turn["id"], scope.old_job, action["id"])
    with pytest.raises(ValueError):
        scope.ledger.fork_for_submission(scope.parent["id"], scope.old_job, action["id"])
    assert scope.ledger.get_for_turn(turn["id"])["id"] == scope.old_task["id"]
