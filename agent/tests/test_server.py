import io

from fastapi.testclient import TestClient

from r2g_agent import config, server


def _client(monkeypatch, fake=None):
    server.STATE.clear()
    server.init_runtime(config.Config())
    if fake is not None:
        monkeypatch.setattr(server, "make_adapter", lambda mc: fake)
    return TestClient(server.app)


def test_health_and_examples(monkeypatch):
    c = _client(monkeypatch)
    h = c.get("/api/health").json()
    assert [s["id"] for s in h["stages"]][:3] == ["prepare", "synth", "size"]
    assert {e["name"] for e in c.get("/api/examples").json()["examples"]} >= {"counter8"}


def test_api_key_never_returned(monkeypatch):
    c = _client(monkeypatch)
    r = c.post("/api/config", json={"model": {"provider": "deepseek", "api_key": "sk-secret-123"}}).json()
    assert "sk-secret" not in str(r) and r["model_resolved"]["has_key"] is True
    assert "sk-secret" not in str(c.get("/api/config").json())
    assert "sk-secret" not in (config.config_path().read_text())


def test_chat_streams_events(monkeypatch, fake_adapter):
    fake = fake_adapter([[{"type": "tool_call", "id": "a", "name": "list_examples", "arguments": {}}],
                         [{"type": "text", "delta": "好的"}]])
    c = _client(monkeypatch, fake)
    body = c.post("/api/chat", json={"message": "示例"}).text
    assert '"type": "session"' in body and '"type": "tool_result"' in body and '"type": "done"' in body


def test_upload_keeps_only_hdl_and_sanitises(monkeypatch):
    c = _client(monkeypatch)
    files = [("files", ("../../evil.v", io.BytesIO(b"module m; endmodule"), "text/plain")),
             ("files", ("rom.txt", io.BytesIO(b"00"), "text/plain")),
             ("files", ("notes.pdf", io.BytesIO(b"x"), "application/pdf"))]
    r = c.post("/api/upload", files=files).json()
    assert r["files"] == ["evil.v", "rom.txt"]   # no path escape; a $readmem image is kept
    assert r["skipped"] == ["notes.pdf"]


def test_bad_job_request(monkeypatch):
    c = _client(monkeypatch)
    assert c.post("/api/jobs", json={"designs": [{"path": "/no/such/dir"}]}).status_code == 400


def test_tool_output_api_survives_session_reload_and_enforces_scope(monkeypatch, fake_adapter):
    import json
    from r2g_agent import agent_loop, tools
    fake = fake_adapter([[{"type": "tool_call", "id": "a", "name": "list_examples", "arguments": {}}], []])
    c = _client(monkeypatch, fake)
    original = {"log": "first\n" + "x" * 20000 + "ERROR_IN_MIDDLE" + "y" * 20000 + "\nlast"}
    monkeypatch.setattr(tools, "call", lambda *a, **kw: original)
    stream = c.post("/api/chat", json={"message": "show log"}).text
    events = [json.loads(line[6:]) for line in stream.splitlines() if line.startswith("data: ")]
    sid = next(e["session_id"] for e in events if e["type"] == "session")
    result = next(e for e in events if e["type"] == "tool_result")
    aid = result["output_ref"]["artifact_id"]
    assert result["result"]["truncated"]
    server.STATE["sessions"] = agent_loop.SessionStore(server.STATE["sessions"].path)
    saved = c.get(f"/api/sessions/{sid}").json()
    assert saved["messages"][-2]["output_ref"]["artifact_id"] == aid
    url = f"/api/sessions/{sid}/tool-outputs/{aid}"
    read = c.get(url, params={"query": "ERROR_IN_MIDDLE", "limit": 40})
    assert read.status_code == 200 and read.json()["text"].startswith("ERROR_IN_MIDDLE")
    assert c.get(url, params={"limit": 4001}).status_code == 422
    other = server.STATE["sessions"].create()
    assert c.get(f"/api/sessions/{other}/tool-outputs/{aid}").status_code == 404
    path = server.STATE["sessions"].outputs.directory / (aid + ".json")
    path.write_text("corrupted")
    assert c.get(url).status_code == 404
