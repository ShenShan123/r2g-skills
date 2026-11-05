"""Bare-agent harness: starting state, sandbox and budgets, without a model or EDA."""
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bare_agent"))
import bare_tools as bt  # noqa: E402
import launch_bare  # noqa: E402,F401
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


def test_prepare_carries_the_baselines_staged_platform_overrides(tmp_path):
    # gf180's frozen setup: PDN and tapcell overrides staged beside config.mk at /work.
    # Copying only config.mk + SDC dropped both, so every bare gf180 run fell back to
    # the stock PDN (V2.1/V3.1) and tap distance (DF.13/14_MV) -- 2026-11-04.
    case, base = _case(tmp_path)
    cfg = (base / "config.mk").read_text()
    (base / "config.mk").write_text(cfg + "export PDN_TCL = /work/pdn_override.cfg\n"
                                    "export TAPCELL_TCL = /work/tapcell_override.tcl\n")
    (base / "pdn_override.cfg").write_text("define_pdn_grid -name {block}\n")
    (base / "tapcell_override.tcl").write_text("tapcell -distance 30\n")
    proj = tmp_path / "out" / "gf180_C1_bare"
    rb.prepare(case, base, proj)
    out = (proj / "constraints/config.mk").read_text()
    assert "export PDN_TCL = %s" % (proj / "constraints/pdn_override.cfg") in out
    assert "export TAPCELL_TCL = %s" % (proj / "constraints/tapcell_override.tcl") in out
    assert (proj / "constraints/tapcell_override.tcl").read_text() == "tapcell -distance 30\n"
    assert "/work/" not in out


def test_prepare_refuses_a_staged_file_the_baseline_does_not_provide(tmp_path):
    case, base = _case(tmp_path)
    (base / "config.mk").write_text((base / "config.mk").read_text() + "export PDN_TCL = /work/pdn_override.cfg\n")
    with pytest.raises(SystemExit, match="pdn_override.cfg"):
        rb.prepare(case, base, tmp_path / "out" / "gf180_C1_bare")


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


def test_compaction_shrinks_old_results_in_one_block_and_keeps_recent():
    msgs = [{"role": "user", "content": "task"}]
    for i in range(10):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": "t%d" % i, "name": "read_file", "arguments": {"path": "f%d.v" % i}}]})
        msgs.append({"role": "tool", "tool_call_id": "t%d" % i, "name": "read_file", "content": "x" * 15000})
    transcript = []
    assert rb.compact(msgs, transcript) is True
    tools = [m for m in msgs if m["role"] == "tool"]
    assert all(m.get("_compacted") for m in tools[:-rb.KEEP_RECENT])
    assert all(len(m["content"]) == 15000 for m in tools[-rb.KEEP_RECENT:])
    assert "f0.v" in tools[0]["content"] and len(tools[0]["content"]) < 600
    # below the threshold again: no further rewrite, so the cached prefix stays stable
    assert rb.compact(msgs, transcript) is False


def test_cost_cap_replaces_the_token_cap(tmp_path):
    t, proj, orfs = _tools(tmp_path)

    class Pricey(FakeAdapter):
        def stream(self, messages, specs, system):
            yield {"type": "tool_call", "id": "x%d" % self.n, "name": "list_dir", "arguments": {"path": "."}}
            self.n += 1
            yield {"type": "usage", "usage_reported": True, "stream_complete": True,
                   "input_tokens": 1_000_000, "cache_read_input_tokens": 0, "uncached_input_tokens": 1_000_000,
                   "output_tokens": 0, "total_tokens": 1_000_000}
            yield {"type": "done", "stop_reason": "tool_calls"}
    calls = []
    stop = rb.run_loop(Pricey([]), t, "task", {**rb.DEFAULT_CAPS, "cost_cny": 5.0}, [], calls)
    assert stop == "cap_cost" and len(calls) == 3          # 2 CNY per call at peak uncached price
    assert "total_tokens" not in rb.DEFAULT_CAPS


