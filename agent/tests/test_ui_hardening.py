"""Regressions for the web UI's controls, found by driving a real server end to end (2026-11-25).

Each test names the defect it pins: what the UI or API used to do, and what it must do now."""
import io
import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from r2g_agent import config, housekeeping, server, tools
from r2g_agent.deletion import DeletionManager, uploaded_sources
from r2g_agent.jobs import FAILED, RUNNING, JobStore
from r2g_agent.paths import r2g_home
from r2g_agent.pipeline import OWNER, Pipeline


def _client(monkeypatch, fake=None):
    server.STATE.clear()
    server.init_runtime(config.Config())
    monkeypatch.setattr(server.tools.RT.pipeline, "submit",
                        lambda sources, **kw: server.tools.RT.store.create_job(
                            kw.get("title") or "t", {"platform": kw.get("platform"), "output": kw.get("output"),
                                                     "sources": sources, "params": kw.get("params") or {},
                                                     **({"retry_of": kw["retry_of"]} if kw.get("retry_of") else {})},
                            [{"name": s["name"], "detail": {"source": s["path"], "top": s.get("top"),
                                                            "requested_top": s.get("top")}} for s in sources]))
    if fake is not None:
        monkeypatch.setattr(server, "make_adapter", lambda mc: fake)
    return TestClient(server.app)


V = b"module top(input clk, output reg q); always @(posedge clk) q <= ~q; endmodule\n"


def _files(*items):
    return [("files", (name, io.BytesIO(data), "text/plain")) for name, data in items]


# --- uploads ----------------------------------------------------------------
def test_upload_refuses_binary_hdl_and_never_overwrites_a_sanitised_twin(monkeypatch):
    c = _client(monkeypatch)
    r = c.post("/api/upload", files=_files(("d/a b.v", V), ("d/a_b.v", b"module other; endmodule\n"),
                                           ("d/blob.v", b"\x00\x01binary"))).json()
    assert r["files"] == ["d/a_b.v"]
    reasons = {x["file"]: x["reason"] for x in r["rejected"]}
    assert "二进制" in reasons["d/blob.v"] and "重名" in reasons["d/a_b.v"]
    assert (Path(r["path"]) / "d" / "a_b.v").read_bytes() == V          # the first one, not overwritten


def test_upload_without_files_or_only_binaries_is_a_clean_400_and_leaves_nothing(monkeypatch):
    c = _client(monkeypatch)
    r = c.post("/api/upload")
    assert r.status_code == 400 and isinstance(r.json()["detail"], str)  # not a 422 validation list
    r = c.post("/api/upload", files=_files(("x.v", b"\x00\x00")))
    assert r.status_code == 400 and "二进制" in r.json()["detail"]
    assert not any((r2g_home() / "uploads").iterdir())


def test_upload_takes_more_than_starlettes_1000_files_per_request(monkeypatch):
    c = _client(monkeypatch)
    many = [("b/m%d/m%d.v" % (i, i), b"module m%d; endmodule\n" % i) for i in range(1100)]
    r = c.post("/api/upload", files=_files(*many))
    assert r.status_code == 200 and len(r.json()["files"]) == 1100


def test_a_folder_arrives_in_several_requests_into_one_upload(monkeypatch):
    c = _client(monkeypatch)
    first = c.post("/api/upload", files=_files(("f/a.v", V))).json()
    second = c.post("/api/upload?into=" + first["id"], files=_files(("f/rom.hex", b"00\n"))).json()
    assert second["path"] == first["path"] and sorted(p.name for p in (Path(first["path"]) / "f").iterdir()) == ["a.v", "rom.hex"]
    assert c.post("/api/upload?into=../../etc", files=_files(("a.v", V))).status_code == 400
    assert c.post("/api/upload?into=1111111111-abcdef", files=_files(("a.v", V))).status_code == 404


