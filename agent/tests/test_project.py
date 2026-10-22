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
    assert project.infer_top(project.find_hdl(tmp_path)) == "top"
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
