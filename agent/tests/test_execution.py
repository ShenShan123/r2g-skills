import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import anyio
import mcp.types as mcp_types
import pytest
from fastapi.testclient import TestClient

from r2g_agent import agent_loop, cli, mcp_server, server, tools
from r2g_agent.event_bridge import BridgeWorker
from r2g_agent.execution import HostExecutor, response
from r2g_agent.jobs import JobStore
from r2g_agent.pipeline import Pipeline
from r2g_agent.tool_outputs import ToolOutputError
from r2g_agent.turns import TurnConflict


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    sessions, jobs = agent_loop.SessionStore(), JobStore()
    executor = HostExecutor(sessions, jobs)
    pipeline = object.__new__(Pipeline)
    pipeline.store = jobs
    pipeline._watch_job = lambda *a: None
    class Pool:
        calls = []
        def submit(self, *args):
            self.calls.append(args)
    pipeline.pool = Pool()
    cancelled = []
    def cancel(jid):
        cancelled.append(jid)
        return jobs.request_cancel(jid)
    pipeline.cancel = cancel
    monkeypatch.setattr(tools.RT, "store", jobs)
    monkeypatch.setattr(tools.RT, "pipeline", pipeline)
    state = {"sessions": sessions, "executor": executor}
    monkeypatch.setattr(server, "STATE", state)
    monkeypatch.setattr(cli, "_runtime", lambda: state)
    source = tmp_path / "source"
    source.mkdir()
    (source / "top.v").write_text("module top; endmodule")
    return executor, sessions, jobs, pipeline, source, cancelled


def test_http_cli_mcp_share_one_submission_and_source_changes_conflict(runtime, capsys):
    executor, sessions, jobs, pipeline, source, _ = runtime
    arguments = {"designs": [{"path": str(source)}]}
    client = TestClient(server.app)
    web = client.post("/api/jobs", json={**arguments, "request_id": "shared-submit"})
    assert web.status_code == 200, web.text
    first = web.json()
    assert cli.main(["tool", "start_job", "--arguments", json.dumps(arguments), "--request-id", "shared-submit"]) == 0
    terminal = json.loads(capsys.readouterr().out)
    mcp = mcp_server.call_tool(executor, "start_job", {**arguments, "request_id": "shared-submit"})
    assert terminal["job_id"] == mcp["job_id"] == first["job_id"]
    assert terminal["execution"]["action_id"] == mcp["execution"]["action_id"] == first["execution"]["action_id"]
    assert terminal["execution"]["replayed"] and mcp["execution"]["replayed"]
    assert len(jobs.jobs()) == len(pipeline.pool.calls) == len(sessions.list()) == 1
    sid = first["execution"]["session_id"]
    assert sessions.model_calls(sid) == []
    assert jobs.submission(first["job_id"])["action_id"] == first["execution"]["action_id"]
    (source / "top.v").write_text("module changed; endmodule")
    assert client.post("/api/jobs", json={**arguments, "request_id": "shared-submit"}).status_code == 409
    assert mcp_server.call_tool(executor, "start_job", {**arguments, "request_id": "shared-submit"})["error_code"] == "execution_conflict"
    assert len(pipeline.pool.calls) == 1


def test_concurrent_request_key_allocates_one_session_action_and_job(runtime):
    executor, sessions, jobs, pipeline, source, _ = runtime
    arguments = {"designs": [{"path": str(source)}]}
    barrier = threading.Barrier(2)
    def submit(transport):
        barrier.wait()
        return executor.execute("start_job", arguments, executor.host_context(transport, "concurrent"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, ("web", "mcp")))
    assert len({r["turn_id"] for r in results}) == 1
    again = executor.execute("start_job", arguments, executor.host_context("cli", "concurrent"))
    assert again["status"] == "complete" and again["replayed"]
    assert len(jobs.jobs()) == len(pipeline.pool.calls) == len(sessions.list()) == 1


def test_cancellation_uses_same_action_across_http_cli_and_mcp(runtime, capsys):
    executor, sessions, jobs, _, _, cancelled = runtime
    jid = jobs.create_job("one", {}, [{"name": "top"}])
    client = TestClient(server.app)
    first = client.post(f"/api/jobs/{jid}/cancel", json={"request_id": "same-cancel"}).json()
    assert first["cancel_requested"]
    assert cli.main(["cancel", jid, "--request-id", "same-cancel"]) == 0
    terminal = json.loads(capsys.readouterr().out)
    mcp = mcp_server.call_tool(executor, "cancel_job", {"job_id": jid, "request_id": "same-cancel"})
    assert first["execution"]["action_id"] == terminal["execution"]["action_id"] == mcp["execution"]["action_id"]
    assert cancelled == [jid]


