import json
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from r2g_agent import config, server


def _events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def _client(monkeypatch, adapter):
    server.STATE.clear()
    server.init_runtime(config.Config(ingest=False))
    monkeypatch.setattr(server, "make_adapter", lambda mc: adapter)
    return TestClient(server.app)


def test_retry_replays_same_turn_and_conflicting_request_is_409(monkeypatch, fake_adapter):
    adapter = fake_adapter([[{"type": "text", "delta": "public result"}]])
    client = _client(monkeypatch, adapter)
    try:
        request = {"message": "inspect", "request_id": "client-request-1"}
        first = _events(client.post("/api/chat", json=request))
        second = _events(client.post("/api/chat", json=request))
        assert first == second and len(adapter.seen) == 1
        tid, sid = first[0]["turn_id"], first[0]["session_id"]
        assert all(e["turn_id"] == tid and e["session_id"] == sid for e in first)
        assert client.post("/api/chat", json={**request, "message": "different"}).status_code == 409
        persisted = client.get(f"/api/turns/{tid}/events", params={"after": first[1]["event_id"]}).json()
        assert persisted["turn"]["status"] == "complete"
        assert persisted["events"] == first[2:]
        resumed = _events(client.get(f"/api/turns/{tid}/stream", params={"after": first[1]["event_id"]}))
        assert resumed == [first[0], *first[2:]]
        assert client.get(f"/api/sessions/{sid}").json()["turns"][0]["id"] == tid
    finally:
        server.STATE["bridge"].stop()


def test_same_session_parallel_turn_rejected_before_second_model_request(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []
    class Adapter:
        def stream(self, *a):
            calls.append(1)
            entered.set()
            assert release.wait(5)
            yield {"type": "text", "delta": "finished"}
            yield {"type": "done", "metadata": {"stream_complete": True}}
    client = _client(monkeypatch, Adapter())
    sid = server.STATE["sessions"].create()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(client.post, "/api/chat", json={"message": "first", "session_id": sid, "request_id": "first"})
            assert entered.wait(5)
            blocked = client.post("/api/chat", json={"message": "second", "session_id": sid, "request_id": "second"})
            assert blocked.status_code == 409
            assert len(server.STATE["sessions"].model_calls(sid)) == 1
            release.set()
            assert first.result(timeout=5).status_code == 200
        assert calls == [1]
    finally:
        release.set()
        server.STATE["bridge"].stop()


def test_job_binding_is_immutable_for_retry_even_after_action_links_child(monkeypatch, fake_adapter):
    adapter = fake_adapter([[]])
    client = _client(monkeypatch, adapter)
    try:
        request = {"message": "run", "request_id": "same-input"}
        first = _events(client.post("/api/chat", json=request))
        tid = first[0]["turn_id"]
        server.STATE["sessions"].turns.link_job(tid, "later-child-job")
        again = client.post("/api/chat", json=request)
        assert again.status_code == 200 and _events(again) == first
        assert len(adapter.seen) == 1
        assert server.STATE["sessions"].turns.get(tid)["job_id"] == "later-child-job"
    finally:
        server.STATE["bridge"].stop()


def test_bad_chat_keys_fail_before_creating_session_or_requesting_model(monkeypatch, fake_adapter):
    adapter = fake_adapter([[]])
    client = _client(monkeypatch, adapter)
    try:
        for payload in (["bad"], {"message": 7}, {"message": "inspect", "request_id": []},
                        {"message": "inspect", "request_id": ""}, {"message": "inspect", "session_id": {}}):
            assert client.post("/api/chat", json=payload).status_code == 400
        assert adapter.seen == []
        assert server.STATE["sessions"].list() == []
    finally:
        server.STATE["bridge"].stop()
