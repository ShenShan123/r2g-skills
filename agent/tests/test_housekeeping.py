import json
import subprocess
from pathlib import Path
import sys
import threading
import time

import pytest

from r2g_agent import housekeeping
from r2g_agent.jobs import FAILED, RUNNING, JobStore
from r2g_agent.pipeline import OWNER, Pipeline, owner_alive


class Stop(Exception):
    pass


def test_slot_waits_then_proceeds():
    got, waited = [], []
    with housekeeping.design_slot(1) as i:
        assert i == 0 and housekeeping.slots_in_use(1) == 1

        def other():
            with housekeeping.design_slot(1, on_wait=lambda: waited.append(1), poll=0.05):
                got.append(time.time())
        t = threading.Thread(target=other)
        t.start()
        time.sleep(0.3)
        assert waited == [1] and got == []        # blocked while the slot is held
        released = time.time()
    t.join(5)
    assert got and got[0] >= released
    assert housekeeping.slots_in_use(1) == 0


def test_slot_is_machine_wide_and_dies_with_its_process(r2g_home):
    code = ("import time; from r2g_agent import housekeeping\n"
            "with housekeeping.design_slot(1):\n print('held', flush=True); time.sleep(30)\n")
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True,
                         env={"R2G_HOME": str(r2g_home), "PATH": "/usr/bin:/bin",
                              "PYTHONPATH": ":".join(sys.path)})
    try:
        assert p.stdout.readline().strip() == "held"
        assert housekeeping.slots_in_use(1) == 1

        def stop():
            raise Stop()
        with pytest.raises(Stop):
            with housekeeping.design_slot(1, should_stop=stop):
                pass
    finally:
        p.kill()
        p.wait()
    with housekeeping.design_slot(1) as i:        # a killed holder frees its slot
        assert i == 0


def _run_meta(project, flow, variant, design="top", platform="nangate45"):
    run = project / "backend" / "RUN_1"
    run.mkdir(parents=True)
    (run / "run-meta.json").write_text(json.dumps({
        "design_name": design, "platform": platform, "flow_variant": variant,
        "orfs_results": str(flow / "results" / platform / design / variant)}))


def test_only_this_projects_variant_dirs_are_dropped(tmp_path):
    flow = tmp_path / "orfs" / "flow"
    proj = tmp_path / "ws" / "top__job1"
    for kind in ("results", "logs", "reports", "objects"):
        (flow / kind / "nangate45" / "top" / "top__job1").mkdir(parents=True)
        (flow / kind / "nangate45" / "top" / "top__job1" / "f").write_bytes(b"x" * 10)
        (flow / kind / "nangate45" / "top" / "top__job2").mkdir(parents=True)
    (flow / "results" / "nangate45" / "top" / "shared.odb").write_text("no-variant fallback")
    _run_meta(proj, flow, "top__job1")
    assert housekeeping.drop_orfs_copies(proj) == 40
    assert not (flow / "results" / "nangate45" / "top" / "top__job1").exists()
    assert (flow / "results" / "nangate45" / "top" / "top__job2").is_dir()
    assert (flow / "results" / "nangate45" / "top" / "shared.odb").is_file()


def test_a_variant_not_named_after_the_project_is_never_touched(tmp_path):
    flow = tmp_path / "orfs" / "flow"
    (flow / "results" / "nangate45" / "top" / "other").mkdir(parents=True)
    proj = tmp_path / "ws" / "top__job1"
    _run_meta(proj, flow, "other")
    assert housekeeping.orfs_variant_dirs(proj) == []


def _resumable(st, status, owner, restarts=0, name="b"):
    jid = st.create_job("u", {"platform": "nangate45", "output": "graph"}, [{"name": name}])
    st.set_job(jid, RUNNING)
    st.set_design(jid, name, status=status, stage="flow", project="/tmp/none",
                  detail={"owner": owner, "source": "/src/" + name, "top": "t", "restarts": restarts})
    return jid


def test_server_resumes_a_dead_owners_designs_and_leaves_a_live_one(monkeypatch):
    st = JobStore()
    live = _resumable(st, RUNNING, OWNER, name="a")
    dead_run = _resumable(st, RUNNING, "999999999:1", name="b")
    dead_queue = _resumable(st, "pending", "999999999:1", name="c")
    p = Pipeline(st, workers=1, ingest=False)
    calls = []
    monkeypatch.setattr(p.pool, "submit", lambda fn, *a: calls.append(a))
    monkeypatch.setattr(p, "_watch_job", lambda jid: None)
    assert owner_alive(OWNER) and not owner_alive("999999999:1") and not owner_alive(None)
    p.recover(resume=True)
    assert st.design(live, "a")["status"] == RUNNING and st.design(live, "a")["detail"]["owner"] == OWNER
    assert sorted(a[1] for a in calls) == ["b", "c"]       # both re-queued, with source/top/platform
    assert calls[0][2] == Path("/src/" + calls[0][1]) and calls[0][3] == "t"
    b = st.design(dead_run, "b")
    assert b["status"] == "pending" and b["detail"]["restarts"] == 1 and b["detail"]["owner"] == OWNER
    assert st.design(dead_queue, "c")["detail"]["restarts"] == 0   # queued, never started: not a restart
    calls.clear()
    p.recover(resume=True)                                 # now owned by us (alive): nothing again
    assert calls == []


