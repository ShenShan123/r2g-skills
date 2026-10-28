from pathlib import Path
import json

import pytest

from r2g_agent import frontend, project

FLAT = """
=== top ===
      109  173.166      109  173.166 cells
   Chip area for module '\\top': 173.166000
"""


def _w(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def test_included_v_is_a_header_not_a_source(tmp_path):
    src = tmp_path / "src"
    _w(src / "top.v", 'module top(input a, output y);\n`include "inc/modes.v"\nassign y = a;\nendmodule\n')
    _w(src / "inc" / "modes.v", "localparam M = 1;\ninitial begin end\n")
    meta = project.create(src, tmp_path / "p", "nangate45")
    assert [Path(f).name for f in meta["files"]] == ["top.v"]
    assert meta["frontend_fixes"][0]["recipe"] == "header_not_source"
    assert str(tmp_path / "p" / "rtl" / "inc") in meta["include_dirs"]


def test_loose_snippet_dropped_defines_only_file_kept(tmp_path):
    src = tmp_path / "src"
    _w(src / "top.v", "module top(input a, output y); assign y = a; endmodule\n")
    _w(src / "insert.v", "// paste into your module\n`define S 25\nreg [3:0] r;\nalways @(*) r = `S;\n")
    _w(src / "defs.v", "`define WIDTH 8\n`timescale 1ns/1ps\n")
    meta = project.create(src, tmp_path / "p", "nangate45", top="top")
    assert sorted(Path(f).name for f in meta["files"]) == ["defs.v", "top.v"]
    assert "代码片段" in meta["frontend_fixes"][0]["detail"]


def test_commented_include_is_not_a_header(tmp_path):
    src = tmp_path / "src"
    _w(src / "top.v", 'module top(input a, output y);\n// `include "sub.v"\nsub u(.a(a), .y(y));\nendmodule\n')
    _w(src / "sub.v", "module sub(input a, output y); assign y = a; endmodule\n")
    meta = project.create(src, tmp_path / "p", "nangate45", top="top")
    assert sorted(Path(f).name for f in meta["files"]) == ["sub.v", "top.v"]
    assert meta["frontend_fixes"] == []


def test_readmem_parameter_and_literal_become_absolute(tmp_path):
    src = tmp_path / "src"
    _w(src / "hw" / "mem" / "top.v",
       'module top #(parameter F = "hw/mem/prog.hex") (input c);\n'
       'reg [7:0] m [0:3];\ninitial $readmemh(F, m);\n'
       'reg [7:0] n [0:3];\ninitial $readmemb("tab.mem", n);\nendmodule\n')
    _w(src / "hw" / "mem" / "prog.hex", "00\n01\n02\n03\n")
    _w(src / "hw" / "mem" / "tab.mem", "0\n1\n0\n1\n")
    meta = project.create(src, tmp_path / "p", "nangate45")
    text = (tmp_path / "p" / "rtl" / "hw" / "mem" / "top.v").read_text()
    rtl = tmp_path / "p" / "rtl" / "hw" / "mem"
    assert '"%s"' % (rtl / "prog.hex") in text and '"%s"' % (rtl / "tab.mem") in text
    assert (rtl / "prog.hex").read_text() == "00\n01\n02\n03\n"
    assert [f["recipe"] for f in meta["frontend_fixes"]] == ["readmem_absolute"] * 2
    assert meta["missing_data_files"] == []


def test_readmem_missing_or_ambiguous_is_reported_not_invented(tmp_path):
    src = tmp_path / "src"
    _w(src / "top.v", 'module top(input c);\nreg [7:0] m [0:3];\ninitial $readmemh("gone.hex", m);\n'
       'reg [7:0] n [0:3];\ninitial $readmemh("x/dup.hex", n);\nendmodule\n')
    _w(src / "a" / "dup.hex", "0\n")
    _w(src / "b" / "dup.hex", "1\n")
    meta = project.create(src, tmp_path / "p", "nangate45")
    miss = {m["reference"]: m["reason"] for m in meta["missing_data_files"]}
    assert set(miss) == {"gone.hex", "x/dup.hex"} and "多个" in miss["x/dup.hex"]
    assert '"gone.hex"' in (tmp_path / "p" / "rtl" / "top.v").read_text()


def test_unique_basename_fallback(tmp_path):
    src = tmp_path / "src"
    _w(src / "top.v", 'module top(input c);\nreg [7:0] m [0:3];\n'
       'initial $readmemh("repo/data/prog.hex", m);\nendmodule\n')
    _w(src / "data" / "prog.hex", "0\n")
    meta = project.create(src, tmp_path / "p", "nangate45")
    assert "按唯一文件名找到" in meta["frontend_fixes"][0]["detail"]


def test_readmem_fix_preserves_source_bytes_and_line_endings(tmp_path):
    src = tmp_path / "src"
    original = (b'// original comment: \xf1\r\nmodule top(input c, output [7:0] q);\r\n'
                b'reg [7:0] m [0:3];\r\ninitial $readmemh("prog.hex", m);\r\n'
                b'assign q=m[0];\r\nendmodule\r\n')
    src.mkdir()
    (src / "top.v").write_bytes(original)
    _w(src / "prog.hex", "01 02 03 04\n")
    p = tmp_path / "project"
    project.create(src, p, "nangate45")
    expected = original.replace(b'"prog.hex"', ('"' + str(p / 'rtl/prog.hex') + '"').encode())
    assert (p / "rtl/top.v").read_bytes() == expected
    assert (src / "top.v").read_bytes() == original
    assert (p / "rtl/prog.hex").read_bytes() == (src / "prog.hex").read_bytes()


ALDFF_LOG = "Warning: Cell top.q$_ALDFFE_PNP_ is an unmapped internal cell of type $_ALDFFE_PNP_.\n"
STA_LOG = "[ERROR STA-0171] /work/results/nangate45/top/base/1_2_yosys.v line 3, syntax error\n"


def test_synth_signatures(tmp_path):
    net = _w(tmp_path / "1_2_yosys.v", '(* altera_attribute = "-name SDC_STATEMENT \\"x\\"" *)\nmodule top();\n')
    plain = _w(tmp_path / "plain.v", "module top();\n")
    ids = lambda log, n, tried=(): [r["id"] for r in frontend.match_synth_failure(log, n, list(tried))]
    assert ids(ALDFF_LOG, None) == []
    assert ids(STA_LOG, net) == ["strip_vendor_attr"]
    assert ids(STA_LOG, plain) == []                   # STA-0171 on something else
    assert ids(STA_LOG, None) == []
    assert ids(ALDFF_LOG + STA_LOG, net, ["aldff_to_dffsr"]) == ["strip_vendor_attr"]
    assert ids("ERROR: Found 2 problems in 'check -assert'.\n", net) == []
    for r in frontend.SYNTH_RECIPES:
        assert frontend.hook_path(r["id"]).is_file()


def test_synth_hook_survives_sizing(tmp_path):
    src = _w(tmp_path / "src" / "top.v", "module top(input clk, output q); assign q = clk; endmodule\n")
    pdir = tmp_path / "p"
    project.create(src.parent, pdir, "nangate45")
    project.add_synth_hooks(pdir, ["strip_vendor_attr"])
    project.add_synth_hooks(pdir, ["strip_vendor_attr", "strip_vendor_attr"])
    hook = pdir / "constraints" / "frontend_fix.tcl"
    assert hook.read_text().count("source {") == 1
    _w(tmp_path / "s.txt", FLAT)
    _w(tmp_path / "n.v", "module top(clk, q);\n input clk;\n output q;\nendmodule\n")
    project.apply_sizing(pdir, tmp_path / "s.txt", tmp_path / "n.v")
    cfg = (pdir / "constraints" / "config.mk").read_text()
    assert "export SYNTH_CANONICALIZE_TCL = %s" % hook in cfg and "DIE_AREA" in cfg


def test_quarantined_hook_cannot_be_requested_or_partially_written(tmp_path):
    src = _w(tmp_path / "src/top.v", "module top(input clk, output q); assign q = clk; endmodule\n")
    pdir = tmp_path / "p"
    project.create(src.parent, pdir, "nangate45")
    project.add_synth_hooks(pdir, ["strip_vendor_attr"])
    files = [pdir / "constraints/config.mk", pdir / "constraints/frontend_fix.tcl", pdir / "metadata.json"]
    before = {p: p.read_bytes() for p in files}
    with pytest.raises(ValueError, match="复位行为反例"):
        project.add_synth_hooks(pdir, ["aldff_to_dffsr"])
    assert all(p.read_bytes() == value for p, value in before.items())
    assert frontend.semantic_blockers(ALDFF_LOG)[0]["recipe"] == "aldff_to_dffsr"
    assert frontend.semantic_blockers(STA_LOG) == []


@pytest.mark.parametrize("platform", list(project.platforms.PLATFORMS))
def test_pipeline_reports_semantic_hold_without_converting(tmp_path, monkeypatch, platform):
    from r2g_agent.jobs import JobStore
    from r2g_agent.pipeline import Pipeline
    src = tmp_path / "src"
    original = "module top(input clk, output q); assign q = clk; endmodule\n"
    _w(src / "top.v", original)
    store = JobStore()
    jid = store.create_job("semantic hold", {}, [{"name": "top"}])
    pipe = Pipeline(store, ingest=False)
    calls = []

    def run(_jid, _name, stage, cmd, pdir, **kwargs):
        calls.append(stage)
        _w(pdir / "agent_logs/synth.log", ALDFF_LOG)
        return 1

    monkeypatch.setattr(pipe, "_sh", run)
    p = tmp_path / "project"
    try:
        pipe._run_design_inner(jid, "top", src, "top", p, platform)
        assert calls == ["synth"]
        result = store.design(jid, "top")
        assert result["status"] == "failed"
        assert "复位行为反例" in result["detail"]["reason"]
        assert not json.loads((p / "metadata.json").read_text()).get("synth_hooks")
        assert (p / "rtl/top.v").read_text() == original
    finally:
        pipe.pool.shutdown()


@pytest.mark.parametrize("diagnostic", [
    "syntax error, unexpected TOK_REG",
    "syntax error, unexpected OP_CAST",
    "syntax error, unexpected '[', expecting ')' or ',' or '='",
    "Failed to detect width for parameter \\C_OP!",
])
def test_sv_frontend_only_once_after_source_diagnostic(diagnostic):
    log = "/inputs/top.sv:25: ERROR: " + diagnostic
    assert [r["id"] for r in frontend.match_synth_failure(log, None, [])] == ["sv_frontend"]
    assert frontend.match_synth_failure(log, None, ["sv_frontend"]) == []


@pytest.mark.parametrize("log", [STA_LOG, "ERROR: Module missing referenced in top",
                                     "[ERROR DRT-0305] Router failed",
                                     "ERROR: syntax error, unexpected TOK_REG"])
def test_other_failures_do_not_select_sv_frontend(log):
    assert frontend.match_synth_failure(log, None, []) == []


def test_slang_activation_preserves_sources_defines_hooks_and_sizing(tmp_path, monkeypatch):
    binary = _w(tmp_path / "yosys", "#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    monkeypatch.setenv("R2G_SLANG_YOSYS", str(binary))
    src = tmp_path / "src"
    original = 'module top(input clk, output q); assign q = clk; endmodule\n'
    _w(src / "top.sv", original)
    _w(src / "design.json", json.dumps({"top": "top", "defines": ["WIDTH=8"]}))
    p = tmp_path / "project"
    meta = project.create(src, p, "nangate45")
    assert "VERILOG_DEFINES = -DWIDTH=8" in (p / "constraints/config.mk").read_text()
    meta = project.add_synth_hooks(p, ["strip_vendor_attr"])
    attempt = frontend.prepare_slang(p, meta)
    config_before = (p / "constraints/config.mk").read_bytes()
    with pytest.raises(ValueError, match="Verilog"):
        project.activate_slang(p, attempt)
    assert (p / "constraints/config.mk").read_bytes() == config_before
    _w(Path(attempt["output"]), original)
    activated = project.activate_slang(p, attempt)
    assert (p / "rtl/top.sv").read_text() == original
    assert activated["sv_frontend"]["original_files"] == meta["files"]
    assert activated["sv_frontend"]["original_defines"] == ["WIDTH=8"]
    assert len(activated["sv_frontend"]["input_sha256"]["rtl/top.sv"]) == 64
    assert len(activated["sv_frontend"]["output_sha256"]) == 64
    project.apply_sizing(p, _w(tmp_path / "stat", FLAT),
                         _w(tmp_path / "net.v", "module top(clk, q);\n input clk;\n output q;\nendmodule\n"))
    cfg = (p / "constraints/config.mk").read_text()
    assert "VERILOG_FILES = " + attempt["output"] in cfg
    assert "VERILOG_DEFINES" not in cfg and "SYNTH_CANONICALIZE_TCL" in cfg
    assert "DIE_AREA" in cfg


@pytest.mark.parametrize("translator_rc", [0, 1])
def test_pipeline_retries_only_successful_translation(tmp_path, monkeypatch, translator_rc):
    from r2g_agent.jobs import JobStore
    from r2g_agent.pipeline import Pipeline
    src = tmp_path / "src"
    original = "module top(); endmodule\n"
    _w(src / "top.sv", original)
    store = JobStore()
    jid = store.create_job("frontend", {}, [{"name": "top"}])
    p = tmp_path / "project"
    pipe = Pipeline(store, ingest=False)
    binary = _w(tmp_path / "yosys", "#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    monkeypatch.setenv("R2G_SLANG_YOSYS", str(binary))
    calls = []

    def run(_jid, _name, stage, cmd, pdir, **kwargs):
        calls.append(stage)
        if stage == "synth":
            _w(pdir / "agent_logs/synth.log", str(pdir / "rtl/top.sv")
               + ":25: ERROR: syntax error, unexpected OP_CAST\n")
            return 1
        assert stage == "frontend"
        # Even a failed tool may leave a partial output; it must not activate.
        _w(pdir / "frontend/slang.v", original)
        return translator_rc

    monkeypatch.setattr(pipe, "_sh", run)
    pipe._run_design_inner(jid, "top", src, "top", p, "nangate45")
    assert calls == (["synth", "frontend", "synth"] if translator_rc == 0 else ["synth", "frontend"])
    m = json.loads((p / "metadata.json").read_text())
    assert bool(m.get("sv_frontend")) == (translator_rc == 0)
    assert store.design(jid, "top")["status"] == "failed"
    if translator_rc == 1:
        assert m["files"] == [str(p / "rtl/top.sv")]
    pipe.pool.shutdown()