def test_semantic_review_clears_location_fixes_and_flags_logic(tmp_path):
    case, base = _case(tmp_path)
    (Path(case["input_root"]) / "src/rtl/mem.v").write_text(
        'module mem; initial $readmemh("C:\\\\proj\\\\a b\\\\init.hex", m); endmodule\r\n')
    proj = tmp_path / "out" / "nangate45_C1_bare"
    r = rb.prepare(case, base, proj)
    rtl = proj / "rtl"
    (rtl / "design.json").write_text("{}")                                         # metadata
    (rtl / "src/rtl/mem.v").write_text('module mem; initial $readmemh("init.hex", m); endmodule\n')
    (rtl / "flat").mkdir()
    (rtl / "flat/top.v").write_text((rtl / "src/rtl/top.v").read_text())          # copy
    (rtl / "src/rtl/defs.vh").rename(rtl / "flat/defs.vh")                         # move
    assert rb.semantic_review(proj, r) == {"pending": [], "cleared": [
        {"file": "flat/defs.vh", "why": "copy_of_original"},
        {"file": "flat/top.v", "why": "copy_of_original"},
        {"file": "src/rtl/mem.v", "why": "path_literals_only"},
        {"file": "src/rtl/defs.vh", "why": "moved"}]}
    (rtl / "src/rtl/top.v").write_text("module top(input a, output y); assign y = 1'b0; endmodule\n")
    (rtl / "src/rtl/mem.v").write_text('module mem; initial $readmemh("init.hex", n); endmodule\n')
    assert rb.semantic_review(proj, r)["pending"] == ["src/rtl/mem.v", "src/rtl/top.v"]
    # a changed non-path literal is logic, not location
    assert not rb._path_literals_only(['$display("a");'], ['$display("b");'])


def test_launcher_gives_an_invocation_voided_attempt_one_fresh_retry(tmp_path, monkeypatch):
    import launch_bare as lb
    monkeypatch.setenv("ORFS_ROOT", str(tmp_path / "orfs"))
    runs = []

    def fake_call(cmd, **kw):
        out = Path(cmd[cmd.index("--out") + 1])
        runs.append(out)
        complete = len(runs) > 1 or "C2" in str(out)
        (out / "result.json").write_text(json.dumps({"usage_complete": complete}))
        return 0
    monkeypatch.setattr(lb.subprocess, "call", fake_call)
    a = type("A", (), dict(root=tmp_path / "root", baselines=tmp_path, repo=tmp_path, base_url="u",
                           model="m", max_tokens=32768, caps="", protocol="openai", reasoning_effort=None, max_load=0, container_image="", start_spacing=0))
    cid, status = lb.run_one({"id": "n_C1", "platform": "nangate45"}, a)
    assert status.endswith("attempt 2") and len(runs) == 2
    assert json.loads((a.root / "cases/n_C1/result.json").read_text())["usage_complete"]
    assert len(list((a.root / "voided").iterdir())) == 1
    runs.clear()
    # an incomplete second attempt is kept as the result, not retried again
    monkeypatch.setattr(lb.subprocess, "call", lambda cmd, **kw: (runs.append(1), Path(
        cmd[cmd.index("--out") + 1], "result.json").write_text('{"usage_complete": false}'))[0] and 0)
    cid, status = lb.run_one({"id": "n_C3", "platform": "nangate45"}, a)
    assert status.endswith("attempt 2") and len(runs) == 2
    assert (a.root / "cases/n_C3/result.json").is_file()


def test_an_invocation_outage_is_waited_out_not_taken_as_the_end(tmp_path, monkeypatch):
    t, proj, orfs = _tools(tmp_path)
    monkeypatch.setattr(rb.time, "sleep", lambda s: None)

    class Outage(FakeAdapter):
        def stream(self, messages, specs, system):
            self.n += 1
            if self.n <= 2:
                yield {"type": "error", "message": "连接模型失败：timed out"}
                return
            yield from FakeAdapter([[("finish", {"summary": "ok"})]]).stream(messages, specs, system)
    calls, failed, transcript = [], [], []
    stop = rb.run_loop(Outage([]), t, "task", dict(rb.DEFAULT_CAPS), transcript, calls, failed)
    assert stop == "finished" and len(calls) == 1 and len(failed) == 2
    assert [f["backoff"] for f in failed] == [30, 60] and not any(f["partial_output"] for f in failed)

    class Down(FakeAdapter):
        def stream(self, messages, specs, system):
            yield {"type": "error", "message": "down"}
    t2, _, _ = _tools(tmp_path / "b")
    failed = []
    assert rb.run_loop(Down([]), t2, "task", dict(rb.DEFAULT_CAPS), [], [], failed) == "model_error"
    assert sum(f.get("backoff", 0) for f in failed) == rb.INVOCATION_WAIT