def test_upload_size_limits(monkeypatch):
    c = _client(monkeypatch)
    monkeypatch.setenv("R2G_UPLOAD_MAX_FILE_MB", "1")
    r = c.post("/api/upload", files=_files(("big.v", b"//" * (1 << 20)), ("ok.v", V))).json()
    assert r["files"] == ["ok.v"] and "超过" in r["rejected"][0]["reason"] and not (Path(r["path"]) / "big.v").exists()
    monkeypatch.setenv("R2G_UPLOAD_MAX_FILE_MB", "10")
    monkeypatch.setenv("R2G_UPLOAD_MAX_TOTAL_MB", "1")
    before = set((r2g_home() / "uploads").iterdir())
    r = c.post("/api/upload", files=_files(("a.v", b"/" * 700_000), ("b.v", b"/" * 700_000)))
    assert r.status_code == 413 and set((r2g_home() / "uploads").iterdir()) == before   # nothing half-written kept


def test_discarded_upload_is_deleted_but_one_a_job_uses_is_kept(monkeypatch):
    c = _client(monkeypatch)
    a = c.post("/api/upload", files=_files(("a/top.v", V))).json()
    b = c.post("/api/upload", files=_files(("b/top.v", V))).json()
    assert c.post("/api/jobs", json={"designs": [{"path": b["path"] + "/b"}]}).status_code == 200
    assert c.delete("/api/uploads/" + a["id"]).status_code == 200 and not Path(a["path"]).exists()
    assert c.delete("/api/uploads/" + b["id"]).status_code == 409 and Path(b["path"]).exists()


# --- choosing designs ---------------------------------------------------------
def test_inspect_reports_the_top_or_the_candidates_to_choose_from(monkeypatch, tmp_path):
    c = _client(monkeypatch)
    one = tmp_path / "one"; one.mkdir(); (one / "t.v").write_bytes(V)
    r = c.post("/api/inspect", json={"path": str(one)}).json()
    assert r["top"] == "top" and not r["problem"] and r["file_names"] == ["t.v"]
    two = tmp_path / "two"; two.mkdir()
    (two / "x.v").write_text("module a; endmodule\nmodule b; endmodule\n")
    r = c.post("/api/inspect", json={"path": str(two)}).json()
    assert r["problem"] and r["candidates"] == ["a", "b"]
    empty = tmp_path / "empty"; empty.mkdir(); (empty / "e.v").write_text("")
    assert "module" in c.post("/api/inspect", json={"path": str(empty)}).json()["problem"]
    assert c.post("/api/inspect", json={"path": "relative/dir"}).status_code == 400


def test_scan_needs_an_absolute_folder(monkeypatch):
    c = _client(monkeypatch)
    # an empty path used to scan the server's own working directory
    for bad in ("", "  ", "relative", "/no/such/dir"):
        assert c.post("/api/batch/scan", json={"path": bad}).status_code == 400
    assert c.post("/api/batch/scan", content=b"{bad json").status_code == 400


def test_job_requests_are_validated_before_anything_runs(monkeypatch, tmp_path):
    c = _client(monkeypatch)
    (tmp_path / "notes.txt").write_text("x")
    for body in ({"designs": "x"}, {"designs": ["x"]}, {"designs": [{"path": str(tmp_path)}]},
                 {"designs": [{"path": "rel/dir"}]}, {"designs": [{"example": "../etc"}]},
                 {"designs": [{"example": "counter8", "top": "a b"}]}, {"designs": [{"example": "counter8"}], "params": "x"}):
        r = c.post("/api/jobs", json=body)
        assert r.status_code == 400, body
    assert c.post("/api/jobs", content=b"not json").status_code == 400
    assert server.tools.RT.store.jobs() == []


def test_job_list_filters_counts_and_shows_deletions(monkeypatch):
    c = _client(monkeypatch)
    st = server.tools.RT.store
    a = st.create_job("uart batch", {}, [{"name": "a"}]); st.set_job(a, "done")
    b = st.create_job("alu run", {}, [{"name": "b"}]); st.set_job(b, RUNNING)
    st.request_deletion("job", a)
    r = c.get("/api/jobs?status=active")
    assert [j["id"] for j in r.json()] == [b] and r.headers["x-total-count"] == "1"
    r = c.get("/api/jobs?q=uart")
    assert [j["id"] for j in r.json()] == [a] and r.json()[0]["deletion"] == "pending"
    assert c.get("/api/jobs?q=100%").json() == []                        # LIKE wildcards are literal
    assert c.get("/api/jobs?limit=1&offset=1").json()[0]["id"] == a
    assert c.get("/api/jobs?status=bogus").status_code == 400
    assert c.get("/api/jobs/" + b).json()["deletion"] is None


