"""Context contract regressions; scripted models and job fixtures, no EDA."""
import json
import sqlite3

import pytest

from r2g_agent import agent_loop, config, tools
from r2g_agent.jobs import JobStore
from r2g_agent.llm.anthropic import AnthropicAdapter
from r2g_agent.llm.openai_compat import OpenAICompatAdapter
from r2g_agent.model_context import prepare_context, related_jobs
from r2g_agent.tool_outputs import ToolContext


def _exchange(number, jid=None):
    call = {"id": f"c{number}", "name": "job_status" if jid else "list_examples",
            "arguments": {"job_id": jid} if jid else {}}
    return [{"role": "user", "content": f"request {number}"},
            {"role": "assistant", "content": "checking", "tool_calls": [call]},
            {"role": "tool", "tool_call_id": call["id"], "name": call["name"],
             "content": json.dumps({"job_id": jid, "status": "running"} if jid else {"log": "OLD_LOG_" * 1000})},
            {"role": "assistant", "content": "running"}]


def _runtime(messages):
    return [json.loads(m["content"].split("\n")[-1]) for m in messages
            if m.get("content", "").startswith("【运行时上下文")]


def _assert_pairs(messages):
    pending = set()
    for m in messages:
        if m["role"] == "tool":
            assert m["tool_call_id"] in pending
            pending.remove(m["tool_call_id"])
        else:
            assert not pending
            pending = {c["id"] for c in m.get("tool_calls", [])}
    assert not pending


def test_long_history_is_bounded_without_rewriting_archive_or_splitting_tools(fake_adapter):
    ss = agent_loop.SessionStore()
    sid = ss.create()
    history = [m for i in range(80) for m in _exchange(i)]
    history[0]["content"] = "Original constraint: never change RTL or retry without approval."
    ss.save(sid, history)
    ad = fake_adapter([[{"type": "text", "delta": "answer"}]])
    # The registry now includes repair proposals and their bounded schemas;
    # leave room for those plus the protected last tool group and user inputs.
    ad.mc = config.ModelConfig(context_max_chars=48000, context_recent_turns=3)
    list(agent_loop.run_turn(ad, ss, sid, "latest request, kept exactly"))
    view = ad.seen[0]
    assert view[-1] == {"role": "user", "content": "latest request, kept exactly"}
    assert len(json.dumps(view)) < 48000
    assert len(view) < 20 and len(ss.load(sid)) == len(history) + 2
    assert ss.load(sid)[:len(history)] == history
    index = _runtime(view)[0]["history_index"]
    assert index["incomplete"] and index["omitted_turns"] > 0
    assert "never change RTL" in json.dumps(index)
    _assert_pairs(view)
    # Both protocols accept a complete tool exchange, with no invented result IDs.
    assert OpenAICompatAdapter.to_wire(view, "policy")[0]["role"] == "system"
    assert AnthropicAdapter.to_wire(view)[-1]["role"] == "user"
    record = ss.model_calls(sid)[0]
    assert record["context"]["context_chars"] <= 48000
    assert record["context"]["archive_message_count"] == len(history) + 1


def test_history_reader_is_paged_and_session_scoped_after_restart():
    ss = agent_loop.SessionStore()
    first, other = ss.create(), ss.create()
    ss.save(first, [{"role": "user", "content": "x" * 12000 + "EXACT_OLD_CONSTRAINT: 不允许重跑"}])
    ss.save(other, [{"role": "user", "content": "separate conversation"}])
    ss = agent_loop.SessionStore(ss.path)
    ctx = ToolContext(first, ss.outputs, ss.read_history)
    result = tools.call("read_session_history", {"query": "EXACT_OLD_CONSTRAINT", "limit": 80}, context=ctx)
    assert result["text"].startswith("EXACT_OLD_CONSTRAINT: 不允许重跑") and result["match"] > 12000
    assert len(result["text"]) <= 80
    assert tools.call("read_session_history", {"query": "EXACT_OLD_CONSTRAINT"},
                      context=ToolContext(other, ss.outputs, ss.read_history))["match"] == -1
    for bad in ({"session_id": first}, {"limit": 4001}, {"offset": -1}, {"query": "x" * 257}):
        assert "error" in tools.call("read_session_history", bad, context=ctx)
    assert "read_session_history" not in {s["name"] for s in tools.specs()}


@pytest.mark.parametrize("status", ["done", "failed", "cancelled", "recovery_required"])
def test_latest_job_state_reaches_next_model_call_without_frontend_polling(monkeypatch, fake_adapter, status):
    st = JobStore()
    monkeypatch.setattr(tools.RT, "store", st)
    jid = st.create_job("counter", {}, [{"name": "top"}])
    st.set_job(jid, "running")
    ss = agent_loop.SessionStore()
    sid = ss.create()
    ss.save(sid, _exchange(1, jid))
    # A global job that was never explicitly used by this session must stay out.
    unrelated = st.create_job("OTHER_SESSION_ONLY", {}, [{"name": "private"}])
    before = fake_adapter([[]])
    list(agent_loop.run_turn(before, ss, sid, "still running?"))
    previous = _runtime(before.seen[0])[0]["related_jobs"][0]
    assert previous["status"] == "running"
    detail = {"reason": "recorded failure rc=1", "failed_stage": "synth"} if status == "failed" else {}
    if status == "done":
        detail = {"signoff": {"drc": "clean", "lvs": "clean"}, "verify": {"passed": 296, "total": 296}}
    st.set_design(jid, "top", status={"done": "passed"}.get(status, status), stage="verify", detail=detail,
                  event=("synth", "fail" if status == "failed" else "done", "observed outcome",
                         {"log_tail": "mkdir: Permission denied\napi_key=do-not-copy"}))
    st.set_job(jid, status, {"passed": int(status == "done"), "total": 1},
               event=("job", "done", "job outcome", {}))
    reopened = agent_loop.SessionStore(ss.path)
    after = fake_adapter([[]])
    list(agent_loop.run_turn(after, reopened, sid, "what is the result?"))
    snapshots = _runtime(after.seen[0])
    assert len(snapshots) == 1
    jobs = snapshots[0]["related_jobs"]
    assert [j["job_id"] for j in jobs] == [jid] and unrelated not in str(jobs)
    job = jobs[0]
    assert job["status"] == status and job["event_cursor"] > previous["event_cursor"]
    if status == "failed":
        assert job["designs"][0]["reason"] == "recorded failure rc=1"
        assert "Permission denied" in json.dumps(job) and "do-not-copy" not in json.dumps(job)
    if status == "done":
        assert job["designs"][0]["verify"] == {"passed": 296, "total": 296}
    assert "运行时上下文" not in json.dumps(reopened.load(sid), ensure_ascii=False)
    persisted = reopened.model_calls(sid)[-1]["context"]["job_observations"]
    assert persisted == jobs  # provenance saved before the provider request


