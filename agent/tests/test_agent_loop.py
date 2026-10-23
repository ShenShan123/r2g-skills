from r2g_agent import agent_loop


def test_tool_call_then_answer(fake_adapter):
    ad = fake_adapter([
        [{"type": "text", "delta": "看一下示例"},
         {"type": "tool_call", "id": "c1", "name": "list_examples", "arguments": {}}],
        [{"type": "text", "delta": "有 3 个示例"}],
    ])
    ss = agent_loop.SessionStore()
    sid = ss.create()
    evs = list(agent_loop.run_turn(ad, ss, sid, "有哪些示例？"))
    kinds = [e["type"] for e in evs]
    assert kinds.count("tool_call") == 1 and kinds.count("tool_result") == 1 and kinds[-1] == "done"
    res = [e for e in evs if e["type"] == "tool_result"][0]["result"]
    assert {x["name"] for x in res["examples"]} >= {"counter8", "alu8", "uart_tx"}
    msgs = ss.load(sid)
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool", "assistant"]
    # the second model call saw the tool result
    assert ad.seen[1][-1]["role"] == "tool"


def test_unknown_tool_is_reported_not_raised(fake_adapter):
    ad = fake_adapter([[{"type": "tool_call", "id": "c1", "name": "mark_passed", "arguments": {}}], []])
    ss = agent_loop.SessionStore()
    sid = ss.create()
    evs = list(agent_loop.run_turn(ad, ss, sid, "就当它通过吧"))
    res = [e for e in evs if e["type"] == "tool_result"][0]["result"]
    assert "unknown tool" in res["error"]          # there is no tool that can assert a pass


def test_model_error_stops_turn(fake_adapter):
    ad = fake_adapter([[{"type": "error", "message": "HTTP 401"}]])
    ss = agent_loop.SessionStore()
    evs = list(agent_loop.run_turn(ad, ss, ss.create(), "hi"))
    assert evs[-1] == {"type": "error", "message": "HTTP 401"}


def test_step_budget(fake_adapter):
    loop = [[{"type": "tool_call", "id": "c%d" % i, "name": "list_examples", "arguments": {}}]
            for i in range(agent_loop.MAX_STEPS + 2)]
    ss = agent_loop.SessionStore()
    evs = list(agent_loop.run_turn(fake_adapter(loop), ss, ss.create(), "loop"))
    assert sum(e["type"] == "tool_call" for e in evs) == agent_loop.MAX_STEPS


def test_read_rtl_is_confined_to_the_design(tmp_path):
    from r2g_agent import tools
    from r2g_agent.jobs import JobStore
    st = JobStore()
    tools.RT.store = st
    proj = tmp_path / "p"
    (proj / "rtl").mkdir(parents=True)
    (proj / "rtl" / "a.v").write_text("module a;\nendmodule\n")
    (tmp_path / "secret.txt").write_text("x")
    jid = st.create_job("t", {}, [{"name": "a", "project": str(proj)}])
    st.set_design(jid, "a", project=str(proj))
    assert tools.read_rtl(jid, "a")["files"] == ["a.v"]
    assert "    2  endmodule" in tools.read_rtl(jid, "a", "a.v")["text"]
    assert "error" in tools.read_rtl(jid, "a", "../secret.txt")