def test_retry_reruns_failed_designs_as_a_new_job(monkeypatch, tmp_path):
    c = _client(monkeypatch)
    st = server.tools.RT.store
    src = tmp_path / "s"; src.mkdir(); (src / "t.v").write_bytes(V)
    jid = st.create_job("orig", {"platform": "nangate45", "output": "graph", "params": {"utilization": 0.4}},
                        [{"name": "ok"}, {"name": "bad"}, {"name": "gone"}])
    for name, status, source in (("ok", "passed", src), ("bad", FAILED, src), ("gone", "cancelled", tmp_path / "x")):
        st.set_design(jid, name, status=status, detail={"source": str(source), "top": "top", "requested_top": None})
    st.set_job(jid, "done")
    assert "no longer on disk" in c.post(f"/api/jobs/{jid}/retry").json()["detail"]
    r = c.post(f"/api/jobs/{jid}/retry", json={"designs": ["bad"]})
    assert r.status_code == 200 and r.json()["designs"] == ["bad"]
    new = st.job(r.json()["job_id"])
    assert new["spec"]["retry_of"] == jid and new["spec"]["params"] == {"utilization": 0.4}
    assert new["designs"][0]["detail"]["top"] is None                    # the user's choice, not the inferred top
    assert c.post(f"/api/jobs/{jid}/retry", json={"designs": ["nope"]}).status_code == 400
    assert c.post("/api/jobs/nope/retry").status_code == 404


# --- settings -----------------------------------------------------------------
def test_settings_are_validated_all_or_nothing(monkeypatch):
    c = _client(monkeypatch)
    for body in ({"workers": 0}, {"cores_per_design": -3}, {"workers": "abc"}, {"workers": 2.5},
                 {"min_free_gb": -1}, {"model": {"provider": "nope"}}, {"model": {"base_url": "ftp://x"}},
                 {"workers": 3, "model": {"temperature": 9}}, {"model": {"api_key_env": "A B"}}):
        r = c.post("/api/config", json=body)
        assert r.status_code == 400 and isinstance(r.json()["detail"], str), body
    assert c.post("/api/config", content=b"{bad").status_code == 400
    cfg = c.get("/api/config").json()
    assert (cfg["workers"], cfg["cores_per_design"]) == (2, 8)          # nothing half-applied
    r = c.post("/api/config", json={"workers": "3", "cores_per_design": 4, "min_free_gb": 0})
    assert r.status_code == 200 and server.tools.RT.pipeline.workers == 3 and server.tools.RT.pipeline.cores == 4
    assert config.load().workers == 3


def test_a_local_endpoint_needs_no_key(monkeypatch):
    c = _client(monkeypatch)
    r = c.post("/api/config", json={"model": {"provider": "custom", "model": "m", "base_url": "http://127.0.0.1:8000/v1"}}).json()
    assert r["model_resolved"]["needs_key"] is False
    r = c.post("/api/config", json={"model": {"provider": "deepseek", "base_url": ""}}).json()
    assert r["model_resolved"]["needs_key"] is True


def test_health_reports_the_cpus_this_server_may_use(monkeypatch):
    c = _client(monkeypatch)
    monkeypatch.setattr(server.os, "sched_getaffinity", lambda pid: {0, 1, 2})
    assert c.get("/api/health").json()["machine"]["threads"] == 3


# --- chat ---------------------------------------------------------------------
class SlowAdapter:
    """Streams one text delta every 50 ms for `n` deltas."""
    def __init__(self, n=200):
        self.n = n

    def stream(self, messages, tools, system=""):
        for i in range(self.n):
            time.sleep(0.05)
            yield {"type": "text", "delta": "w%d " % i}
        yield {"type": "done", "stop_reason": "end"}


