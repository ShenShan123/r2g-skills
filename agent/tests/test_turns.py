import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from r2g_agent import agent_loop, tools
from r2g_agent.event_bridge import BridgeWorker
from r2g_agent.jobs import JobStore
from r2g_agent.pipeline import Pipeline
from r2g_agent.submissions import SubmissionConflict
from r2g_agent.tool_outputs import ToolContext, ToolOutputError
from r2g_agent.turns import SessionBusy, TurnConflict


def _turn(store, sid, key="request", **kwargs):
    return store.turns.create(sid, key, {"message": "inspect"}, **kwargs)[0]


def _wait(store, tid):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        row = store.turns.get(tid)
        if row["status"] not in ("queued", "running"):
            return row
        time.sleep(.01)
    pytest.fail("turn did not finish")


def test_submission_key_conflict_and_one_active_turn_across_connections():
    store = agent_loop.SessionStore()
    other = agent_loop.SessionStore(store.path)
    sid = store.create()
    first = _turn(store, sid)
    assert _turn(other, sid)["id"] == first["id"]
    with pytest.raises(TurnConflict):
        other.turns.create(sid, "request", {"message": "different"})
    with pytest.raises(SessionBusy):
        _turn(other, sid, "another")
    assert other.turns.claim(first["id"])
    assert not store.turns.claim(first["id"])
    queued = _turn(store, sid, "review", kind="job_review", allow_queue=True)
    assert not store.turns.claim(queued["id"])
    other.turns.finish(first["id"], "complete", {"type": "done"})
    assert store.turns.claim(queued["id"])


def test_action_is_durable_before_tool_and_completion_follows_observation(monkeypatch, fake_adapter):
    store = agent_loop.SessionStore()
    sid = store.create()
    seen = []
    def call(name, arguments, *, context):
        row = store.turns.actions(context.turn_id)[0]
        assert row["id"] == context.action_id and row["status"] == "executing"
        assert row["result"] is None and row["output_ref"] is None
        assert store.load(sid)[-1]["tool_calls"][0]["name"] == name
        seen.append(context)
        return {"measured": 17}
    monkeypatch.setattr(tools, "call", call)
    adapter = fake_adapter([[{"type": "tool_call", "id": "c1", "name": "list_examples", "arguments": {}}], []])
    events = list(agent_loop.run_turn(adapter, store, sid, "inspect"))
    turn_id = events[0]["turn_id"]
    action = store.turns.actions(turn_id)[0]
    assert action["status"] == "complete" and action["result"] == {"measured": 17}
    assert store.outputs.read(sid, action["output_ref"]["artifact_id"])["text"]
    assert len(seen) == 1
    assert store.turns.events(turn_id) == events
    assert all(record["turn_id"] == turn_id for record in store.model_calls(sid))


def test_failed_observation_is_uncertain_and_does_not_execute_again(monkeypatch, fake_adapter):
    store = agent_loop.SessionStore()
    sid = store.create()
    calls = []
    monkeypatch.setattr(tools, "call", lambda *a, **k: calls.append(1) or {"side_effect": "happened"})
    monkeypatch.setattr(store.outputs, "record", lambda *a: (_ for _ in ()).throw(ToolOutputError("disk failed")))
    adapter = fake_adapter([[{"type": "tool_call", "id": "c1", "name": "start_job", "arguments": {}}]])
    events = list(agent_loop.run_turn(adapter, store, sid, "run"))
    tid = events[0]["turn_id"]
    assert store.turns.get(tid)["status"] == "recovery_required"
    assert store.turns.actions(tid)[0]["status"] == "recovery_required"
    assert list(agent_loop.run_turn(adapter, store, sid, "run", turn_id=tid)) == []
    assert calls == [1]