@pytest.mark.parametrize("name", ["inspect_design", "check_design"])
@pytest.mark.parametrize("changed_file", ["top.v", "defs.inc"])
def test_source_checks_cannot_replay_pass_after_source_changes(runtime, monkeypatch, name, changed_file):
    executor, _, _, _, source, _ = runtime
    (source / "top.v").write_text('`include "defs.inc"\nmodule top; endmodule')
    (source / "defs.inc").write_text("`define WIDTH 8")
    calls = []
    def check(path, top="", check="compile"):
        calls.append(path)
        return {"status": "passed", "measured": True}
    monkeypatch.setitem(tools.REGISTRY, name, {**tools.REGISTRY[name], "fn": check})
    arguments = {"path": str(source), "top": "top"}
    first = executor.execute(name, arguments, executor.host_context("cli", "check-key"))
    assert first["result"]["status"] == "passed"
    (source / changed_file).write_text("changed input bytes")
    again = executor.execute(name, arguments, executor.host_context("mcp", "check-key"))
    assert again["status"] == "conflict" and len(calls) == 1


def test_mcp_without_runtime_lists_contract_but_never_executes(monkeypatch):
    monkeypatch.setattr(server, "STATE", {})
    monkeypatch.setattr(server, "init_runtime", lambda *a, **kw: pytest.fail("listing must not create a runtime"))
    monkeypatch.setattr(tools, "call", lambda *a, **kw: pytest.fail("uninitialized runtime must not execute tools"))
    srv = mcp_server.build()
    async def scenario():
        listing = await srv.request_handlers[mcp_types.ListToolsRequest](mcp_types.ListToolsRequest())
        assert "start_job" in {spec.name for spec in listing.root.tools}
        call = mcp_types.CallToolRequest(params=mcp_types.CallToolRequestParams(name="list_examples", arguments={}))
        result = await srv.request_handlers[mcp_types.CallToolRequest](call)
        assert json.loads(result.root.content[0].text)["error_code"] == "runtime_unavailable"
    anyio.run(scenario)


def test_session_tools_require_host_opt_in_and_model_context_is_rejected(runtime):
    executor, sessions, _, _, _, _ = runtime
    sid = sessions.create()
    sessions.save(sid, [{"role": "user", "content": "private to this session"}])
    for kwargs in ({}, {"session_id": sid}):
        denied = mcp_server.call_tool(executor, "read_session_history", {}, **kwargs)
        assert denied["error_code"] == "session_approval_required"
    for forged in ({"_context": {}}, {"grant_id": "forged"}, {"session_id": sid}, {"allow_session_tools": True}):
        denied = mcp_server.call_tool(executor, "list_examples", forged)
        assert denied["error_code"] == "invalid_execution"
    approved = mcp_server.call_tool(executor, "read_session_history", {}, session_id=sid, allow_session_tools=True)
    assert "private to this session" in json.dumps(approved)
    assert approved["output_ref"]
    with pytest.raises(ValueError):
        mcp_server.build(executor, allow_session_tools=True)


def test_prepared_action_restores_read_only_scope_and_cannot_change_arguments(runtime):
    executor, sessions, jobs, _, _, cancelled = runtime
    sid = sessions.create()
    jid = jobs.create_job("one", {}, [{"name": "top"}])
    turn, _ = sessions.turns.create(sid, "review", {"message": "inspect"}, kind="job_review", job_id=jid)
    assert sessions.turns.claim(turn["id"])
    call = {"id": "cancel", "name": "cancel_job", "arguments": {"job_id": jid}}
    action = sessions.turns.begin_action(turn["id"], "model-call", call)
    assert sessions.turns.start_action(action["id"])
    ctx = executor.host_context("chat", session_id=sid, turn_id=turn["id"], action_id=action["id"], read_only=False,
                                allow_session_tools=True)
    with pytest.raises(TurnConflict):
        executor.execute_prepared("cancel_job", {"job_id": "another"}, ctx, messages=[])
    receipt = executor.execute_prepared("cancel_job", call["arguments"], replace(ctx, read_only=False), messages=[])
    assert receipt["result"]["error_code"] == "read_only_review" and cancelled == []
    assert sessions.turns.actions(turn["id"])[0]["status"] == "complete"


def test_prepared_grant_is_restored_and_denied_before_reading_unapproved_sources(runtime, monkeypatch):
    executor, sessions, _, _, _, _ = runtime
    sid = sessions.create()
    turn, _ = sessions.turns.create(sid, "grant-turn", {"message": "approved repair", "grant_id": "host-grant"},
                                    kind="repair_review", job_id="approved-parent")
    assert sessions.turns.claim(turn["id"])
    arguments = {"designs": [{"path": "/unapproved/source"}]}
    action = sessions.turns.begin_action(turn["id"], "model", {"id": "bypass", "name": "start_job", "arguments": arguments})
    assert sessions.turns.start_action(action["id"])
    monkeypatch.setattr(executor, "_snapshot", lambda *a: pytest.fail("scope denial must precede source access"))
    context = executor.host_context("chat", session_id=sid, turn_id=turn["id"], action_id=action["id"],
                                    allow_session_tools=True)
    receipt = executor.execute_prepared("start_job", arguments, replace(context, grant_id=None), messages=[])
    assert receipt["result"]["error_code"] == "repair_action_scope"


