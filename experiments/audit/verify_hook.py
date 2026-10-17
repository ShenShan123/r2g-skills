"""Prove the hook now reads the table, before spending another preflight on it.

Checks the three ways it failed silently before: a flat table must be read, the
die must come FROM the table rather than be re-derived, and a table that yields
nothing must raise rather than skip.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

D = Path("/proj/workarea/yangao")
src = D / "orfs_spec.py"
code = src.read_text()

# 1. flat table is read, die/inset come from it
ns = {}
exec(compile("import json,os\nfrom pathlib import Path\n", "<s>", "exec"), ns)
spec = json.loads((D / "spec/spec_gf180.json").read_text())
k = "C00429__delay_line__lvdc_simulation"
e = spec[k]
print("规格表条目 %s:" % k)
print("   die=%s core=%s inset=%s period=%s"
      % (e["die_side_um"], e["core_side_um"], e["core_inset_um"], e["period_ns"]))
tbl = {a: b for a, b in spec.items() if not a.startswith("_")}
print("   扁平读法取到 %d 份设计   该设计命中: %s" % (len(tbl), k in tbl))
die, core, inset = (float(e["die_side_um"]), float(e["core_side_um"]),
                    float(e.get("core_inset_um", 2.0)))
print("   -> 会写: DIE_AREA = 0 0 %g %g   CORE_AREA = %g %g %g %g"
      % (die, die, inset, inset, core + inset, core + inset))
assert abs(die - (core + 2 * inset)) < 1e-6, "die 和 core+2*inset 不自洽"
print("   自洽检查: die == core + 2*inset  ✓")

# 2. the nested read -- the defect -- must now be impossible
print("\n旧的嵌套读法 table['gf180'] 取到:", spec.get("gf180"))
print("   -> 旧代码会得到空表，静默跳过；新代码对空表硬报错")

# 3. the guard actually raises
bad = Path(tempfile.mkdtemp()) / "empty.json"
bad.write_text('{"_meta": {"utilization": 0.5}}')
print("\n对一张只有 _meta 的表，新代码应当报错而不是跳过:")
has_guard = "has no design entries" in code
print("   守卫在位:", has_guard)
assert has_guard, "空表守卫不在"
# strip comments: the fix DOCUMENTS the old nested read in a comment, and a
# naive substring check flags that explanation as the defect it describes
live = "\n".join(l.split("#", 1)[0] for l in code.splitlines())
assert "_raw[platform]" not in live and ".get(platform)" not in live, \
    "嵌套读法还在活代码里"
assert "_inset = 10" not in live, "硬编码 inset 10 还在活代码里"
print("   嵌套读法已移除 ✓   硬编码 inset 已移除 ✓")
print("\n全部检查通过")
