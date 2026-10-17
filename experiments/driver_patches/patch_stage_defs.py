import pathlib
p = pathlib.Path("/proj/workarea/yangao/orfs_spec.py")
s = p.read_text()

old = """    for name in ("config.mk", "constraint.sdc", "flow.log"):
        if (work / name).is_file():
            shutil.copy2(work / name, dest / name)"""
new = '''    for name in ("config.mk", "constraint.sdc", "flow.log"):
        if (work / name).is_file():
            shutil.copy2(work / name, dest / name)
    _write_stage_defs(work, dest)'''
assert s.count(old) == 1, "拷贝块锚点不唯一"
s = s.replace(old, new)

# insert the helper before the harvest function that uses it
anchor = "def _run_one_once(design_dir: Path, meta: dict, platform: str, period: str,"
helper = '''def _write_stage_defs(work: Path, dest: Path) -> None:
    """Stream a DEF out of each per-stage ODB, then let the ODB be discarded.

    def-graph's four-stage causal dataset (E3) needs the floorplan, placement
    and CTS views, and ORFS writes NO per-stage .def at all -- it works in ODB
    and streams a DEF only on demand. So these cannot be copied later; they have
    to be generated here, before the scratch directory goes away.

    Converting now rather than keeping the ODBs is the safer of the two, and not
    mainly for disk. An ODB is a version-bound OpenROAD database; keeping it
    defers the conversion to whatever OpenROAD exists then. Doing it here uses
    the exact binary that WROTE the database, which is the only moment the
    conversion is guaranteed to mean the same thing. A DEF is text and
    tool-independent.

    Cost measured on this corpus: 5-28 s per DEF, so about 8.7 h added to a
    104 h campaign -- against re-running the entire flow if the views turn out
    to be needed and were not kept. Keeping the three ODBs instead would cost
    4.5 TB, which the volume has, but would carry the version risk above.

    Failure here is NEVER fatal to the run: a design whose DEF cannot be written
    still has its flow result. The absence is visible (no file) rather than
    silent, which is the property that matters.
    """
    import subprocess as _sp
    oroad = os.environ.get("OPENROAD_EXE") or "openroad"
    for stage in ("2_floorplan", "3_place", "4_cts"):
        odb = None
        for cand in work.rglob("%s.odb" % stage):
            odb = cand
            break
        if odb is None:
            continue
        out = dest / ("%s.def" % stage)
        tcl = ("read_db {%s}\\nwrite_def {%s}\\nexit\\n" % (odb, out))
        try:
            _sp.run([oroad, "-no_init", "-exit", "-"], input=tcl, text=True,
                    capture_output=True, timeout=600)
        except Exception:                                        # noqa: BLE001
            pass
        if not out.is_file():
            print("  STAGE-DEF MISS %s (%s)" % (stage, dest.name), flush=True)


'''
assert s.count(anchor) == 1, "函数锚点不唯一"
s = s.replace(anchor, helper + anchor)

# 1_2_yosys.v: two downstream products read it and 1_synth.v is not a substitute
old2 = '''                    "6_final.gds", "1_synth.v",'''
new2 = '''                    "6_final.gds", "1_synth.v",
                    # 1_2_yosys.v, not just 1_synth.v: the four-stage dataset
                    # takes its canonical topology from 1_2_yosys.v (so a
                    # resizer-deleted gate stays a node) and rtl-acquire's
                    # netlist_graph.pt is built from it too. Two downstream
                    # products, neither of which 1_synth.v substitutes for.
                    "1_2_yosys.v",'''
assert s.count(old2) == 1, "yosys 锚点不唯一"
s = s.replace(old2, new2)
p.write_text(s)
print("orfs_spec.py: 阶段 DEF 生成 + 1_2_yosys.v 保留 已加入")
