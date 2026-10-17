from pathlib import Path
p = Path("/proj/workarea/yangao/orfs_spec.py")
t = p.read_text()

# Compute the derived constraints in PYTHON and emit literals. `expr {2} * 0.05`
# is legal Tcl (expr concatenates its args) but it is a brace-escaping hazard
# inside a .format() template and reads wrong; a literal cannot be misparsed.
old = """set_clock_uncertainty [expr {{{period}}} * 0.05] [get_clocks *]"""
new = """set_clock_uncertainty {unc} [get_clocks *]"""
assert t.count(old) == 1
t = t.replace(old, new)

old2 = """set _r2g_io [expr {{{period}}} * 0.15]"""
new2 = """set _r2g_io {iodly}"""
assert t.count(old2) == 1
t = t.replace(old2, new2)

old3 = """    (work / "constraint.sdc").write_text(
        SDC.format(ports=CLOCK_PORTS, period=_eff_period))"""
new3 = """    _p = float(_eff_period)
    (work / "constraint.sdc").write_text(SDC.format(
        ports=CLOCK_PORTS, period=_eff_period,
        unc=f"{_p * 0.05:.4f}", iodly=f"{_p * 0.15:.4f}"))"""
assert t.count(old3) == 1
t = t.replace(old3, new3)
p.write_text(t)
print("SDC 改为 Python 预算 + 字面值")
