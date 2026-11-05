"""Replay mode: another model orchestrates a finished R2G job without re-running the pipeline."""
import json

import pytest


@pytest.fixture
def replay(tmp_path, monkeypatch):
    from r2g_agent import tools
    from r2g_agent.jobs import JobStore
    st = JobStore()
    tools.RT.store = st
    jid = st.create_job("recorded", {"platform": "nangate45", "output": "signoff"}, [{"name": "nangate45_C1"}])
    st.set_design(jid, "nangate45_C1", stage="done", status="passed")
    st.set_job(jid, "done")
    m = tmp_path / "map.json"
    m.write_text(json.dumps({"nangate45_C1": {"job_id": jid, "platform": "nangate45", "output": "signoff"}}))
    monkeypatch.setenv("R2G_REPLAY_MAP", str(m))
    monkeypatch.setenv("R2G_REPLAY_LOG", str(tmp_path / "replay.jsonl"))
    monkeypatch.setattr(tools, "_REPLAY", None)
    tools.REPLAY_PENDING.clear()

    class NoPipeline:
        def submit(self, *a, **k):
            raise AssertionError("replay must never submit a pipeline")
    tools.RT.pipeline = NoPipeline()
    src = tmp_path / "nangate45_C1"
    src.mkdir()
    (src / "top.v").write_text("module top; endmodule\n")
    yield tools, jid, src, tmp_path
    tools.REPLAY_PENDING.clear()


def _log(tmp):
    return [json.loads(l) for l in (tmp / "replay.jsonl").read_text().splitlines()]


def test_start_job_attaches_the_recorded_job_and_status_starts_as_running(replay):
    tools, jid, src, tmp = replay
    r = tools.start_job([{"path": str(src), "name": "nangate45_C1"}], platform="nangate45", output="signoff")
    assert r["job_id"] == jid and "error" not in r
    s = tools.job_status(jid)                       # submission phase: as it looked when submitted
    assert s["status"] == "running" and s["designs"][0]["signoff"] is None
    assert _log(tmp)[0]["event"] == "attach" and _log(tmp)[0]["divergence"] == {}


def test_the_runner_poll_ends_the_submission_phase(replay):
    from r2g_agent import server
    tools, jid, src, tmp = replay
    tools.start_job([{"path": str(src), "name": "nangate45_C1"}], platform="nangate45", output="signoff")
    server.job(jid)                                  # the measurement runner's HTTP poll
    assert tools.job_status(jid)["designs"][0]["status"] == "passed"


def test_divergent_arguments_and_early_peeks_are_logged(replay):
    tools, jid, src, tmp = replay
    tools.start_job([{"path": str(src), "name": "nangate45_C1"}], platform="sky130hd", output="graph")
    tools.call("design_detail", {"job_id": jid, "design": "nangate45_C1"})
    log = _log(tmp)
    assert log[0]["divergence"] == {"platform": ["sky130hd", "nangate45"], "output": ["graph", "signoff"]}
    assert any(e["event"] == "early_peek" and e["tool"] == "design_detail" for e in log)


def test_a_design_without_a_recorded_job_is_refused(replay):
    tools, jid, src, tmp = replay
    other = tmp / "nangate45_C2"; other.mkdir()
    r = tools.start_job([{"path": str(other), "name": "nangate45_C2"}], platform="nangate45", output="signoff")
    assert "error" in r and _log(tmp)[-1]["event"] == "no_recorded_job"