def test_new_job_is_refreshed_between_tool_steps(monkeypatch, fake_adapter):
    st = JobStore()
    monkeypatch.setattr(tools.RT, "store", st)
    jid = st.create_job("submitted", {}, [{"name": "top"}])
    monkeypatch.setattr(tools, "call", lambda *a, **kw: {"job_id": jid, "note": "running in background",
                                                       "designs": ["long-name" * 10] * 1000})
    ad = fake_adapter([[{"type": "tool_call", "id": "submit", "name": "start_job",
                        "arguments": {"designs": [{"example": "counter8"}]}}], []])
    ss = agent_loop.SessionStore()
    sid = ss.create()
    list(agent_loop.run_turn(ad, ss, sid, "run"))
    assert not _runtime(ad.seen[0])[0]["related_jobs"]
    assert _runtime(ad.seen[0])[0]["controller_state"]["source"]["turn_id"]
    assert _runtime(ad.seen[1])[0]["related_jobs"][0]["status"] == "pending"
    assert ad.seen[1][-1]["role"] == "tool"
    _assert_pairs(ad.seen[1])


def test_prose_global_lists_and_failed_tools_do_not_link_jobs():
    history = [{"role": "user", "content": "please see other-job"},
               {"role": "assistant", "content": "job other-job", "tool_calls": [
                   {"id": "list", "name": "list_jobs", "arguments": {}}]},
               {"role": "tool", "name": "list_jobs", "tool_call_id": "list",
                "content": json.dumps({"jobs": [{"job_id": "other-job"}]})},
               *_exchange(1, "missing")]
    history[-2]["content"] = json.dumps({"error": "no such job"})
    assert related_jobs(history) == []


def test_large_batch_and_old_jobs_survive_history_eviction_with_bounded_evidence():
    st = JobStore()
    jid = st.create_job("big batch", {}, [{"name": f"d{i}"} for i in range(500)])
    st.set_design(jid, "d499", status="failed", detail={"reason": "FAIL_MARKER" + "X" * 50000},
                  event=("synth", "fail", "actual failure", {"log_tail": "L" * 50000}))
    history = [*_exchange(1, jid), *[m for i in range(2, 70) for m in _exchange(i)],
               {"role": "user", "content": "result?"}]
    view, record = prepare_context(history, st, config.ModelConfig(context_max_chars=32000), [], "policy")
    job = _runtime(view)[0]["related_jobs"][0]
    assert job["counts"] == {"failed": 1, "pending": 499}
    assert job["omitted_design_count"] == 496 and "FAIL_MARKER" in str(job)
    assert len(json.dumps(job)) < 5000 and record["context_chars"] <= 32000
    assert not any(m.get("tool_call_id") == "c1" for m in view)


def test_oversized_current_turn_stops_without_network_tools_or_history_loss(fake_adapter, monkeypatch):
    ss = agent_loop.SessionStore()
    sid = ss.create()
    ad = fake_adapter([])
    ad.mc = config.ModelConfig(context_max_chars=24000)
    monkeypatch.setattr(tools, "call", lambda *a, **kw: pytest.fail("must not execute"))
    text = "large current request" * 4000
    events = list(agent_loop.run_turn(ad, ss, sid, text))
    assert events[-1]["type"] == "error" and "上下文" in events[-1]["message"]
    assert not ad.seen and not ss.model_calls(sid)
    saved = ss.load(sid)
    assert len(saved) == 1 and saved[0]["role"] == "user" and saved[0]["content"] == text


def test_context_configuration_and_additive_model_call_migration(tmp_path):
    cfg = config.Config(model=config.ModelConfig(context_max_chars=48000, context_recent_turns=2))
    config.save(cfg)
    assert config.load().model.resolved().context_max_chars == 48000
    assert config.load().model.resolved().context_recent_turns == 2
    old = tmp_path / "old.sqlite"
    with sqlite3.connect(old) as conn:
        conn.execute("CREATE TABLE model_calls (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, "
                     "step INTEGER NOT NULL, status TEXT NOT NULL, started REAL NOT NULL, "
                     "finished REAL, metadata TEXT NOT NULL)")
        conn.execute("INSERT INTO model_calls VALUES ('old','s',0,'complete',1,2,'{}')")
    ss = agent_loop.SessionStore(old)
    assert ss.model_calls("s")[0]["context"] == {}
    assert ss.model_calls("s")[0]["status"] == "complete"
