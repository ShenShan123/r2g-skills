from pathlib import Path
import re

p = Path("/proj/workarea/yangao/orfs_spec.py")
t = p.read_text()

# ---- 1. new SDC: per-design period + uncertainty + I/O delay ---------------
old_sdc_anchor = "set_clock_uncertainty 0.0 [get_clocks *]"
assert t.count(old_sdc_anchor) == 1, "SDC 锚点不唯一"
new_sdc = """set_clock_uncertainty [expr {{{period}}} * 0.05] [get_clocks *]
# I/O delay exists for COVERAGE, not pressure: without it only reg-to-reg paths
# are constrained and 27-28% of pilot designs had NO constrained path at all --
# they report `worst slack max INF` while wns reads 0.00, which looks like a
# pass. 15% keeps I/O from becoming the binding constraint on designs that
# already had internal paths.
set _r2g_io [expr {{{period}}} * 0.15]
if {{[llength [all_inputs]] > 0}} {{
  set_input_delay  $_r2g_io -clock [lindex [get_clocks *] 0] [all_inputs]
}}
if {{[llength [all_outputs]] > 0}} {{
  set_output_delay $_r2g_io -clock [lindex [get_clocks *] 0] [all_outputs]
}}"""
t = t.replace(old_sdc_anchor, new_sdc)

# ---- 2. spec-table hook in write_config -----------------------------------
anchor = "    # Arbitrary extra make assignments, newline-separated."
spec_hook = '''    # Frozen per-design spec (experiments/BASELINE_PARAMETERS.md). The table is
    # generated once by make_spec_table.py and never recomputed, so a repair
    # cannot move its own goalpost: the die is pinned from the BASELINE cell
    # area, and the size class that chose the period is pinned too.
    #
    # Replaces CORE_UTILIZATION entirely -- an explicit DIE_AREA is the whole
    # point. `override export` is NOT needed for DIE_AREA/CORE_AREA on these
    # platforms (checked with platform_assigned_keys: the platform configs do
    # not assign them), but IS needed for TECH_LEF/SC_LEF, which is why the
    # stock-LEF path uses it.
    _spec_file = os.environ.get("R2G_SPEC_TABLE", "")
    if _spec_file:
        if not Path(_spec_file).is_file():
            raise SystemExit(f"R2G_SPEC_TABLE not found: {_spec_file}")
        _spec = json.loads(Path(_spec_file).read_text())
        _key = design_dir.name
        _s = _spec.get(_key)
        if _s is None:
            # No pilot size -> no spec. Falling back silently would hand the
            # design a different period from its peers with nothing recording
            # it, so say so and use the platform's smallest bin + the PDN floor.
            print(f"  SPEC MISS {_key}: no entry, using smallest bin + PDN floor")
        else:
            _die, _core, _in = (_s["die_side_um"], _s["core_side_um"],
                                _s["core_inset_um"])
            cfg = [c for c in cfg
                   if not c.startswith("export CORE_UTILIZATION")
                   and not c.startswith("export DIE_AREA")
                   and not c.startswith("export CORE_AREA")]
            cfg.append(f"export DIE_AREA  = 0 0 {_die:g} {_die:g}")
            cfg.append(f"export CORE_AREA = {_in:g} {_in:g} "
                       f"{_core + _in:g} {_core + _in:g}")

'''
assert t.count(anchor) == 1, "extra-mk 锚点不唯一"
t = t.replace(anchor, spec_hook + anchor, 1)

# ---- 3. per-design period feeds the SDC ------------------------------------
old_sdc_write = '(work / "constraint.sdc").write_text(SDC.format(ports=CLOCK_PORTS, period=period))'
assert t.count(old_sdc_write) == 1, "SDC 写入锚点不唯一"
new_sdc_write = '''_eff_period = period
    _sf = os.environ.get("R2G_SPEC_TABLE", "")
    if _sf and Path(_sf).is_file():
        _tbl = json.loads(Path(_sf).read_text())
        _e = _tbl.get(design_dir.name)
        if _e:
            _eff_period = str(_e["period_ns"])
    (work / "constraint.sdc").write_text(
        SDC.format(ports=CLOCK_PORTS, period=_eff_period))'''
t = t.replace(old_sdc_write, new_sdc_write)

p.write_text(t)
print("orfs_spec.py 已打补丁: SDC 三处改动 + 规格表钩子")
