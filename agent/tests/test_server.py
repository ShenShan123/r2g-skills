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


def test_platform_params_and_invalid_selection(monkeypatch):
    c = _client(monkeypatch)
    for platform, first_period, floor in (("nangate45", 0.5, 32), ("sky130hd", 1.8, 32), ("gf180", 2.2, 110)):
        r = c.get("/api/params", params={"platform": platform})
        assert r.status_code == 200
        defaults = r.json()["defaults"]
        assert defaults["period_tiers"][0][1] == first_period
        assert defaults["pdn_floor_um"] == floor
    assert c.get("/api/params?platform=unknown").status_code == 400


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


def test_cancel_is_immediate_for_queued_and_missing_job_is_404(monkeypatch):
    c = _client(monkeypatch)
    store = server.tools.RT.store
    jid = store.create_job("queued", {}, [{"name": "a"}])
    store.set_job(jid, "running")
    assert c.post("/api/jobs/no-such-job/cancel").status_code == 404
    result = c.post(f"/api/jobs/{jid}/cancel")
    assert result.status_code == 200 and result.json()["status"] == "cancelled"
    assert c.get(f"/api/jobs/{jid}").json()["designs"][0]["status"] == "cancelled"
    assert c.post(f"/api/jobs/{jid}/cancel").json() == result.json()