def test_sandbox_follows_a_symlinked_orfs_layout(tmp_path):
    t, proj, orfs = _tools(tmp_path)
    shadow = tmp_path / "shadow"
    (shadow / "flow").mkdir(parents=True)
    for e in ("scripts", "platforms"):
        (shadow / "flow" / e).symlink_to(orfs / "flow" / e)
    (shadow / "flow" / "results").mkdir()
    t2 = bt.BareTools(proj, "nangate45", tmp_path / "repo", shadow, {})
    assert t2.read_file(str(shadow / "flow/platforms/nangate45/config.mk"))["text"] == "x"
    with pytest.raises(bt.ToolError):
        t2.write_file(str(shadow / "flow/platforms/nangate45/config.mk"), "y")


def test_launcher_container_mode_mounts_a_per_design_workspace(tmp_path, monkeypatch):
    import launch_bare as lb
    seen = []

    def fake_call(cmd, **kw):
        seen.append(cmd)
        out = Path(cmd[cmd.index("--out") + 1])
        (out / "result.json").write_text('{"usage_complete": true}')
        return 0
    monkeypatch.setattr(lb.subprocess, "call", fake_call)
    a = type("A", (), dict(root=tmp_path / "root", baselines=tmp_path, repo=tmp_path / "rt", base_url="u",
                           model="m", max_tokens=32768, caps="", protocol="openai", reasoning_effort=None, max_load=0, container_image="img:1",
                           container_orfs="/orfs", container_ws=tmp_path / "ws",
                           container_env_file=tmp_path / "e.env", container_mount=["/x:/x:ro,Z"],
                           start_spacing=0))
    lb.run_one({"id": "n_C1", "platform": "nangate45"}, a)
    cmd = seen[0]
    assert cmd[:3] == ["podman", "run", "--rm"] and "img:1" in cmd and cmd[cmd.index("img:1") + 1] == "python3"
    assert "%s:/orfs/flow/results:Z" % (tmp_path / "ws/n_C1/results") in cmd and "/x:/x:ro,Z" in cmd
    assert not (tmp_path / "ws/n_C1").exists()          # workspace removed after the design


YOSYS = "/opt/OpenROAD/oss-cad-suite/bin/yosys"


@pytest.mark.skipif(not Path(YOSYS).is_file(), reason="needs a yosys")
def test_equiv_review_proves_a_location_fix_and_refutes_a_logic_change(tmp_path):
    import subprocess
    import equiv_review as er
    src = tmp_path / "src"
    src.mkdir()
    (src / "sub.v").write_text("module sub(input clk, input d, output reg q); always @(posedge clk) q <= d; endmodule\n")
    (src / "top.v").write_text('`include "sub.v"\nmodule top(input clk, input a, output y); sub u(.clk(clk), .d(a), .q(y)); endmodule\n')
    gold = {"top": "top", "files": [str(src / "sub.v"), str(src / "top.v")], "incdirs": [str(src)], "defines": []}
    fixed = tmp_path / "fixed"
    fixed.mkdir()
    (fixed / "top.v").write_text("module top(input clk, input a, output y); sub u(.clk(clk), .d(a), .q(y)); endmodule\n")
    gate_ok = {**gold, "files": [str(src / "sub.v"), str(fixed / "top.v")]}
    (fixed / "bad.v").write_text("module top(input clk, input a, output y); sub u(.clk(clk), .d(~a), .q(y)); endmodule\n")
    gate_bad = {**gold, "files": [str(src / "sub.v"), str(fixed / "bad.v")]}

    def status(gate):
        ys = tmp_path / "e.ys"
        ys.write_text(er.script(gold, gate))
        p = subprocess.run([YOSYS, "-q", "-l", str(tmp_path / "l"), "-s", str(ys)], capture_output=True, text=True)
        log = (tmp_path / "l").read_text()
        m = re.search(r"Of those cells (\d+) are proven and (\d+) are unproven", log)
        return p.returncode, (int(m.group(1)), int(m.group(2))) if m else None
    rc, (proven, unproven) = status(gate_ok)
    assert rc == 0 and proven > 0 and unproven == 0          # duplicate definition read once, then proven
    rc, (proven, unproven) = status(gate_bad)
    assert unproven > 0                                        # an inverted input is caught


