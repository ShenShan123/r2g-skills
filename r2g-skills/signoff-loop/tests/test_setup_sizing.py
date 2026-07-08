"""Project setup must size the die so it FITS the synthesized cells.

Regression for the 2026-06-23 root cause: generate_config_mk sized tiny/small
designs with a FIXED DIE_AREA (50x50 / 120x120) chosen from RTL LINE COUNT. Line
count is a terrible proxy for gate count -- a compact-but-dense design (wide
multiplier, FFT butterfly, DMA datapath) synthesizes to thousands of cells that
don't fit a hardcoded 50x50 die -> [ERROR FLW-0024] place density > 1.0. The fix:
use CORE_UTILIZATION (auto-size) for every non-pin-heavy bucket so ORFS sizes the
die to the real cell area.
"""
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[3] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import setup_rtl_designs as srd


def _mk_rtl(tmp_path, lines):
    tmp_path.mkdir(parents=True, exist_ok=True)
    rtl = tmp_path / "demo.v"
    body = "\n".join(f"  wire w{i} = a ^ {i & 1};" for i in range(lines))
    rtl.write_text(f"module demo(input a, output b);\n{body}\n  assign b = a;\nendmodule\n")
    return rtl


def _gen(tmp_path, rtl):
    proj = tmp_path / "proj"
    (proj / "constraints").mkdir(parents=True)
    cfg_path, complexity, cat = srd.generate_config_mk(
        proj, "demo", "nangate45", [rtl], None)
    return Path(cfg_path).read_text(), cat


def test_tiny_design_autosizes_not_fixed_die(tmp_path):
    # <100 lines, few pins: previously got a HARDCODED DIE_AREA 50x50 from line count.
    cfg, cat = _gen(tmp_path, _mk_rtl(tmp_path, 20))
    assert cat == "tiny"
    assert "CORE_UTILIZATION" in cfg
    assert "DIE_AREA" not in cfg and "CORE_AREA" not in cfg


def test_small_design_autosizes_not_fixed_die(tmp_path):
    cfg, cat = _gen(tmp_path, _mk_rtl(tmp_path, 250))
    assert cat == "small"
    assert "CORE_UTILIZATION" in cfg
    assert "DIE_AREA" not in cfg


def test_no_bucket_emits_a_fixed_die_area(tmp_path):
    # the whole point: NO size bucket should ever hardcode a die (the FLW-0024 trap)
    for n in (20, 250, 1500):
        cfg, _ = _gen(tmp_path / f"n{n}", _mk_rtl(tmp_path / f"n{n}", n))
        assert "DIE_AREA" not in cfg, f"{n}-line design got a fixed DIE_AREA"
        assert "CORE_UTILIZATION" in cfg


def _gen_platform(tmp_path, rtl, platform):
    proj = tmp_path / "proj"
    (proj / "constraints").mkdir(parents=True)
    cfg_path, _complexity, _cat = srd.generate_config_mk(
        proj, "demo", platform, [rtl], None)
    return Path(cfg_path).read_text()


def test_sky130_wires_feedthrough_hook(tmp_path):
    # sky130 uses Netgen LVS: ORFS global_place `remove_buffers` merges `assign out = in`
    # port-feedthrough nets onto ONE net -> SPICE can't express it -> Netgen "Top level
    # cell failed pin matching" (top_pin_mismatch). buffer_port_feedthroughs.tcl (wired as
    # POST_GLOBAL_PLACE_TCL) splits them. mk_sky130_project.py wires it; the
    # setup_rtl_designs.py re-point path MUST too or a whole re-pointed round's feedthrough
    # designs top_pin_mismatch with no hook (2026-07-01 parity gap: picorv32_mem_adapter,
    # sirv_gnrl_icb_arbt). Mirrors the known PDN-floor parity gap. See failure-patterns.md.
    cfg = _gen_platform(tmp_path, _mk_rtl(tmp_path, 20), "sky130hd")
    assert "POST_GLOBAL_PLACE_TCL" in cfg
    assert "buffer_port_feedthroughs.tcl" in cfg


def test_sky130hs_wires_feedthrough_hook(tmp_path):
    cfg = _gen_platform(tmp_path, _mk_rtl(tmp_path, 20), "sky130hs")
    assert "buffer_port_feedthroughs.tcl" in cfg


def test_nangate45_omits_feedthrough_hook(tmp_path):
    # KLayout-LVS platforms (nangate45/asap7/gf180/ihp) don't need the sky130-specific
    # Netgen feedthrough hook -- wiring it there would be dead config noise.
    cfg = _gen_platform(tmp_path, _mk_rtl(tmp_path, 20), "nangate45")
    assert "POST_GLOBAL_PLACE_TCL" not in cfg
