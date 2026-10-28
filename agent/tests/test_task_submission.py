"""A new user submission owns its goal despite the old job reading focus."""
from types import SimpleNamespace

import pytest

from r2g_agent.agent_loop import SessionStore
from r2g_agent.task_state import TaskStateStore
from r2g_agent.task_runtime import task_for_turn


@pytest.fixture
def history():
    sessions = SessionStore()
    sid = sessions.create()
    root, _ = sessions.turns.create(sid, "old-request", {"message": "交付 counter 设计；保持接口"}, job_id="old-job")
    old = task_for_turn(sessions, root)
    assert sessions.turns.claim(root["id"])
    sessions.turns.finish(root["id"], "complete", {"type": "done"})
    return SimpleNamespace(sessions=sessions, sid=sid, root=root, old=old,
                           ledger=TaskStateStore(sessions.path))


def submitted(h, *, kind="user", tool="start_job", grant=False, error=False):
    payload = {"message": "现在换成 UART，交付其数据集"}
    if grant:
        payload["grant_id"] = "repair-grant"
    turn, _ = h.sessions.turns.create(h.sid, "new-request", payload, kind=kind, job_id="old-job")
    assert task_for_turn(h.sessions, turn)["id"] == h.old["id"]
    assert h.sessions.turns.claim(turn["id"])
    action = h.sessions.turns.begin_action(turn["id"], "model-call", {"id": "submit", "name": tool, "arguments": {}})
    assert h.sessions.turns.start_action(action["id"])
    result = {"error": "not submitted"} if error else {"job_id": "new-job"}
    h.sessions.turns.complete_action(action["id"], result, {}, [], "", job_id="new-job")
    return h.sessions.turns.get(turn["id"]), action


def test_committed_submission_forks_current_user_goal_and_preserves_old_task(history):
    h = history
    turn, action = submitted(h)
    # Reopening after action commit simulates the missing post-execution hook.
    recovered = SessionStore(h.sessions.path)
    new = task_for_turn(recovered, turn)
    assert new["id"] != h.old["id"]
    assert new["goal"] == turn["payload"]["message"]
    assert new["constraints"] == h.old["constraints"]
    assert new["job_id"] == "new-job" and new["status"] == "waiting_job"
    assert h.ledger.get_for_job(h.sid, "old-job")["id"] == h.old["id"]
    assert h.ledger.get(h.old["id"])["job_id"] == "old-job"
    assert task_for_turn(recovered, turn)["id"] == new["id"]
    assert h.ledger.fork_for_submission(turn["id"], "new-job", action["id"])["id"] == new["id"]
    with h.ledger.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM engineering_tasks").fetchone()[0] == 2


@pytest.mark.parametrize("options", [{"kind": "debug_review", "tool": "execute_dependency_debug"},
                                      {"kind": "repair_review", "tool": "execute_rtl_repair"},
                                      {"grant": True}, {"error": True}])
def test_repair_or_unconfirmed_submission_cannot_fork_goal(history, options):
    h = history
    turn, action = submitted(h, **options)
    with pytest.raises(ValueError):
        h.ledger.fork_for_submission(turn["id"], "new-job", action["id"])
    assert h.ledger.get_for_turn(turn["id"])["id"] == h.old["id"]


def test_repair_child_keeps_goal_and_old_user_turn_cannot_rewind_it(history):
    h = history
    turn, _ = submitted(h, kind="debug_review", tool="execute_dependency_debug")
    child_task = task_for_turn(h.sessions, turn)
    assert child_task["id"] == h.old["id"] and child_task["job_id"] == "new-job"
    assert child_task["goal"] == h.old["goal"]
    assert task_for_turn(h.sessions, h.root)["job_id"] == "new-job"


def test_submission_proof_cannot_be_reused_for_another_job(history):
    turn, action = submitted(history)
    with pytest.raises(ValueError):
        history.ledger.fork_for_submission(turn["id"], "unrelated-job", action["id"])
