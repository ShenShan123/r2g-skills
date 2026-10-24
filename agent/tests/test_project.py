from pathlib import Path

import pytest

from r2g_agent import platforms, project

FLAT = """
=== counter8 ===
      109  173.166      109  173.166 cells
   Chip area for module '\\counter8': 173.166000
"""
HIER = """
=== sub ===
       10   20.000       10   20.000 cells
=== top ===
      300  900.500      300  900.500 cells
"""
NETLIST = """module top(clk, d, q);
  input clk;
  input [7:0] d;
  output [15:0] q;
  wire x;
endmodule
"""


def test_synth_size_flat_and_hierarchical(tmp_path):
    (tmp_path / "a.txt").write_text(FLAT)
    (tmp_path / "b.txt").write_text(HIER)
    assert project.synth_size(tmp_path / "a.txt", "counter8") == (109, 173.166)
    # a hierarchical report must be read at the TOP's section, not the first one
    assert project.synth_size(tmp_path / "b.txt", "top") == (300, 900.5)


def test_count_io_bits(tmp_path):
    (tmp_path / "n.v").write_text(NETLIST)
    assert project.count_io_bits(tmp_path / "n.v", "top") == 1 + 8 + 16


def test_die_rules():
    p = platforms.get("nangate45")
    tiny = platforms.die_for(p, 173.0, 22)
    assert tiny["core_side_um"] == 32 and tiny["binding"] == "pdn_floor"   # PDN-0185 otherwise
    big = platforms.die_for(p, 50000.0, 100)
    assert big["binding"] == "area" and big["core_side_um"] == 320
    pinny = platforms.die_for(p, 500.0, 1000)
    assert pinny["binding"] == "pins" and pinny["core_side_um"] >= 1000 / (1.8 * 4)
    assert tiny["die_side_um"] == tiny["core_side_um"] + 4


def test_period_bins():
    p = platforms.get("nangate45")
    assert [platforms.period_for(p, n) for n in (50, 500, 2000, 9000)] == [0.5, 1.0, 1.6, 3.0]


def test_unsupported_platform():
    with pytest.raises(ValueError):
        platforms.get("asap7")


def test_infer_top(tmp_path):
    (tmp_path / "a.v").write_text("module leaf(input a, output b); assign b=a; endmodule\n")
    (tmp_path / "b.v").write_text("module top(input a, output b);\n  leaf u0 (.a(a), .b(b));\nendmodule\n")
    assert project.infer_top(project.find_hdl(tmp_path))[0] == "top"
    (tmp_path / "c.v").write_text("module other(input a); endmodule\n")
    with pytest.raises(ValueError):
        project.infer_top(project.find_hdl(tmp_path))


def test_create_and_size(tmp_path):
    src = Path(project.__file__).parent / "examples" / "counter8"
    pdir = tmp_path / "p"
    meta = project.create(src, pdir, "nangate45")
    assert meta["top"] == "counter8"
    cfg = (pdir / "constraints" / "config.mk").read_text()
    assert "CORE_UTILIZATION" in cfg and "DIE_AREA" not in cfg
    (tmp_path / "s.txt").write_text(FLAT)
    (tmp_path / "n.v").write_text("module counter8(clk, q);\n input clk;\n output [7:0] q;\nendmodule\n")
    s = project.apply_sizing(pdir, tmp_path / "s.txt", tmp_path / "n.v")
    cfg = (pdir / "constraints" / "config.mk").read_text()
    assert "export DIE_AREA  = 0 0 36 36" in cfg and "CORE_UTILIZATION" not in cfg
    assert "set _per 1.0" in (pdir / "constraints" / "constraint.sdc").read_text()
    assert s["pins"] == 9