def test_fallback_starts_from_r2g_project_with_its_context(tmp_path):
    case, base = _case(tmp_path)
    r2g = tmp_path / "ws" / "top__job1"
    (r2g / "rtl/src/rtl").mkdir(parents=True)
    (r2g / "rtl/src/rtl/top.v").write_text("module top(input a, output y); assign y = a; endmodule\n")
    (r2g / "constraints").mkdir()
    (r2g / "constraints/constraint.sdc").write_text("create_clock -period 3.0 [get_ports a]\n")
    (r2g / "constraints/config.mk").write_text(
        "export VERILOG_FILES = %s/rtl/src/rtl/top.v\nexport SDC_FILE = %s/constraints/constraint.sdc\n" % (r2g, r2g))
    (r2g / "backend/RUN_1/results").mkdir(parents=True)
    (r2g / "backend/RUN_1/results/6_final.gds").write_text("x" * 100)
    (r2g / "reports").mkdir()
    (r2g / "reports/drc.json").write_text('{"status": "violations"}')
    (r2g / "reports/fix_log.jsonl").write_text('{"strategy": "none", "verdict": "stop_residual"}\n')
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "diagnosis_drc.json").write_text('{"status": "residual"}')
    proj = tmp_path / "out" / "nangate45_C1_bare"
    r = rb.prepare_from_project(case, r2g, proj, ctx)
    cfg = (proj / "constraints/config.mk").read_text()
    assert str(proj) in cfg and str(r2g) not in cfg                      # paths now name this project
    assert not (proj / "backend").exists() and not list((proj / "reports").iterdir())   # graded fresh
    assert sorted(p.name for p in (proj / "r2g_context").iterdir()) == ["diagnosis_drc.json", "drc.json", "fix_log.jsonl"]
    assert r["sdc_sha256"] == rb.sha(proj / "constraints/constraint.sdc") and r["from_project"] == str(r2g)
    assert rb.semantic_review(proj, r) == {"cleared": [], "pending": []}   # untouched R2G RTL is not an edit
    t = bt.BareTools(proj, "nangate45", tmp_path / "repo", tmp_path / "orfs", {})
    t.force_synth_first = True
    seen = []
    t._sh = lambda stage, cmd, env, timeout: seen.append(env.get("FROM_STAGE")) or 0
    t._latest_run = lambda: None
    t.run_flow("route")
    t.run_flow("route")
    assert seen == [None, "route"]                                       # first run forced to synth


def test_hybrid_classifies_r2g_stops_from_the_fix_log(tmp_path):
    import hybrid
    p = tmp_path / "proj"
    (p / "reports").mkdir(parents=True)
    assert hybrid.classify(p) == "no_recipe"                         # synth/flow failure, no rows
    (p / "reports/fix_log.jsonl").write_text('{"strategy": "none", "verdict": "stop_residual"}\n')
    assert hybrid.classify(p) == "no_recipe"
    (p / "reports/fix_log.jsonl").write_text('{"strategy": "dont_use_violating_master", "verdict": "recipe_no_effect"}\n'
                                             '{"strategy": "none", "verdict": "stop_residual"}\n')
    assert hybrid.classify(p) == "recipe_failed"
    case = tmp_path / "cases" / "n_C1"
    case.mkdir(parents=True)
    (case / "summary.json").write_text(json.dumps({"designs": [{"project": str(p), "signoff": {"dual_clean": True}}]}))
    assert hybrid.r2g_outcome(case)["outcome"] == "r2g_pass"
    (case / "summary.json").write_text(json.dumps({"designs": [{"project": str(p), "stage": "signoff",
                                                                "signoff": {"dual_clean": False}}]}))
    assert hybrid.r2g_outcome(case)["outcome"] == "recipe_failed"


def test_verdict_binds_by_gds_digest_when_gf180_drc_has_no_run_tag(tmp_path):
    t, proj, orfs = _tools(tmp_path)
    run = proj / "backend" / "RUN_9"
    (run / "results").mkdir(parents=True)
    (run / "results/6_final.gds").write_text("layout")
    g = rb.sha(run / "results/6_final.gds")
    (proj / "reports").mkdir(exist_ok=True)
    (proj / "reports/lvs.json").write_text(json.dumps({"status": "clean", "gds_sha256": g, "run_tag": "RUN_9"}))
    receipt = {"rtl_sha256": {}, "sdc_sha256": rb.sha(proj / "constraints/constraint.sdc"), "input_root": str(proj / "rtl")}
    pdir = orfs / "flow/platforms/nangate45"
    base = {"status": "clean", "total_violations": 0, "drc_mode": "full", "gds_sha256": g}
    for tag, ok in ((None, True), ("RUN_9", True), ("RUN_8", False)):
        (proj / "reports/drc.json").write_text(json.dumps({**base, "run_tag": tag}))
        assert rb.verdict(t, proj, receipt, pdir, rb.tree_hash(pdir))["bound_to_layout"] is ok
    (proj / "reports/drc.json").write_text(json.dumps({**base, "run_tag": None, "gds_sha256": "other"}))
    assert rb.verdict(t, proj, receipt, pdir, rb.tree_hash(pdir))["bound_to_layout"] is False   # digest still required


