"""Bare-agent harness: starting state, sandbox and budgets, without a model or EDA."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bare_agent"))
import bare_tools as bt  # noqa: E402
import run_bare as rb  # noqa: E402


def _case(tmp_path):
    src = tmp_path / "inputs" / "c1"
    (src / "src" / "rtl").mkdir(parents=True)
    (src / "src" / "rtl" / "top.v").write_text("module top(input a, output y); assign y = a; endmodule\n")
    (src / "src" / "rtl" / "defs.vh").write_text("`define W 4\n")
    base = tmp_path / "baseline"
    base.mkdir()
    (base / "config.mk").write_text(
        "export DESIGN_NAME = top\nexport PLATFORM = nangate45\n"
        "export VERILOG_FILES = /proj/x/sources/R1__a__b/src/rtl/top.v /proj/x/sources/R1__a__b/src/rtl/defs.vh "
        "/proj/x/sources/R1__a__b/gone.v\n"
        "export SDC_FILE = /work/constraint.sdc\n"
        "export VERILOG_INCLUDE_DIRS = /proj/x/sources/R1__a__b /proj/x/sources/R1__a__b/src/rtl\n"
        "export DIE_AREA  = 0 0 50 50\n")
    (base / "constraint.sdc").write_text("create_clock -period 3.0 [get_ports a]\n")
    case = {"id": "nangate45_C1", "platform": "nangate45", "top": "top", "input_root": str(src),
            "source_files": ["src/rtl/top.v"], "include_dirs": ["src/rtl"],
            "source_sha256": {"src/rtl/top.v": rb.sha(src / "src/rtl/top.v")}}
    return case, base


def test_prepare_remaps_the_baseline_onto_the_project(tmp_path):
    case, base = _case(tmp_path)
    proj = tmp_path / "out" / "nangate45_C1_bare"
    r = rb.prepare(case, base, proj)
    cfg = (proj / "constraints/config.mk").read_text()
    assert str(proj / "rtl/src/rtl/top.v") in cfg and str(proj / "rtl/src/rtl/defs.vh") in cfg
    assert "export SDC_FILE = %s" % (proj / "constraints/constraint.sdc") in cfg
    assert str(proj / "rtl/src/rtl") in cfg and "DIE_AREA  = 0 0 50 50" in cfg
    # the baseline source root itself maps onto rtl/ (smoke test: it was left unmapped)
    assert "export VERILOG_INCLUDE_DIRS = %s %s" % (proj / "rtl", proj / "rtl/src/rtl") in cfg
    # a baseline path absent from the original inputs is kept and reported, not invented
    assert r["unmapped_baseline_paths"] == ["/proj/x/sources/R1__a__b/gone.v"]
    # the same neutral design description the R2G arm gets
    dj = json.loads((proj / "rtl/design.json").read_text())
    assert dj == {"top": "top", "files": ["src/rtl/top.v"], "include_dirs": ["src/rtl"], "defines": []}
    assert rb.remap("/work/_src/d0/defs.vh", proj / "rtl") == ([str(proj / "rtl/src/rtl/defs.vh")], [])
    with pytest.raises(SystemExit):
        rb.prepare(case, base, proj)                     # never overwrites an attempt


def _tools(tmp_path):
    case, base = _case(tmp_path)
    proj = tmp_path / "out" / "nangate45_C1_bare"
    rb.prepare(case, base, proj)
    orfs = tmp_path / "orfs"
    (orfs / "flow/platforms/nangate45").mkdir(parents=True)
    (orfs / "flow/platforms/nangate45/config.mk").write_text("x\n")
    (orfs / "flow/scripts").mkdir(parents=True)
    return bt.BareTools(proj, "nangate45", tmp_path / "repo", orfs, {}), proj, orfs


def test_sandbox_confines_reads_and_writes(tmp_path):
    t, proj, orfs = _tools(tmp_path)
    assert "config.mk" in t.read_file(str(orfs / "flow/platforms/nangate45/config.mk"))["path"]
    with pytest.raises(bt.ToolError):
        t.write_file(str(orfs / "flow/platforms/nangate45/config.mk"), "y")     # PDK is read-only
    with pytest.raises(bt.ToolError):
        t.read_file("/home/yangao/r2g-skills/r2g-skills/signoff-loop/SKILL.md")  # no R2G knowledge
    with pytest.raises(bt.ToolError):
        t.write_file("../escape.txt", "y")
    t.write_file("hooks/fix.tcl", "puts hi\n")
    t.write_file("rtl/src/rtl/top.v", "module top(input a, output y); assign y = a; endmodule\n")
    assert [(w["path"], w["rtl"]) for w in t.writes] == [("hooks/fix.tcl", False), ("rtl/src/rtl/top.v", True)]


class FakeAdapter:
    def __init__(self, script):
        self.script, self.n = script, 0

    def stream(self, messages, specs, system):
        step = self.script[min(self.n, len(self.script) - 1)]
        self.n += 1
        for i, (name, args) in enumerate(step):
            yield {"type": "tool_call", "id": "c%d_%d" % (self.n, i), "name": name, "arguments": args}
        yield {"type": "usage", "usage_reported": True, "stream_complete": True, "total_tokens": 100,
               "transport_retries": []}
        yield {"type": "done", "stop_reason": "tool_calls"}


def test_loop_finishes_and_enforces_budgets(tmp_path):
    t, proj, orfs = _tools(tmp_path)
    calls, transcript = [], []
    stop = rb.run_loop(FakeAdapter([[("list_dir", {"path": "rtl"})], [("finish", {"summary": "done"})]]),
                       t, "task", dict(rb.DEFAULT_CAPS), transcript, calls)
    assert stop == "finished" and len(calls) == 2 and t.finished["summary"] == "done"

    t2, _, _ = _tools(tmp_path / "b")
    stop = rb.run_loop(FakeAdapter([[("list_dir", {"path": "."})]]), t2, "task",
                       {**rb.DEFAULT_CAPS, "model_calls": 3}, [], calls := [])
    assert stop == "cap_model_calls" and len(calls) == 3

    t3, _, _ = _tools(tmp_path / "c")
    t3.counts["run_flow"] = rb.DEFAULT_CAPS["run_flow"]
    transcript = []
    rb.run_loop(FakeAdapter([[("run_flow", {"from_stage": "route"})], [("finish", {"summary": "x"})]]),
                t3, "task", dict(rb.DEFAULT_CAPS), transcript, [])
    assert transcript[1]["result"] == {"error": "budget exhausted for run_flow"}


class TruncatingAdapter:
    """First reply is all reasoning, cut at the length limit; then the model finishes."""
    def __init__(self):
        self.n = 0

    def stream(self, messages, specs, system):
        self.n += 1
        if self.n == 1:
            yield {"type": "usage", "usage_reported": True, "stream_complete": True, "total_tokens": 4096}
            yield {"type": "done", "stop_reason": "length"}
            return
        yield {"type": "tool_call", "id": "f", "name": "finish", "arguments": {"summary": "ok"}}
        yield {"type": "usage", "usage_reported": True, "stream_complete": True, "total_tokens": 10}
        yield {"type": "done", "stop_reason": "tool_calls"}


def test_a_truncated_reply_is_continued_not_taken_as_a_stop(tmp_path):
    t, proj, orfs = _tools(tmp_path)
    transcript, calls = [], []
    stop = rb.run_loop(TruncatingAdapter(), t, "task", dict(rb.DEFAULT_CAPS), transcript, calls)
    assert stop == "finished" and len(calls) == 2
    assert any("harness_nudge" in e for e in transcript)


def test_read_log_accepts_a_path_and_tool_errors_do_not_end_the_attempt(tmp_path):
    t, proj, orfs = _tools(tmp_path)
    (proj / "agent_logs").mkdir(exist_ok=True)
    (proj / "agent_logs" / "flow.log").write_text("a\nERROR x\n")
    # smoke test: the model passed an absolute path and rglob raised NotImplementedError
    assert "ERROR x" in t.read_log(str(proj / "agent_logs" / "flow.log"))["text"]
    transcript = []
    stop = rb.run_loop(FakeAdapter([[("read_log", {"name": "/etc/passwd"})], [("finish", {"summary": "x"})]]),
                       t, "task", dict(rb.DEFAULT_CAPS), transcript, [])
    assert stop == "finished" and "error" in transcript[1]["result"]


def test_janitor_keeps_the_graded_layout_and_reports(tmp_path):
    import janitor
    case = tmp_path / "cases" / "c1"
    proj = case / "c1_bare"
    for run in ("RUN_A", "RUN_B"):
        r = proj / "backend" / run
        (r / "results").mkdir(parents=True)
        (r / "objects").mkdir()
        for n in ("6_final.gds", "6_final.def", "5_route.odb", "6_final.spef"):
            (r / "results" / n).write_text("x" * 100)
        (r / "objects" / "lib.lyt").write_text("x")
        (r / "flow.log").write_text("log")
    (proj / "reports").mkdir()
    (proj / "reports" / "drc.json").write_text("{}")
    # open case: nothing is touched
    assert janitor.prune.__name__ and list(janitor.closed_projects(tmp_path, "bare")) == []
    (case / "result.json").write_text(json.dumps({"run": "RUN_B"}))
    for p, graded in janitor.closed_projects(tmp_path, "bare"):
        janitor.prune(p, graded, dry=False)
    left = sorted(str(f.relative_to(proj)) for f in proj.rglob("*") if f.is_file())
    assert left == ["backend/RUN_A/flow.log", "backend/RUN_B/flow.log",
                    "backend/RUN_B/results/6_final.def", "backend/RUN_B/results/6_final.gds",
                    "reports/drc.json"]
