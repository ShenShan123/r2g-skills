from pathlib import Path

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


ALDFF_LOG = "Warning: Cell top.q$_ALDFFE_PNP_ is an unmapped internal cell of type $_ALDFFE_PNP_.\n"
STA_LOG = "[ERROR STA-0171] /work/results/nangate45/top/base/1_2_yosys.v line 3, syntax error\n"


def test_synth_signatures(tmp_path):
    net = _w(tmp_path / "1_2_yosys.v", '(* altera_attribute = "-name SDC_STATEMENT \\"x\\"" *)\nmodule top();\n')
    plain = _w(tmp_path / "plain.v", "module top();\n")
    ids = lambda log, n, tried=(): [r["id"] for r in frontend.match_synth_failure(log, n, list(tried))]
    assert ids(ALDFF_LOG, None) == ["aldff_to_dffsr"]
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
    project.add_synth_hooks(pdir, ["aldff_to_dffsr"])
    project.add_synth_hooks(pdir, ["strip_vendor_attr", "aldff_to_dffsr"])
    hook = pdir / "constraints" / "frontend_fix.tcl"
    assert hook.read_text().count("source {") == 2
    _w(tmp_path / "s.txt", FLAT)
    _w(tmp_path / "n.v", "module top(clk, q);\n input clk;\n output q;\nendmodule\n")
    project.apply_sizing(pdir, tmp_path / "s.txt", tmp_path / "n.v")
    cfg = (pdir / "constraints" / "config.mk").read_text()
    assert "export SYNTH_CANONICALIZE_TCL = %s" % hook in cfg and "DIE_AREA" in cfg