def test_container_name_prefix_separates_arms_on_one_host(tmp_path):
    import launch_bare
    case = {"id": "gf180_C00006", "platform": "gf180"}
    base = dict(container_ws=tmp_path, container_orfs="/orfs", container_mount=[], container_memory="",
                container_env_file=tmp_path / "e", repo=tmp_path, container_image="img:1")
    names = []
    for extra in ({}, {"container_name_prefix": "bare_gpt_"}):
        a = type("A", (), {**base, **extra})
        cmd = launch_bare.container_cmd(["python3", "run_bare.py"], case, a)
        names.append(cmd[cmd.index("--name") + 1])
    assert names == ["bare_gf180_C00006", "bare_gpt_gf180_C00006"]


def test_cgroup_headroom_takes_the_tightest_limited_ancestor(tmp_path):
    """203: the user slice is capped at 200 GB on a host with far more RAM, so the
    launcher's memory gate must see the cgroup's headroom, not only MemAvailable."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("launch_bare", Path(__file__).resolve().parents[1] / "bare_agent" / "launch_bare.py")
    lb = importlib.util.module_from_spec(spec); spec.loader.exec_module(lb)
    G = 2**30
    root = tmp_path / "cg"; user = root / "user.slice" / "user-1.slice"; scope = user / "session-1.scope"
    scope.mkdir(parents=True)
    (root / "user.slice" / "memory.max").write_text("max\n"); (root / "user.slice" / "memory.current").write_text(str(500 * G))
    (user / "memory.max").write_text(str(200 * G)); (user / "memory.current").write_text(str(174 * G))
    (scope / "memory.max").write_text("max\n"); (scope / "memory.current").write_text(str(10 * G))
    pc = tmp_path / "cgroup"; pc.write_text("0::/user.slice/user-1.slice/session-1.scope\n")
    assert abs(lb.cgroup_headroom_gb(str(pc), str(root)) - 26) < 1e-9
    # cold page cache is reclaimable: it does not count against the headroom
    (user / "memory.stat").write_text("anon %d\ninactive_file %d\nactive_file %d\n" % (104 * G, 70 * G, 4 * G))
    assert abs(lb.cgroup_headroom_gb(str(pc), str(root)) - 96) < 1e-9
    pc.write_text("12:memory:/foo\n")  # cgroup v1: no limit known
    assert lb.cgroup_headroom_gb(str(pc), str(root)) == float("inf")


def _load_launch_bare():
    import importlib.util
    spec = importlib.util.spec_from_file_location("launch_bare_t", Path(__file__).resolve().parents[1] / "bare_agent" / "launch_bare.py")
    lb = importlib.util.module_from_spec(spec); spec.loader.exec_module(lb)
    return lb


def test_kill_leftovers_spares_another_arms_process(tmp_path, monkeypatch):
    """Two arms on one host name a design's variant identically; the reaper must
    only kill processes under its own ORFS_ROOT (203, 2026-11-05)."""
    import subprocess, time
    lb = _load_launch_bare()
    tag = "gf180_CTEST9_bare"
    script = "import time; time.sleep(60)  # " + tag
    other = subprocess.Popen([sys.executable, "-c", script], env={"ORFS_ROOT": "/other/orfs", "PATH": "/usr/bin:/bin"})
    mine = subprocess.Popen([sys.executable, "-c", script], env={"ORFS_ROOT": "/mine/orfs", "PATH": "/usr/bin:/bin"})
    try:
        time.sleep(0.3)
        monkeypatch.setenv("ORFS_ROOT", "/mine/orfs")
        lb.kill_leftovers({"id": "gf180_CTEST9"})
        assert mine.wait(timeout=10) == -9
        assert other.poll() is None
    finally:
        other.kill(); mine.kill()


def test_other_arm_runner_sees_the_same_design_in_another_root(tmp_path):
    import subprocess, time
    lb = _load_launch_bare()
    a, b = tmp_path / "armA" / "cases" / "x_CT1", tmp_path / "armB" / "cases" / "x_CT1"
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "run_bare.py", "--case", str(a / "case.json")])
    try:
        time.sleep(0.3)
        assert lb.other_arm_runner({"id": "x_CT1"}, b) == p.pid
        assert lb.other_arm_runner({"id": "x_CT1"}, a) is None
        assert lb.other_arm_runner({"id": "x_CT2"}, b) is None
    finally:
        p.kill()
