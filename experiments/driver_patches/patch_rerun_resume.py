import pathlib
p = pathlib.Path("/proj/workarea/yangao/rerun_baseline.sh")
s = p.read_text()

# 1. --force makes a crash cost the whole platform again
old = """    --timeout "$STAGE_TIMEOUT" --pdn-core-floor-um "$FL" \\
    ${LIMIT:+--designs $LIMIT} --force \\"""
new = """    --timeout "$STAGE_TIMEOUT" --pdn-core-floor-um "$FL" \\
    ${LIMIT:+--designs $LIMIT} ${FRESH:+--force} \\"""
assert s.count(old) == 1, "force 锚点不唯一"
s = s.replace(old, new)

old2 = """ONLY=${ONLY:-}                            # restrict to one platform, for a pilot"""
new2 = """ONLY=${ONLY:-}                            # restrict to one platform, for a pilot
# --force is OFF by default so the campaign is RESUMABLE. The driver skips a
# design that already has a result.json, so a run killed at design 800 of 1249
# picks up at 801 instead of redoing a day's work. Pass FRESH=1 only to
# deliberately rebuild from scratch.
#
# The driver's skip is not naive: a record that hit PDN-0185 without a core
# floor is the first half of a retry whose second half never ran, and it counts
# as unfinished rather than done.
FRESH=${FRESH:-}"""
assert s.count(old2) == 1, "ONLY 锚点不唯一"
s = s.replace(old2, new2)

# 2. record the inputs' digests at launch and re-verify at the end
old3 = """say "=== 正式重跑起  worker=$WORKERS  利用率=$UTIL  阶段超时=${STAGE_TIMEOUT}s\""""
new3 = """say "=== 正式重跑起  worker=$WORKERS  利用率=$UTIL  阶段超时=${STAGE_TIMEOUT}s"

# Freeze the digests of everything that decides what gets built, and re-check
# them at the end. Not hypothetical: another session edited orfs_spec.py twice
# in one day, once making the spec-table hook a silent no-op, and I regenerated
# the spec table WHILE a measurement was running against it. The launch gate
# checks these at launch; nothing checked they held for the next four days.
DIGESTS=$D/rerun_${STAMP}.digests
: > "$DIGESTS"
for f in "$D/orfs_spec.py" "$D/signoff45.py" "$D/signoff_pass.py" \\
         "$D/lvs130.py" "$D/rerun_tally.py" \\
         "$D/spec/spec_nangate45.json" "$D/spec/spec_gf180.json" \\
         "$D/spec/spec_sky130hd.json" "$D/spec/area_allowance.json" \\
         "$D/splits/dev.txt" "$D/_rerun_all.select"; do
  [ -f "$f" ] && sha256sum "$f" >> "$DIGESTS"
done
say "已冻结 $(wc -l < "$DIGESTS") 份输入的 sha256 -> $(basename "$DIGESTS")\""""
assert s.count(old3) == 1, "起跑行锚点不唯一"
s = s.replace(old3, new3)

old4 = """say "=== 三平台重跑完成，开始汇总\""""
new4 = """say "=== 三平台重跑完成，先核对输入没被改过"
if sha256sum -c --quiet "$DIGESTS" 2>/dev/null; then
  say "  输入与起跑时一致 ✓"
else
  say "!! 有输入在跑的过程中被修改 -- 结果的来历不完整，逐项列出:"
  sha256sum -c "$DIGESTS" 2>&1 | grep -v ': OK$' | sed 's/^/     /' | tee -a "$LOG"
  say "   （不阻塞汇总，但任何结论都必须带上这一条）"
fi
say "=== 开始汇总\""""
assert s.count(old4) == 1, "汇总行锚点不唯一"
p.write_text(s.replace(old4, new4))
print("rerun_baseline.sh: resumable by default + input digests frozen and re-checked")
