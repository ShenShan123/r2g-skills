from r2g_agent.jobs import JobStore, PASSED


def test_job_lifecycle():
    st = JobStore()
    jid = st.create_job("t", {"platform": "nangate45"}, [{"name": "a"}, {"name": "b"}])
    st.set_design(jid, "a", stage="synth", status="running", detail={"x": 1})
    st.set_design(jid, "a", status=PASSED, detail={"y": 2})
    d = st.design(jid, "a")
    assert d["status"] == PASSED and d["detail"] == {"x": 1, "y": 2}   # detail merges
    st.event(jid, "a", "synth", "ok", "done")
    evs = st.events(jid)
    assert [e["kind"] for e in evs] == ["created", "ok"]
    assert st.events(jid, after=evs[0]["id"])[0]["message"] == "done"
    assert st.jobs()[0]["counts"] == {"passed": 1, "pending": 1}


def test_recover_closes_finished_and_interrupts_orphans():
    from r2g_agent.jobs import FAILED, PASSED, RUNNING
    from r2g_agent.pipeline import Pipeline
    st = JobStore()
    done = st.create_job("t", {}, [{"name": "a"}])
    st.set_job(done, RUNNING)
    st.set_design(done, "a", status=FAILED)               # finished, but its watcher died
    orphan = st.create_job("u", {}, [{"name": "b"}])
    st.set_job(orphan, RUNNING)
    st.set_design(orphan, "b", status=RUNNING, stage="flow")   # its thread died with the server
    Pipeline(st, workers=1).recover()
    assert st.job(done)["status"] == "failed"
    o = st.job(orphan)
    assert o["status"] == "failed" and o["designs"][0]["detail"]["reason"].startswith("服务重启时中断")


def test_a_job_is_closed_once_even_by_two_runtimes():
    from r2g_agent.jobs import RUNNING
    from r2g_agent.pipeline import Pipeline
    st = JobStore()
    jid = st.create_job("t", {}, [{"name": "a"}])
    st.set_job(jid, RUNNING)
    st.set_design(jid, "a", status="failed")
    a, b = Pipeline(st, workers=1, ingest=False), Pipeline(st, workers=1, ingest=False)
    assert a.finalize_if_done(jid) and b.finalize_if_done(jid)
    assert [e["kind"] for e in st.events(jid)].count("done") == 1
    assert st.job(jid)["status"] == "failed"