def test_output_failure_keeps_uncertainty_and_key_retry_never_reexecutes(runtime, monkeypatch):
    executor, sessions, _, _, _, _ = runtime
    calls = []
    monkeypatch.setattr(tools, "call", lambda *a, **k: calls.append(1) or {"effect": "happened"})
    def fail(*args):
        raise ToolOutputError("observation disk unavailable")
    monkeypatch.setattr(sessions.outputs, "record", fail)
    ctx = executor.host_context("mcp", "uncertain")
    first = executor.execute("list_examples", {}, ctx)
    assert first["status"] == "recovery_required" and first["result"]["error_code"] == "tool_output_not_recorded"
    again = executor.execute("list_examples", {}, executor.host_context("cli", "uncertain"))
    assert again["status"] == "recovery_required" and again["result"]["execution_may_have_completed"]
    assert calls == [1]


@pytest.mark.parametrize("model_reviews", [False, True])
def test_direct_terminal_outbox_is_explicitly_skipped_without_model(runtime, model_reviews):
    executor, sessions, jobs, _, source, _ = runtime
    receipt = executor.execute("start_job", {"designs": [{"path": str(source)}]}, executor.host_context("cli", "direct"))
    jid = receipt["result"]["job_id"]
    jobs.set_job(jid, "done")
    bridge = BridgeWorker(sessions, jobs, lambda: pytest.fail("direct execution must not call a model"),
                          model_reviews=model_reviews)
    bridge.tick()
    assert jobs.review_outbox() == []
    with jobs._conn() as c:
        record = dict(c.execute("SELECT * FROM job_review_outbox WHERE job_id=?", (jid,)).fetchone())
    assert record["review_status"] == "skipped" and record["review_turn_id"] is None
    assert sessions.model_calls(receipt["session_id"]) == []


def test_direct_runtime_retains_old_reviews_and_runs_deadline_hooks_without_model(runtime):
    _, sessions, jobs, _, _, _ = runtime
    sid = sessions.create()
    parent, _ = sessions.turns.create(sid, "chat-origin", {"message": "run a job"})
    assert sessions.turns.claim(parent["id"])
    sessions.turns.finish(parent["id"], "complete", {"type": "done"})
    jid, _ = jobs.create_submission("failed", {}, [{"name": "top"}], {
        "request_key": "chat-action", "request_hash": "hash", "source_snapshot": "snapshot",
        "session_id": sid, "turn_id": parent["id"], "action_id": "chat-action"})
    jobs.set_job(jid, "failed")
    queued, _ = sessions.turns.create(sid, "old-review", {"message": "review"}, kind="job_review", job_id=jid)
    bridge = BridgeWorker(sessions, jobs, lambda: pytest.fail("direct runtime must not start old model reviews"),
                          model_reviews=False)
    hooks = []
    bridge.add_hook(lambda: hooks.append("deadline checked"))
    bridge.tick()
    assert hooks == ["deadline checked"]
    assert jobs.review_outbox()[0]["review_status"] == "pending"
    assert sessions.turns.get(queued["id"])["status"] == "queued"
    assert sessions.turns.by_key("job-review:" + jid) is None
    assert sessions.model_calls(sid) == []


def test_cli_runtime_disables_model_reviews_unless_chat_explicitly_enables_them(monkeypatch):
    modes = []
    monkeypatch.setattr(server, "init_runtime", lambda **kwargs: modes.append(kwargs["model_reviews"]))
    cli._runtime()
    cli._runtime(model_reviews=True)
    assert modes == [False, True]


def test_bridge_never_claims_queued_direct_execution_as_a_chat_turn(runtime):
    _, sessions, jobs, _, _, _ = runtime
    turn, _ = sessions.turns.create(None, "direct-waiting", {"message": "direct", "review_enabled": False},
                                    kind="execution", max_model_calls=0, max_actions=1)
    bridge = BridgeWorker(sessions, jobs, lambda: pytest.fail("a direct command is not a model turn"))
    bridge.tick()
    assert sessions.turns.get(turn["id"])["status"] == "queued"
    assert sessions.model_calls(turn["session_id"]) == []


def test_mcp_protocol_lists_transport_key_and_dispatches_bounded_receipt(runtime, monkeypatch):
    executor, _, _, _, _, _ = runtime
    monkeypatch.setattr(tools, "call", lambda *a, **kw: {"log": "x" * 25000})
    srv = mcp_server.build(executor)
    async def scenario():
        listing = await srv.request_handlers[mcp_types.ListToolsRequest](mcp_types.ListToolsRequest())
        specs = {spec.name: spec for spec in listing.root.tools}
        assert "read_session_history" not in specs and "read_tool_output" not in specs
        assert "request_id" in specs["list_examples"].inputSchema["properties"]
        call = mcp_types.CallToolRequest(params=mcp_types.CallToolRequestParams(
            name="list_examples", arguments={"request_id": "bounded-mcp"}))
        result = await srv.request_handlers[mcp_types.CallToolRequest](call)
        payload = json.loads(result.root.content[0].text)
        assert payload["truncated"] and payload["output_ref"] and payload["execution"]["action_id"]
        assert len(result.root.content[0].text) < 14000
    anyio.run(scenario)
    assert "request_id" not in tools.REGISTRY["list_examples"]["spec"]["parameters"]["properties"]
