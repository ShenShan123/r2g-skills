import json
import subprocess
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


def test_recover_leaves_a_live_owners_job_alone():
    st = JobStore()
    live = st.create_job("t", {}, [{"name": "a"}])
    st.set_job(live, RUNNING)
    st.set_design(live, "a", status=RUNNING, stage="flow", detail={"owner": OWNER})
    dead = st.create_job("u", {}, [{"name": "b"}])
    st.set_job(dead, RUNNING)
    st.set_design(dead, "b", status=RUNNING, stage="flow", detail={"owner": "999999999:1"})
    assert owner_alive(OWNER) and not owner_alive("999999999:1") and not owner_alive(None)
    Pipeline(st, workers=1, ingest=False).recover()
    assert st.job(live)["status"] == RUNNING and st.design(live, "a")["status"] == RUNNING
    assert st.design(dead, "b")["status"] == FAILED


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
