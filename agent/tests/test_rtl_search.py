from pathlib import Path

import pytest

from r2g_agent import rtl_search


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_strip_verification_nested_and_else():
    t = ("a\n`ifdef FORMAL\nf1\n`ifdef X\nf2\n`endif\nf3\n`else\nkeep\n`endif\n"
         "`ifndef FORMAL\nsynth\n`else\nformal\n`endif\n`ifdef USE_X\nx\n`endif\n")
    lines = [l for l in rtl_search.strip_verification(t).splitlines() if l.strip()]
    assert lines == ["a", "keep", "synth", "x"]


def test_designs_in_marks_missing_modules(tmp_path):
    _w(tmp_path / "rtl" / "top.v", "module top(input a, output y);\n sub u(.a(a), .y(y));\n"
       "`ifdef FORMAL\n checker c(.a(a));\n always @(*) assert property (y == a);\n`endif\nendmodule\n")
    _w(tmp_path / "rtl" / "sub.v", "module sub(input a, output y);\n"
       "always @(*) case (a) 1'b0: ; endcase\n assign y = a;\nendmodule\n")
    _w(tmp_path / "fpga" / "board.v", "module board(input clk);\n IBUFG b(.I(clk));\n top t();\nendmodule\n")
    ds = {d["rel"]: d for d in rtl_search.designs_in(tmp_path)}
    assert ds["rtl"]["self_contained"] and ds["rtl"]["top_candidates"] == ["top"]
    assert ds["fpga"]["missing_modules"] == ["IBUFG", "top"] and not ds["fpga"]["self_contained"]
    assert list(ds)[0] == "rtl"                               # self-contained first


@pytest.mark.parametrize("text,name", [("MIT License\n\nCopyright", "MIT"),
                                       ("Apache License\nVersion 2.0", "Apache-2.0"),
                                       ("GNU LESSER GENERAL PUBLIC LICENSE", "LGPL"),
                                       ("Some custom terms", "见 LICENSE（未识别）")])
def test_license(tmp_path, text, name):
    (tmp_path / "LICENSE").write_text(text)
    assert rtl_search._license(tmp_path) == name


def test_license_missing(tmp_path):
    assert rtl_search._license(tmp_path) == "未找到许可证文件"


def test_fetch_rejects_other_hosts():
    with pytest.raises(ValueError):
        rtl_search.fetch("https://example.com/a/b")
    with pytest.raises(ValueError):
        rtl_search.fetch("file:///etc")
