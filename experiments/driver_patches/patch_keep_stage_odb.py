import pathlib
import re

p = pathlib.Path("/proj/workarea/yangao/orfs_spec.py")
s = p.read_text()

# 1. drop the DEF-generation call and the helper -- measurement reversed it
s = s.replace("    _write_stage_defs(work, dest)\n", "", 1)
m = re.search(r"def _write_stage_defs\(work: Path, dest: Path\) -> None:.*?\n\n\n",
              s, re.S)
assert m, "找不到 _write_stage_defs 整体"
s = s[:m.start()] + s[m.end():]
assert "_write_stage_defs" not in s, "还有残留"

# 2. keep the three stage ODBs instead
old = '''                    "5_route.odb", "6_final.odb",'''
new = '''                    "5_route.odb", "6_final.odb",
                    # The per-stage views def-graph's four-stage causal dataset
                    # (E3) needs. ORFS writes no per-stage .def at all -- it
                    # works in ODB and streams a DEF on demand -- so these
                    # cannot be copied later from anything.
                    #
                    # Keeping the ODB rather than converting now, decided AGAINST
                    # my first instinct by measurement: write_def on a 197 MB
                    # 3_place.odb takes 20.9 s and produces a 311.7 MB DEF. So
                    # converting eagerly costs MORE disk (7.0 TB against 4.5 TB
                    # over 7491 designs) AND 11 h of campaign time. The one
                    # argument for converting -- an ODB is a version-bound
                    # OpenROAD database, a DEF is tool-independent text -- is
                    # answered more cheaply by provenance: the launch gate
                    # records the container image id, so the conversion can
                    # always be re-run in the image that wrote the database.
                    "2_floorplan.odb", "3_place.odb", "4_cts.odb",'''
assert s.count(old) == 1, "ODB 锚点不唯一"
s = s.replace(old, new)
p.write_text(s)
print("orfs_spec.py: 改为保留三个阶段 ODB，撤回 DEF 生成")