def test_infer_top_sees_parameterised_instances(tmp_path):
    # the I2C_module case: multi-parameter overrides used to hide the instance
    (tmp_path / "m.v").write_text("module I2C_master #(parameter A=1)(input a); endmodule\n")
    (tmp_path / "s.v").write_text("module I2C_slave(input a); endmodule\n")
    (tmp_path / "t.v").write_text(
        "module I2C_module(input a);\n"
        "I2C_master #( .A(A), .B(f(1,2)), .C(3)) m1 (\n .a(a));\n"
        "I2C_slave #(.X(1)) s1 (.a(a));\nendmodule\n")
    assert project.infer_top(project.find_hdl(tmp_path))[0] == "I2C_module"


def test_infer_top_folder_name_breaks_tie(tmp_path):
    d = tmp_path / "array_mul"; d.mkdir()
    (d / "a.sv").write_text("module array_mul(input a); endmodule\n")
    (d / "p.sv").write_text("module csa_3_2(input a); endmodule\n")   # unused helper
    top, how = project.infer_top(project.find_hdl(d), hint="array_mul")
    assert top == "array_mul" and "文件夹名" in how


def test_synth_size_scientific_notation(tmp_path):
    (tmp_path / "s.txt").write_text(
        "=== array_mul ===\n     3359 6.25E+03     3359 6.25E+03 cells\n"
        "   Chip area for module '\\array_mul': 6247.276000\n")
    assert project.synth_size(tmp_path / "s.txt", "array_mul") == (3359, 6247.276)


def test_ppl_refusal_grows_only_the_die(tmp_path):
    src = Path(project.__file__).parent / "examples" / "counter8"
    pdir = tmp_path / "p"
    project.create(src, pdir, "nangate45")
    (tmp_path / "s.txt").write_text(FLAT)
    (tmp_path / "n.v").write_text("module counter8(clk, q);\n input clk;\n output [7:0] q;\nendmodule\n")
    before = project.apply_sizing(pdir, tmp_path / "s.txt", tmp_path / "n.v")
    log = ("[ERROR PPL-0024] Number of IO pins (313) exceeds maximum number of available positions (288). "
           "Increase the die perimeter from 160.00um to 173.89um.\n")
    sz = project.ppl_corrected_die(pdir, log)
    # 160 * 313 / 288 * 1.01 / 4 = 43.9 -> 44
    assert sz["die_side_um"] == 44 and sz["core_side_um"] == 40 and sz["binding"] == "pins_ppl"
    assert sz["period_ns"] == before["period_ns"] and sz["ppl"]["die_before_um"] == before["die_side_um"]
    assert "export DIE_AREA  = 0 0 44 44" in (pdir / "constraints" / "config.mk").read_text()
    assert project.ppl_corrected_die(pdir, log) is None          # already big enough: no loop
    assert project.ppl_corrected_die(pdir, "[ERROR GPL-0301] overflow") is None


def test_manifest_compiles_only_the_listed_sources(tmp_path):
    import json as _j
    src = tmp_path / "d"
    (src / "rtl").mkdir(parents=True)
    (src / "inc").mkdir()
    (src / "rtl" / "top.v").write_text('module top(input a, output y);\n`include "defs.vh"\nassign y = a;\nendmodule\n')
    (src / "inc" / "defs.vh").write_text("localparam W = 1;\n")
    (src / "rtl" / "tb.v").write_text("module tb(); top t(); endmodule\n")          # present, not listed
    (src / "design.json").write_text(_j.dumps({"top": "top", "files": ["rtl/top.v", "inc/defs.vh"],
                                               "include_dirs": ["inc"]}))
    meta = project.create(src, tmp_path / "p", "nangate45")
    assert [Path(f).name for f in meta["files"]] == ["top.v"] and meta["top"] == "top"
    assert any(f["recipe"] == "header_not_source" and f["file"] == "inc/defs.vh" for f in meta["frontend_fixes"])
    assert str(tmp_path / "p" / "rtl" / "inc") in meta["include_dirs"]
    (src / "design.json").write_text(_j.dumps({"top": "top", "files": ["rtl/gone.v"]}))
    with pytest.raises(ValueError):
        project.create(src, tmp_path / "q", "nangate45")