def _wait(cond, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_a_dropped_connection_no_longer_wedges_the_conversation(monkeypatch):
    c = _client(monkeypatch, SlowAdapter(20))
    with c.stream("POST", "/api/chat", json={"message": "hi"}) as r:
        first = next(r.iter_lines())                     # read one event, then the "browser" goes away
    sid = json.loads(first[5:])["session_id"]
    turns = server.STATE["turns"]
    assert _wait(lambda: not turns.busy(sid))           # the turn finished on its own thread...
    msgs = server.STATE["sessions"].load(sid)
    assert msgs[-1]["role"] == "assistant" and "w19" in msgs[-1]["content"]   # ...and was saved
    body = c.post("/api/chat", json={"message": "again", "session_id": sid}).text
    assert "正在处理上一条消息" not in body and '"type": "done"' in body


def test_stop_ends_a_running_answer_and_keeps_what_was_said(monkeypatch):
    c = _client(monkeypatch, SlowAdapter(400))
    sid = server.STATE["sessions"].create()
    out = {}
    th = threading.Thread(target=lambda: out.setdefault("body", c.post("/api/chat", json={"message": "long", "session_id": sid}).text))
    th.start()
    turns = server.STATE["turns"]
    assert _wait(lambda: turns.busy(sid))
    assert c.get(f"/api/sessions/{sid}").json()["busy"] is True
    r = c.post("/api/chat", json={"message": "second", "session_id": sid})
    assert r.status_code == 409                          # one answer at a time, said plainly
    time.sleep(0.3)
    assert c.post(f"/api/sessions/{sid}/stop").json() == {"stopping": True}
    th.join(10)
    assert '"type": "stopped"' in out["body"] and '"type": "done"' in out["body"]
    last = server.STATE["sessions"].load(sid)[-1]
    assert last["role"] == "assistant" and last["content"].endswith("（已停止）") and "w0" in last["content"]
    assert not turns.busy(sid) and c.post(f"/api/sessions/{sid}/stop").json() == {"stopping": False}


def test_stopping_between_tool_calls_answers_every_call(monkeypatch, fake_adapter):
    stop = threading.Event()
    calls = [{"type": "tool_call", "id": "a", "name": "list_examples", "arguments": {}},
             {"type": "tool_call", "id": "b", "name": "list_examples", "arguments": {}}]
    fake = fake_adapter([calls])
    monkeypatch.setattr(tools, "call", lambda name, args: (stop.set(), {"ok": 1})[1])
    from r2g_agent import agent_loop
    st = agent_loop.SessionStore()
    sid = st.create()
    events = list(agent_loop.run_turn(fake, st, sid, "go", should_stop=stop.is_set))
    assert [e["type"] for e in events][-2:] == ["stopped", "done"]
    msgs = st.load(sid)
    answered = {m["tool_call_id"] for m in msgs if m["role"] == "tool"}
    assert answered == {"a", "b"}                        # the next request to the model stays well-formed


def test_empty_chat_message_is_refused_without_creating_a_conversation(monkeypatch, fake_adapter):
    c = _client(monkeypatch, fake_adapter([]))
    assert c.post("/api/chat", json={"message": "  "}).status_code == 400
    assert c.post("/api/chat", json={"message": "x", "session_id": "../x"}).status_code == 400
    assert c.get("/api/sessions").json() == []


# --- deletion -------------------------------------------------------------------
def test_deleting_a_job_removes_its_own_upload_but_not_a_shared_one_or_a_server_folder(monkeypatch, tmp_path):
    c = _client(monkeypatch)
    st = server.tools.RT.store
    own = c.post("/api/upload", files=_files(("o/top.v", V))).json()
    shared = c.post("/api/upload", files=_files(("s/top.v", V))).json()
    server_dir = tmp_path / "corpus"; server_dir.mkdir(); (server_dir / "t.v").write_bytes(V)
    j1 = c.post("/api/jobs", json={"designs": [{"path": own["path"] + "/o"}, {"path": shared["path"] + "/s"},
                                               {"path": str(server_dir)}]}).json()["job_id"]
    j2 = c.post("/api/jobs", json={"designs": [{"path": shared["path"] + "/s"}]}).json()["job_id"]
    others = [st.job(j2)["spec"]]
    assert uploaded_sources(st.job(j1), others) == [Path(own["path"]).resolve()]
    for jid in (j1,):
        for d in st.job(jid)["designs"]:
            st.set_design(jid, d["name"], status="failed")
        st.set_job(jid, "failed")
    dm = server.STATE["deletions"]
    dm.request("job", j1)
    dm.process_once()
    assert st.deletion("job", j1)["status"] == "done"
    assert not Path(own["path"]).exists() and Path(shared["path"]).exists() and (server_dir / "t.v").exists()


def test_flow_dir_comes_from_r2g_env_file_too(monkeypatch, tmp_path):
    (tmp_path / "orfs" / "flow").mkdir(parents=True)
    env = tmp_path / "tools.env"
    env.write_text('export ORFS_ROOT="%s"\n' % (tmp_path / "orfs"))
    monkeypatch.delenv("ORFS_ROOT", raising=False)
    monkeypatch.setenv("R2G_ENV_FILE", str(env))
    assert housekeeping.orfs_flow_dir() == tmp_path / "orfs" / "flow"


# --- restarts -------------------------------------------------------------------
def test_a_clean_shutdown_is_not_counted_as_a_crash_and_the_users_top_is_kept(monkeypatch):
    st = JobStore()
    jid = st.create_job("u", {"platform": "nangate45", "output": "graph"}, [{"name": "b"}])
    st.set_job(jid, RUNNING)
    st.set_design(jid, "b", status=RUNNING, stage="flow", project="/tmp/none",
                  detail={"owner": "999999999:1", "source": "/src/b", "top": "inferred_top",
                          "requested_top": None, "restarts": 2})
    st.mark_owner_stopped("999999999:1")
    p = Pipeline(st, workers=1, ingest=False)
    calls = []
    monkeypatch.setattr(p.pool, "submit", lambda fn, *a: calls.append(a))
    monkeypatch.setattr(p, "_watch_job", lambda jid: None)
    p.recover(resume=True)
    d = st.design(jid, "b")
    assert d["status"] == "pending" and d["detail"]["restarts"] == 2 and not d["detail"]["graceful_stop"]
    assert calls[0][3] is None                     # top re-inferred like the first run, not "user-specified"


def test_shutdown_marks_this_servers_running_designs(monkeypatch):
    st = JobStore()
    jid = st.create_job("u", {}, [{"name": "a"}])
    st.set_design(jid, "a", status=RUNNING, detail={"owner": OWNER})
    p = Pipeline(st, workers=1, ingest=False)
    p.shutdown()
    assert st.design(jid, "a")["detail"]["graceful_stop"] == 1 and p._shutting_down


def test_a_signoff_only_job_is_closed_without_a_failed_package(monkeypatch):
    st = JobStore()
    jid = st.create_job("s", {"platform": "nangate45", "output": "signoff"}, [{"name": "a"}])
    st.set_job(jid, RUNNING)
    st.set_design(jid, "a", status="passed", stage="done", project="/tmp/no-such-project")
    p = Pipeline(st, workers=1, ingest=False)
    assert p.finalize_if_done(jid)
    j = st.job(jid)
    assert j["status"] == "done" and "package_error" not in j["result"] and j["result"]["passed"] == 1


def test_a_syntax_error_names_file_and_line_even_after_the_slang_fallback(tmp_path):
    logs = tmp_path / "agent_logs"; logs.mkdir()
    (logs / "frontend.log").write_text(
        "1. Executing SLANG frontend.\n../ws/p/rtl/bad.v:2:32: error: expected ';'\n  always q <= ~q\nERROR: Compilation failed\n")
    assert Pipeline._frontend_error(tmp_path) == "bad.v:2:32：expected ';'"
    assert Pipeline._frontend_error(tmp_path / "none") == ""


def test_deleting_a_conversation_removes_folders_attached_in_it_but_unused(monkeypatch):
    c = _client(monkeypatch)
    a = c.post("/api/upload", files=_files(("a/top.v", V))).json()
    used = c.post("/api/upload", files=_files(("u/top.v", V))).json()
    c.post("/api/jobs", json={"designs": [{"path": used["path"] + "/u"}]})      # another job reads this one
    sessions = server.STATE["sessions"]
    sid = sessions.create()
    sessions.save(sid, [{"role": "user", "content": "[附件] 已上传 RTL 文件夹 a（1 个文件），路径：%s/a\nrun it" % a["path"]},
                        {"role": "user", "content": "[附件] 已上传 RTL 文件夹 u（1 个文件），路径：%s/u\n" % used["path"]}], "t")
    dm = server.STATE["deletions"]
    dm.request("session", sid)
    dm.process_once()
    assert not sessions.exists(sid) and not Path(a["path"]).exists() and Path(used["path"]).exists()


def test_restart_stops_a_dead_servers_stage_by_its_execution_marker(monkeypatch):
    """GNU timeout runs ORFS make in its own process group: stopping only the recorded group
    left it holding the workspace lock and the restarted design failed at once."""
    from r2g_agent import pipeline as pl
    st = JobStore()
    jid = st.create_job("u", {"platform": "nangate45", "output": "graph"}, [{"name": "b"}])
    st.set_job(jid, RUNNING)
    st.set_design(jid, "b", status=RUNNING, stage="flow", project="/tmp/proj",
                  detail={"owner": "999999999:1", "source": "/src/b", "top": None, "pgid": 4242,
                          "process_start": "77", "execution_id": "abc"})
    seen = []
    monkeypatch.setattr(pl, "stop_execution", lambda *a, **k: seen.append((a, k)) or True)
    monkeypatch.setattr(pl, "stop_orphan", lambda *a, **k: (_ for _ in ()).throw(AssertionError("legacy only as fallback")))
    p = Pipeline(st, workers=1, ingest=False)
    monkeypatch.setattr(p.pool, "submit", lambda fn, *a: None)
    monkeypatch.setattr(p, "_watch_job", lambda jid: None)
    p.recover(resume=True)
    assert seen and seen[0][0][:2] == (4242, "/tmp/proj") and seen[0][1] == {"expected_start": "77", "execution_id": "abc"}


def test_a_transient_process_inspection_failure_keeps_the_deletion_pending(monkeypatch):
    from r2g_agent import deletion as dl
    from r2g_agent.processes import ProcessInspectionError
    st = JobStore()
    jid = st.create_job("done", {}, [{"name": "a"}])
    st.set_design(jid, "a", status="passed", detail={"execution_id": "x", "process_start": "1"})
    st.set_job(jid, "done")

    class P:
        def cancel(self, j):
            return None
    dm = DeletionManager(st, None, P())
    st.request_deletion("job", jid)
    calls = []

    def flaky(marker, start):
        calls.append(1)
        if len(calls) == 1:
            raise ProcessInspectionError("cannot enumerate execution children: pid=1")
        return []
    monkeypatch.setattr(dl, "execution_members", flaky)
    dm.process_once()
    assert st.deletion("job", jid)["status"] == "pending"      # not "error": retried on the next pass
    dm.process_once()
    assert st.deletion("job", jid)["status"] == "done" and st.job(jid) is None


def test_deleting_a_job_removes_its_emptied_orfs_design_folder_only(monkeypatch, tmp_path):
    from test_deletion import finished, setup
    s, sessions, pipe, manager = setup(monkeypatch)
    jid, project = finished(s, r2g_home())
    flow = tmp_path / "flow"
    monkeypatch.setattr(housekeeping, "orfs_flow_dir", lambda: flow)
    mine = flow / "designs/nangate45/d" / project.name
    mine.mkdir(parents=True)
    shared = flow / "results/nangate45/d"
    (shared / project.name).mkdir(parents=True)
    (shared / "someone_else__x").mkdir()
    manager.request("job", jid)
    manager.process_once()
    assert s.deletion("job", jid)["status"] == "done"
    assert not (flow / "designs/nangate45/d").exists() and (flow / "designs/nangate45").is_dir()
    assert (shared / "someone_else__x").is_dir() and not (shared / project.name).exists()
    pipe.pool.shutdown()