@pytest.mark.parametrize("started", [False, True])
def test_dead_worker_preserves_intent_without_replaying_and_closes_tool_protocol(started):
    store = agent_loop.SessionStore()
    sid = store.create()
    turn = _turn(store, sid)
    assert store.turns.claim(turn["id"])
    cid = store.begin_model_call(sid, 0, {"usage": {"status": "unresolved"}}, turn_id=turn["id"])
    call = {"id": "c1", "name": "start_job", "arguments": {}}
    store.save(sid, [{"role": "assistant", "content": "", "tool_calls": [call], "turn_id": turn["id"]}])
    action = store.turns.begin_action(turn["id"], cid, call)
    if started:
        store.turns.start_action(action["id"])
    with store.turns.connection() as c:
        c.execute("UPDATE turns SET owner='999999999:0:old-boot' WHERE id=?", (turn["id"],))
    reopened = agent_loop.SessionStore(store.path)
    assert reopened.turns.recover() == [turn["id"]]
    assert reopened.turns.recover() == []
    assert reopened.turns.get(turn["id"])["status"] == "recovery_required"
    assert reopened.turns.actions(turn["id"])[0]["status"] == ("recovery_required" if started else "not_executed")
    assert reopened.model_calls(sid)[0]["status"] == "unresolved"
    observation = json.loads(reopened.load(sid)[-1]["content"])
    assert observation["execution_may_have_completed"] is started
    assert reopened.load(sid)[-1]["tool_call_id"] == "c1"


def _pipeline(store):
    # Execute the real submission transaction and scheduling branch, without EDA.
    pipeline = object.__new__(Pipeline)
    pipeline.store = store
    pipeline._watch_job = lambda *a: None
    class Pool:
        calls = []
        def submit(self, *args):
            self.calls.append(args)
    pipeline.pool = Pool()
    return pipeline


def test_job_creation_is_atomic_idempotent_and_source_changes_conflict(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "top.v").write_text('module top; initial $readmemh("contents.mem", x); endmodule')
    (source / "contents.mem").write_text("00")
    store = JobStore()
    pipeline = _pipeline(store)
    sources = [{"name": "top", "path": str(source), "top": "top"}]
    submission = {"request_key": "action:one", "session_id": "s1", "turn_id": "t1", "action_id": "a1"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(lambda _: pipeline.submit(sources, submission=submission), range(2)))
    assert jobs[0] == jobs[1] and len(store.jobs()) == 1 and len(pipeline.pool.calls) == 1
    assert store.submission(jobs[0])["action_id"] == "a1"
    assert store.job_for_submission("action:one")["job_id"] == jobs[0]
    assert store.job_for_submission("unknown") is None
    (source / "contents.mem").write_text("01")
    with pytest.raises(SubmissionConflict):
        pipeline.submit(sources, submission=submission)
    assert len(store.jobs()) == 1 and len(pipeline.pool.calls) == 1


def test_start_batch_reuses_key_and_rejects_changed_options(monkeypatch, tmp_path):
    source = tmp_path / "top"
    source.mkdir()
    (source / "top.v").write_text("module top; endmodule")
    store = JobStore()
    pipeline = _pipeline(store)
    monkeypatch.setattr(tools.RT, "pipeline", pipeline)
    monkeypatch.setattr(tools.RT, "store", store)
    first = tools.start_batch(str(source), _submission_key="batch-one")
    assert tools.start_batch(str(source), _submission_key="batch-one")["job_id"] == first["job_id"]
    assert tools.start_batch(str(source), output="image", _submission_key="batch-one")["error_code"] == "submission_conflict"
    assert len(pipeline.pool.calls) == 1


def test_outbox_survives_enqueue_crash_and_creates_only_one_read_only_review(monkeypatch, fake_adapter):
    sessions, jobs = agent_loop.SessionStore(), JobStore()
    sid = sessions.create()
    parent = _turn(sessions, sid)
    assert sessions.turns.claim(parent["id"])
    sessions.turns.finish(parent["id"], "complete", {"type": "done"})
    jid, _ = jobs.create_submission("failed", {}, [{"name": "top"}], {
        "request_key": "action:submit", "request_hash": "hash", "source_snapshot": "snapshot",
        "session_id": sid, "turn_id": parent["id"], "action_id": "submit"})
    sessions.turns.link_job(parent["id"], jid)
    jobs.set_job(jid, "failed")
    assert len(jobs.review_outbox()) == 1
    calls = []
    def factory():
        calls.append(1)
        return fake_adapter([[{"type": "tool_call", "id": "c1", "name": "start_job", "arguments": {}}], []])
    monkeypatch.setattr(tools.RT, "store", jobs)
    bridge = BridgeWorker(sessions, jobs, factory)
    reviewed = jobs.reviewed
    monkeypatch.setattr(jobs, "reviewed", lambda *a: (_ for _ in ()).throw(RuntimeError("crash after enqueue")))
    with pytest.raises(RuntimeError):
        bridge.tick()
    review = sessions.turns.by_key("job-review:" + jid)
    _wait(sessions, review["id"])
    monkeypatch.setattr(jobs, "reviewed", reviewed)
    bridge.tick()
    bridge.tick()
    assert calls == [1] and jobs.review_outbox() == []
    assert review["max_model_calls"] == 4
    observations = sessions.turns.actions(review["id"])
    assert observations[0]["result"]["error_code"] == "read_only_review"
    assert sessions.turns.linked_jobs(sid) == [jid]
    assert sessions.turns.context_state(review["id"]).goal == "inspect"


