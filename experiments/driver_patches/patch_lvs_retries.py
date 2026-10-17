import pathlib
import re
p = pathlib.Path("/proj/workarea/yangao/r2g/signoff-loop/scripts/flow/run_lvs.sh")
s = p.read_text()
old = """if [[ -z "${LVS_CRASH_RETRIES:-}" ]]; then
  if [[ "$_CELL_COUNT" -gt 150000 ]] 2>/dev/null; then LVS_CRASH_RETRIES=1; else LVS_CRASH_RETRIES=4; fi
fi"""
new = """# Raised 4 -> 10 (2026-10-17). Measured on the pilot: 11 nangate45 dev designs
# exhausted all 4 retries and were recorded lvs_failed with no verdict, at a
# median of 7657 cells -- small designs, so not the scale limit that explains the
# other 72. Since a surviving run yields the TRUE verdict (above), each extra
# retry is a chance at a real answer and the only cost is time on designs that
# would otherwise be counted as failures for a tool reason.
#
# The >150000 gate is kept at 1 because there each retry re-extracts, which is
# the expensive part; the raise applies where retries are cheap. _CELL_COUNT
# reads 0 on 1081 of 1084 designs (the count extraction is broken, documented in
# BASELINE_PARAMETERS.md), so in practice almost everything takes the 10 branch
# -- which is the intended effect, but it is an accident of that defect rather
# than a working size test, and fixing the count would quietly re-narrow this.
if [[ -z "${LVS_CRASH_RETRIES:-}" ]]; then
  if [[ "$_CELL_COUNT" -gt 150000 ]] 2>/dev/null; then LVS_CRASH_RETRIES=1; else LVS_CRASH_RETRIES=10; fi
fi"""
assert s.count(old) == 1, "锚点不唯一"
p.write_text(s.replace(old, new))
print("run_lvs.sh: LVS_CRASH_RETRIES 4 -> 10")
