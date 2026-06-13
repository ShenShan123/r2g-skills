"""Tests for tools/mk_sky130_project.py die-sizing fallback.

A sky130hd project materialized from an older nangate45 source whose ppa.json
predates `cell_count` must NOT be floored into the tiny 200um PDN die when the
design is actually large — that aborts detailed placement at ~100% utilization
(DPL-0036, iccad2015_unit14_in1). The fix reads a logic-cell count straight from
the source DEF (excluding fillers/taps) so the floorplan policy picks the
utilization-based branch for large designs while still flooring genuinely small
ones. mk_sky130_project lives in tools/, so add it to the path explicitly.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import mk_sky130_project as mk  # noqa: E402


def _write_def(run_dir: Path, logic: int, fillers: int, taps: int) -> None:
    """A minimal 6_final.def with a COMPONENTS section of logic + physical cells."""
    run_dir.mkdir(parents=True, exist_ok=True)
    total = logic + fillers + taps
    lines = [f"COMPONENTS {total} ;"]
    for i in range(logic):
        lines.append(f"- u_logic{i} AND2_X1 + PLACED ( 0 0 ) N ;")
    for i in range(fillers):
        lines.append(f"- u_fill{i} FILLCELL_X1 + PLACED ( 0 0 ) N ;")
    for i in range(taps):
        lines.append(f"- u_tap{i} TAPCELL_X1 + PLACED ( 0 0 ) N ;")
    lines.append("END COMPONENTS")
    (run_dir / "6_final.def").write_text("\n".join(lines) + "\n")


def test_source_def_components_excludes_fillers_and_taps(tmp_path):
    src = tmp_path / "design"
    _write_def(src / "backend" / "RUN_1" / "results", logic=3106, fillers=6589, taps=291)
    assert mk.source_def_components(src) == 3106


def test_source_def_components_zero_when_no_def(tmp_path):
    src = tmp_path / "design"
    (src / "backend").mkdir(parents=True)
    assert mk.source_def_components(src) == 0


def _make_source(tmp_path, *, logic: int, with_cell_count) -> Path:
    """A source project: config.mk + sdc + a DEF, ppa.json cell_count optional."""
    src = tmp_path / "design"
    (src / "constraints").mkdir(parents=True)
    (src / "reports").mkdir(parents=True)
    rtl = src / "rtl" / "in.v"
    rtl.parent.mkdir(parents=True)
    rtl.write_text("module test(); endmodule\n")
    (src / "constraints" / "config.mk").write_text(
        f"export DESIGN_NAME = test\n"
        f"export PLATFORM = nangate45\n"
        f"export VERILOG_FILES = {rtl}\n"
        f"export CORE_UTILIZATION = 20\n"
    )
    (src / "constraints" / "constraint.sdc").write_text("create_clock -period 10 [get_ports clk]\n")
    _write_def(src / "backend" / "RUN_1" / "results", logic=logic, fillers=logic * 2, taps=logic // 10)
    import json
    ppa = {"cell_count": logic} if with_cell_count else {"area_um2": 1.0}
    (src / "reports" / "ppa.json").write_text(json.dumps(ppa))
    return src


def test_large_design_missing_cell_count_uses_utilization(tmp_path):
    """ppa.json without cell_count + a large DEF -> CORE_UTILIZATION branch
    (NOT the 200um DIE_AREA floor). The regression that caused DPL-0036."""
    src = _make_source(tmp_path, logic=3106, with_cell_count=False)
    dest = tmp_path / "dest__sky130hd"
    rc = mk_main(src, dest)
    assert rc == 0
    cfg = (dest / "constraints" / "config.mk").read_text()
    assert "CORE_UTILIZATION" in cfg
    assert "DIE_AREA" not in cfg


def test_geometry_instance_count_is_read(tmp_path):
    """The instance count lives at geometry.instance_count (not top-level
    cell_count). A large geometry count with NO source DEF must still pick the
    utilization branch — proving the geometry read, not the DEF fallback."""
    src = tmp_path / "design"
    (src / "constraints").mkdir(parents=True)
    (src / "reports").mkdir(parents=True)
    rtl = src / "rtl" / "in.v"
    rtl.parent.mkdir(parents=True)
    rtl.write_text("module test(); endmodule\n")
    (src / "constraints" / "config.mk").write_text(
        f"export DESIGN_NAME = test\nexport PLATFORM = nangate45\n"
        f"export VERILOG_FILES = {rtl}\nexport CORE_UTILIZATION = 20\n")
    (src / "constraints" / "constraint.sdc").write_text("create_clock -period 10 [get_ports clk]\n")
    import json
    (src / "reports" / "ppa.json").write_text(
        json.dumps({"geometry": {"instance_count": 3397}}))
    # No backend/DEF at all -> DEF fallback would yield 0 (floor).
    dest = tmp_path / "dest__sky130hd"
    assert mk_main(src, dest) == 0
    cfg = (dest / "constraints" / "config.mk").read_text()
    assert "CORE_UTILIZATION" in cfg and "DIE_AREA" not in cfg


def test_small_design_still_floored(tmp_path):
    """A genuinely tiny design keeps the protective 200um PDN floor."""
    src = _make_source(tmp_path, logic=40, with_cell_count=False)
    dest = tmp_path / "dest__sky130hd"
    rc = mk_main(src, dest)
    assert rc == 0
    cfg = (dest / "constraints" / "config.mk").read_text()
    assert "DIE_AREA  = 0 0 200 200" in cfg


def mk_main(src: Path, dest: Path) -> int:
    """Invoke mk_sky130_project.main with argv set to (src, dest)."""
    argv = sys.argv
    try:
        sys.argv = ["mk_sky130_project.py", str(src), str(dest)]
        return mk.main()
    finally:
        sys.argv = argv