def test_live_worker_and_unknown_model_request_are_not_restarted(monkeypatch):
    sessions, jobs = agent_loop.SessionStore(), JobStore()
    sid = sessions.create()
    turn = _turn(sessions, sid, kind="job_review")
    assert sessions.turns.claim(turn["id"])
    assert sessions.turns.recover() == []
    sessions.begin_model_call(sid, 0, {"usage": {"status": "unresolved"}}, turn_id=turn["id"])
    with sessions.turns.connection() as c:
        c.execute("UPDATE turns SET owner='999999999:0:old-boot' WHERE id=?", (turn["id"],))
    bridge = BridgeWorker(sessions, jobs, lambda: pytest.fail("uncertain network request must not retry"))
    bridge.tick()
    assert sessions.turns.get(turn["id"])["status"] == "recovery_required"
    assert sessions.model_calls(sid)[0]["status"] == "unresolved"


def test_session_scope_and_host_context_cannot_be_forged():
    store = agent_loop.SessionStore()
    context = ToolContext(store.create(), store.outputs, turn_id="t", action_id="a", job_id="j", read_only=True)
    assert tools.call("start_job", {}, context=context)["error_code"] == "read_only_review"
    assert tools.call("job_status", {"job_id": "another"}, context=context)["error_code"] == "review_job_scope"
    assert "error" in tools.call("start_job", {"_context": {}}, context=context)


@pytest.mark.parametrize("window", ["intent", "observation", "complete"])
def test_action_crash_windows_keep_truth_and_never_replay(monkeypatch, fake_adapter, window):
    store = agent_loop.SessionStore()
    sid = store.create()
    calls = []
    monkeypatch.setattr(tools, "call", lambda *a, **kw: calls.append(1) or {"measured": 3})
    def crash(*args, **kwargs):
        raise RuntimeError("injected crash window")
    if window == "intent":
        monkeypatch.setattr(store.turns, "start_action", crash)
    elif window == "observation":
        monkeypatch.setattr(store.turns, "complete_action", crash)
    else:
        publish = store.turns.publish
        def fail_after_complete(tid, event):
            if event["type"] == "tool_result":
                crash()
            return publish(tid, event)
        monkeypatch.setattr(store.turns, "publish", fail_after_complete)
    adapter = fake_adapter([[{"type": "tool_call", "id": "c1", "name": "list_examples", "arguments": {}}]])
    events = list(agent_loop.run_turn(adapter, store, sid, "inspect"))
    tid = events[0]["turn_id"]
    action = store.turns.actions(tid)[0]
    assert action["status"] == {"intent": "not_executed", "observation": "recovery_required", "complete": "complete"}[window]
    assert calls == ([] if window == "intent" else [1])
    with store.outputs._connect() as c:
        assert c.execute("SELECT COUNT(*) FROM tool_outputs").fetchone()[0] == (0 if window == "intent" else 1)
    assert list(agent_loop.run_turn(adapter, store, sid, "inspect", turn_id=tid)) == []
    assert store.turns.get(tid)["status"] == "recovery_required"


def test_checked_repair_child_becomes_explicit_turn_focus(monkeypatch, fake_adapter):
    sessions, jobs = agent_loop.SessionStore(), JobStore()
    sid = sessions.create()
    child = jobs.create_job("checked repair child", {}, [{"name": "top"}])
    monkeypatch.setattr(tools.RT, "store", jobs)
    monkeypatch.setattr(tools, "call", lambda *a, **k: {"job_id": child})
    adapter = fake_adapter([[{"type": "tool_call", "id": "repair", "name": "execute_rtl_repair", "arguments": {}}], []])
    events = list(agent_loop.run_turn(adapter, sessions, sid, "execute approved repair"))
    tid = events[0]["turn_id"]
    assert sessions.turns.get(tid)["job_id"] == child
    assert sessions.turns.linked_jobs(sid) == [child]
    assert sessions.turns.context_state(tid).active_job_id == child


