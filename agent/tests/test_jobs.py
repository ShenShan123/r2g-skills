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
