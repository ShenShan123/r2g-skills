import errno
import hashlib
import json
import sqlite3

import pytest

from r2g_agent import agent_loop, tools
from r2g_agent.tool_outputs import MAX_INLINE_CHARS, MAX_OUTPUT_BYTES, ToolContext, ToolOutputError


def long_observation():
    return {"log": "BEGIN\n" + "布线记录\n" * 6000 + "ERROR_LOCATION_317\n" + "ok\n" * 6000 + "END"}


def test_long_result_is_readable_after_reopen_and_in_model_context(fake_adapter, monkeypatch):
    ss = agent_loop.SessionStore()
    sid = ss.create()
    original = long_observation()
    monkeypatch.setattr(tools, "call", lambda *a, **kw: original)
    adapter = fake_adapter([[{"type": "tool_call", "id": "c1", "name": "read_log", "arguments": {"job_id": "synthetic", "design": "top", "stage": "synth"}}], []])
    events = list(agent_loop.run_turn(adapter, ss, sid, "locate the error"))
    result_event = next(e for e in events if e["type"] == "tool_result")
    view = result_event["result"]
    assert view["truncated"] and "BEGIN" in view["head"] and "END" in view["tail"]
    content = adapter.seen[1][-1]["content"]
    assert len(content) <= MAX_INLINE_CHARS
    assert json.loads(content) == view
    assert ss.load(sid)[-2]["output_ref"] == result_event["output_ref"]
    reopened = agent_loop.SessionStore(ss.path)
    ref = result_event["output_ref"]
    piece = reopened.outputs.read(sid, ref["artifact_id"], query="ERROR_LOCATION_317", limit=100)
    assert piece["text"].startswith("ERROR_LOCATION_317")
    assert piece["match"] > 12000
    raw = json.dumps(original, ensure_ascii=False)
    assert ref["sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    assert ref["original_bytes"] == len(raw.encode())
    assert "ERROR_LOCATION_317" not in raw[:12000]  # legacy head-only clipping lost it


def test_small_result_keeps_existing_wire_shape():
    ss = agent_loop.SessionStore()
    result = {"job_id": "job1", "note": "queued"}
    view, ref = ss.outputs.record(ss.create(), "c1", "start_job", result)
    assert view == result
    assert ref["redacted"] is False


def test_session_scope_is_host_supplied_and_cannot_be_overridden():
    ss = agent_loop.SessionStore()
    first, second = ss.create(), ss.create()
    _, ref = ss.outputs.record(first, "c1", "read_log", {"log": "hello"})
    args = {"artifact_id": ref["artifact_id"]}
    assert "hello" in tools.call("read_tool_output", args, context=ToolContext(first, ss.outputs))["text"]
    assert "error" in tools.call("read_tool_output", args, context=ToolContext(second, ss.outputs))
    assert "error" in tools.call("read_tool_output", args)
    assert "error" in tools.call("read_tool_output", dict(args, _context="forged"),
                                context=ToolContext(second, ss.outputs))
    assert "error" in tools.call("read_tool_output", dict(args, session_id=first),
                                context=ToolContext(second, ss.outputs))


def test_session_reader_is_only_advertised_to_chat():
    assert "read_tool_output" not in {s["name"] for s in tools.specs()}
    assert "read_tool_output" in {s["name"] for s in tools.specs(session_scoped=True)}


@pytest.mark.parametrize("change", ["tamper", "missing", "symlink"])
def test_changed_or_missing_output_never_returns_trusted_data(tmp_path, change):
    ss = agent_loop.SessionStore()
    sid = ss.create()
    _, ref = ss.outputs.record(sid, "c1", "read_log", {"log": "hello"})
    path = ss.outputs.directory / (ref["artifact_id"] + ".json")
    if change == "tamper":
        path.write_text('"fake pass"')
    else:
        path.unlink()
        if change == "symlink":
            secret = tmp_path / "secret"
            secret.write_text("not a tool observation")
            path.symlink_to(secret)
    with pytest.raises(ToolOutputError):
        ss.outputs.read(sid, ref["artifact_id"])


@pytest.mark.parametrize("args", [
    {"artifact_id": "../../secret"}, {"offset": -1}, {"offset": True},
    {"limit": 4001}, {"limit": 0}, {"query": "x" * 257}, {"query": []},
])
def test_reader_enforces_bounds(args):
    ss = agent_loop.SessionStore()
    sid = ss.create()
    _, ref = ss.outputs.record(sid, "c1", "read_log", {})
    with pytest.raises(ToolOutputError):
        ss.outputs.read(sid, **dict({"artifact_id": ref["artifact_id"]}, **args))


def test_unicode_paging_and_no_match():
    ss = agent_loop.SessionStore()
    sid = ss.create()
    result = {"log": "汉字é\n" * 4000}
    _, ref = ss.outputs.record(sid, "c1", "read_log", result)
    pieces, offset = [], 0
    while offset is not None:
        page = ss.outputs.read(sid, ref["artifact_id"], offset=offset, limit=317)
        pieces.append(page["text"])
        offset = page["next_offset"]
    assert json.loads("".join(pieces)) == result
    missing = ss.outputs.read(sid, ref["artifact_id"], query="not found")
    assert missing["text"] == "" and missing["match"] == -1 and missing["next_offset"] is None


def test_credentials_redacted_before_model_ui_or_disk(fake_adapter, monkeypatch):
    ss = agent_loop.SessionStore()
    sid = ss.create()
    result = {"api_key": "secret-a", "nested": [{"password": "secret-b"}],
              "log": "Authorization: Bearer secret-c\napi_key=secret-d\nhello"}
    monkeypatch.setattr(tools, "call", lambda *a, **kw: result)
    adapter = fake_adapter([[{"type": "tool_call", "id": "c1", "name": "read_log", "arguments": {"job_id": "synthetic", "design": "top", "stage": "synth"}}], []])
    events = list(agent_loop.run_turn(adapter, ss, sid, "read"))
    result_event = next(e for e in events if e["type"] == "tool_result")
    ref = result_event["output_ref"]
    assert ref["redacted"]
    stored = ss.outputs.read(sid, ref["artifact_id"])["text"]
    for secret in ("secret-a", "secret-b", "secret-c", "secret-d"):
        assert secret not in json.dumps(events)
        assert secret not in json.dumps(adapter.seen)
        assert secret not in json.dumps(ss.load(sid))
        assert secret not in stored


def test_disk_full_stops_without_repeating_action(fake_adapter, monkeypatch):
    import r2g_agent.tool_outputs as output_module
    ss = agent_loop.SessionStore()
    sid = ss.create()
    executed = []
    monkeypatch.setattr(tools, "call", lambda *a, **kw: executed.append(a) or {"job_id": "created"})
    def full(*a, **kw):
        raise OSError(errno.ENOSPC, "disk full")
    monkeypatch.setattr(output_module.os, "replace", full)
    adapter = fake_adapter([[{"type": "tool_call", "id": "c1", "name": "start_job", "arguments": {"designs": [{"example": "counter8"}]}},
                             {"type": "tool_call", "id": "c2", "name": "start_job", "arguments": {"designs": [{"example": "counter8"}]}}], []])
    events = list(agent_loop.run_turn(adapter, ss, sid, "run"))
    assert len(executed) == len(adapter.seen) == 1
    assert events[-1]["type"] == "error"
    failure = json.loads(ss.load(sid)[-2]["content"])
    assert failure["error_code"] == "tool_output_not_recorded" and failure["execution_may_have_completed"]
    assert json.loads(ss.load(sid)[-1]["content"])["error_code"] == "not_executed"
    assert list(ss.outputs.directory.iterdir()) == []


def test_large_error_preserves_failure_signal_and_json_bounds():
    ss = agent_loop.SessionStore()
    view, _ = ss.outputs.record(ss.create(), "c", "read_log", {"error": "\x00\"\\" * 10000})
    assert "error" in view and view["truncated"]
    assert len(json.dumps(view, ensure_ascii=False)) <= MAX_INLINE_CHARS


def test_oversize_observation_is_refused_without_storing_partial_data():
    ss = agent_loop.SessionStore()
    with pytest.raises(ToolOutputError, match="storage limit"):
        ss.outputs.record(ss.create(), "c", "read_log", {"log": "x" * MAX_OUTPUT_BYTES})
    with ss.outputs._connect() as c:
        assert c.execute("SELECT COUNT(*) FROM tool_outputs").fetchone()[0] == 0
    assert not ss.outputs.directory.exists()


def test_legacy_session_survives_additive_upgrade_and_backup(tmp_path):
    path = tmp_path / "sessions.sqlite"
    old = [{"role": "user", "content": "existing conversation"}]
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, created REAL, updated REAL, title TEXT, messages TEXT)")
        c.execute("INSERT INTO sessions VALUES ('old',1,1,'title',?)", (json.dumps(old),))
        with sqlite3.connect(tmp_path / "before.sqlite") as dest:
            c.commit()
            c.backup(dest)
    ss = agent_loop.SessionStore(path)
    assert ss.load("old") == old
    ss.outputs.record("old", "c", "read_log", {"text": "new observation"})
    # Old readers still use the same table; additive data does not replace it.
    with sqlite3.connect(path) as c:
        assert json.loads(c.execute("SELECT messages FROM sessions WHERE id='old'").fetchone()[0]) == old
    with sqlite3.connect(tmp_path / "before.sqlite") as c:
        assert json.loads(c.execute("SELECT messages FROM sessions WHERE id='old'").fetchone()[0]) == old


def test_unknown_session_cannot_execute(fake_adapter, monkeypatch):
    ss = agent_loop.SessionStore()
    monkeypatch.setattr(tools, "call", lambda *a, **kw: pytest.fail("must not execute"))
    adapter = fake_adapter([[{"type": "tool_call", "id": "c1", "name": "start_job", "arguments": {}}]])
    assert list(agent_loop.run_turn(adapter, ss, "unknown", "run")) == [{"type": "error", "message": "unknown session"}]
    assert adapter.seen == []