def test_initial_job_binding_is_atomic_with_turn_creation(monkeypatch):
    store = agent_loop.SessionStore()
    sid = store.create()
    original = store.turns._link_job
    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("crash after link insert")
    monkeypatch.setattr(store.turns, "_link_job", interrupted)
    with pytest.raises(RuntimeError):
        _turn(store, sid, job_id="parent")
    assert store.turns.by_key("request") is None
    assert store.turns.linked_jobs(sid) == []
    monkeypatch.setattr(store.turns, "_link_job", original)
    turn = _turn(store, sid, job_id="parent")
    assert store.turns.linked_jobs(sid) == ["parent"]
    assert _turn(store, sid, job_id="parent")["id"] == turn["id"]


def test_job_binding_and_action_completion_rollback_together(monkeypatch):
    store = agent_loop.SessionStore()
    sid = store.create()
    turn = _turn(store, sid)
    assert store.turns.claim(turn["id"])
    cid = store.begin_model_call(sid, 0, {}, turn_id=turn["id"])
    action = store.turns.begin_action(turn["id"], cid, {"id": "repair", "name": "execute_rtl_repair", "arguments": {}})
    assert store.turns.start_action(action["id"])
    def interrupted(*args, **kwargs):
        raise RuntimeError("crash before link")
    monkeypatch.setattr(store.turns, "_link_job", interrupted)
    with pytest.raises(RuntimeError):
        store.turns.complete_action(action["id"], {"job_id": "child"}, {}, [], "repair", job_id="child")
    assert store.turns.actions(turn["id"])[0]["status"] == "executing"
    assert store.turns.get(turn["id"])["job_id"] is None
    assert store.turns.linked_jobs(sid) == []


def test_live_worker_interruption_marks_pending_model_unresolved():
    store = agent_loop.SessionStore()
    sid = store.create()
    turn = _turn(store, sid)
    assert store.turns.claim(turn["id"])
    store.begin_model_call(sid, 0, {"usage": {"status": "unresolved"}}, turn_id=turn["id"])
    store.turns.finish(turn["id"], "recovery_required", {"type": "error", "message": "stream interrupted"})
    assert store.model_calls(sid)[0]["status"] == "unresolved"
    assert store.turns.recover() == []


def test_real_exited_worker_releases_session_without_replaying_actions():
    from r2g_agent.turns import owner_alive
    store = agent_loop.SessionStore()
    sid = store.create()
    turn = _turn(store, sid)
    assert store.turns.claim(turn["id"])
    cid = store.begin_model_call(sid, 0, {}, turn_id=turn["id"])
    action = store.turns.begin_action(turn["id"], cid, {"id": "call", "name": "start_job", "arguments": {}})
    assert store.turns.start_action(action["id"])
    child = subprocess.Popen([sys.executable, "-c",
        "from r2g_agent.turns import OWNER; import sys; print(OWNER, flush=True); sys.stdin.read(1)"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        owner = child.stdout.readline().strip()
        assert owner and owner_alive(owner)
        with store.turns.connection() as c:
            c.execute("UPDATE turns SET owner=? WHERE id=?", (owner, turn["id"]))
        assert store.turns.recover() == []
        child.communicate(input="x", timeout=5)
        assert child.returncode == 0 and not owner_alive(owner)
        assert store.turns.recover() == [turn["id"]]
        assert store.turns.actions(turn["id"])[0]["status"] == "recovery_required"
        assert store.model_calls(sid)[0]["status"] == "unresolved"
        assert store.turns.claim(_turn(store, sid, key="next-request")["id"])
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


@pytest.mark.parametrize("error,alive", [(FileNotFoundError(), False), (ProcessLookupError(), False),
                                        (PermissionError(), True)])
@pytest.mark.parametrize("wrapped", [False, True])
def test_owner_absence_and_unreadability_remain_distinct(monkeypatch, error, alive, wrapped):
    from r2g_agent import turns
    from r2g_agent.processes import ProcessInspectionError
    def inspect(pid):
        if wrapped:
            raise ProcessInspectionError("cannot inspect process") from error
        raise error
    monkeypatch.setattr(turns, "proc_start", inspect)
    assert turns.owner_alive("123:1:boot") is alive
