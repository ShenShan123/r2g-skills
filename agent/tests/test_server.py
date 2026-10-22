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
             ("files", ("notes.txt", io.BytesIO(b"x"), "text/plain"))]
    r = c.post("/api/upload", files=files).json()
    assert r["files"] == ["evil.v"]            # no path escape, non-HDL dropped


def test_bad_job_request(monkeypatch):
    c = _client(monkeypatch)
    assert c.post("/api/jobs", json={"designs": [{"path": "/no/such/dir"}]}).status_code == 400