def test_a_design_interrupted_too_often_fails_instead_of_looping(monkeypatch):
    st = JobStore()
    jid = _resumable(st, RUNNING, "999999999:1", restarts=2)
    p = Pipeline(st, workers=1, ingest=False)
    monkeypatch.setattr(p.pool, "submit", lambda fn, *a: (_ for _ in ()).throw(AssertionError("resubmitted")))
    p.recover(resume=True)
    d = st.design(jid, "b")
    assert d["status"] == FAILED and "中断" in d["detail"]["reason"]


def test_two_servers_never_both_adopt_a_design():
    st = JobStore()
    jid = _resumable(st, RUNNING, "999999999:1")
    assert st.claim_design(jid, "b", "999999999:1", "1:a") is True
    assert st.claim_design(jid, "b", "999999999:1", "2:b") is False


def test_wait_for_disk_pauses_then_proceeds(monkeypatch):
    free = iter([10.0, 10.0, 80.0])
    monkeypatch.setattr(housekeeping, "free_gb", lambda: next(free))
    told = []
    housekeeping.wait_for_disk(50, on_wait=told.append, poll=0)
    assert told == [10.0]
    housekeeping.wait_for_disk(0)                          # 0 disables the guard


def test_slim_keeps_dataset_reports_logs(tmp_path):
    proj = tmp_path / "p"
    for k in ("results", "objects", "final", "logs", "reports_orfs"):
        (proj / "backend" / "RUN_1" / k).mkdir(parents=True)
        (proj / "backend" / "RUN_1" / k / "f").write_bytes(b"x" * 100)
    for k in ("dataset", "reports", "rtl"):
        (proj / k).mkdir()
        (proj / k / "f").write_text("keep")
    assert housekeeping.slim_passed(proj) == 300
    left = sorted(p.name for p in (proj / "backend" / "RUN_1").iterdir())
    assert left == ["logs", "reports_orfs"] and (proj / "dataset" / "f").is_file()
    assert (proj / "backend" / "SLIMMED").is_file()


def test_clean_candidates_only_old_finished_jobs(r2g_home):
    import os
    st = JobStore()
    old_done = st.create_job("a", {}, [{"name": "x"}])
    st.set_job(old_done, "done")
    running = st.create_job("b", {}, [{"name": "y"}])
    st.set_job(running, RUNNING)
    new_done = st.create_job("c", {}, [{"name": "z"}])
    st.set_job(new_done, FAILED)
    for j in (old_done, running, new_done):
        (r2g_home / "workspace" / j).mkdir(parents=True)
    past = time.time() - 30 * 86400
    for j in (old_done, running):
        os.utime(r2g_home / "workspace" / j, (past, past))
    assert [r["job_id"] for r in housekeeping.clean_candidates(st, 7)] == [old_done]


def test_stop_orphan_only_kills_a_group_that_names_the_project(tmp_path):
    from r2g_agent.pipeline import stop_orphan
    proj = str(tmp_path / "proj_x")
    # like run_orfs.sh <project> <platform>: the project path is an argument
    sleeper = "import time; time.sleep(60)"
    mine = subprocess.Popen([sys.executable, "-c", sleeper, proj, "nangate45"], start_new_session=True)
    other = subprocess.Popen([sys.executable, "-c", sleeper, "/elsewhere", "nangate45"], start_new_session=True)
    try:
        time.sleep(0.3)
        assert stop_orphan(other.pid, proj) is False and other.poll() is None
        assert stop_orphan(mine.pid, proj, grace=5) is True
        mine.wait(5)
        assert mine.returncode is not None
    finally:
        for p in (mine, other):
            if p.poll() is None:
                p.kill()


def test_lowering_the_disk_limit_releases_a_waiter(monkeypatch):
    monkeypatch.setattr(housekeeping, "free_gb", lambda: 10.0)
    limit = {"gb": 50}
    seen = []

    def stop():
        seen.append(1)
        if len(seen) == 2:
            limit["gb"] = 5            # the user lowers it in Settings while it waits
    housekeeping.wait_for_disk(lambda: limit["gb"], should_stop=stop, poll=0)
    assert len(seen) == 2
