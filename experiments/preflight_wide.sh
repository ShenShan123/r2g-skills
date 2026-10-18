#!/usr/bin/env bash
# The WIDE utilization preflight: 30 designs per platform, not 10.
#
# Two jobs, and the second is why it must run before the campaign:
#
#   1. CONFIRM the measured area allowance works. The first preflight found
#      gf180 landing at 60.0% against a frozen 50% target because the die was
#      sized from the pilot's cell area, which does not include the
#      timing-repair buffers a tighter period provokes. The first-order
#      allowance (1.031 / 1.184 / 1.092, from 5/7/5 binding designs) was applied
#      here so achieved utilization could be re-measured against it.
#
#      OUTCOME, recorded so these numbers are not mistaken for current: this
#      preflight REPLACED them with 1.021 / 1.144 / 1.190 over 15/18/18 binding
#      designs. sky130hd's was wrong by 9% -- at 1.092 it landed at 53.7%
#      median against a 50% target. The live values are in
#      spec/area_allowance.json and make_spec_table.py; the pair above is
#      history.
#   2. TIGHTEN the allowance itself. It was derived from 5, 7 and 5 binding
#      designs -- a first-order correction, not a calibration. Thirty per
#      platform makes it one.
#
# Deliberately NOT chained behind the current jobs: one campaign at a time is a
# measured rule, so this waits for the machine to be free and says so, rather
# than stacking onto four running measurements.
set -uo pipefail
D=/proj/workarea/yangao
LOG=$D/preflight_wide.log
WORKERS=${WORKERS:-8}
PER_PLAT=${PER_PLAT:-30}
say(){ printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG"; }

# refuse to stack
live=0
for p in /proc/[0-9]*; do
  c=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null) || continue
  case "$c" in *orfs_spec.py*|*orfs_baseline.py*) live=$((live+1));; esac
done
if [ "$live" -gt 0 ] && [ "${FORCE:-0}" != 1 ]; then
  say "已有 $live 个驱动在跑 -- 不叠加。等它们结束后再起，或 FORCE=1 覆盖"
  exit 1
fi

say "=== 宽预检起  每平台 $PER_PLAT 份  worker=$WORKERS"
# 10 + 10 + 7 + 3 = 30 per platform. The largest bin gets fewest: those designs
# dominate the wall time and inform the same allowance the smaller bins do.
R2G_PREFLIGHT_PER_BIN=10,10,7,3 \
  python3 -I "$D/pick_preflight.py" >> "$LOG" 2>&1 \
  || { say "选题生成失败"; exit 1; }

SPECS=("nangate45 2 32" "gf180 10 110" "sky130hd 5 32")
for spec in "${SPECS[@]}"; do
  set -- $spec; P=$1; PER=$2; FL=$3
  SEL=$D/_preflight_${P}.select
  OUT=$D/_preflight/${P}
  [ -s "$SEL" ] || { say "$P 缺选题，跳过"; continue; }
  rm -rf "$OUT" "${OUT}_s"; mkdir -p "$OUT"
  say "--- $P 起 ($(wc -l < $SEL) 份)"
  t0=$(date +%s)
  R2G_SPEC_TABLE=$D/spec/spec_${P}.json \
  python3 "$D/orfs_spec.py" \
    --package "$D/rtl-certified-handoff-0002" --select "$SEL" \
    --out "$OUT" --scratch "${OUT}_s" \
    --platforms "$P" --periods "${P}=${PER}" --utilization 50 \
    --workers "$WORKERS" --cores-per-design 8 \
    --timeout 21600 --pdn-core-floor-um "$FL" --force \
    > "$D/preflight_wide_${P}.driver.log" 2>&1
  say "--- $P 结束 用时 $(( ($(date +%s)-t0)/60 ))min 产出 $(ls -d $OUT/runs/*/ 2>/dev/null | wc -l)"
  python3 -I "$D/conformance_check.py" --root "_preflight/$P" --platform "$P" >> "$LOG" 2>&1
  rc=$?
  [ "$rc" = 0 ] || { say "!! $P 与冻结文档不一致 (rc=$rc) -- 停止"; exit 1; }
  say "    $P 一致性检查通过"
  rm -rf "${OUT}_s" 2>/dev/null
done
say "=== 量实际利用率"
python3 -I "$D/preflight_tally.py" >> "$LOG" 2>&1
say "=== 重新导出面积余量（这次 30 份/平台）"
python3 -I "$D/derive_area_allowance.py" >> "$LOG" 2>&1
say "全部完成 -- 看 $LOG 的判决段：实际利用率中位应落在 50% 附近"
