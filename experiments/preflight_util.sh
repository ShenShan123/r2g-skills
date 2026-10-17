#!/usr/bin/env bash
# Utilization preflight for the frozen baseline. 30 designs, 10 per platform,
# spread over the four size classes, all of them designs the PILOT already got
# dual-clean.
#
# The question it answers, and the only reason to spend the hour: the die is
# sized from the pilot's SYNTHESIS area, but the final layout is bigger --
# measured on one design, a 3x tighter period costs +10.4% real cell area
# (10437.6 -> 9453.11 um2 in the same core), all of it buffers the resizer
# inserted chasing timing. The frozen SDC is tighter than the pilot's, so the
# frozen 50% target could come out at 55% achieved, and a batch of designs could
# fail to fit on a run that already cost three days.
#
# The authoritative number is ORFS's own:
#     finish__design__instance__area / finish__design__core__area
# instance__area EXCLUDES fill (verified: instance + fill_cell = core area to
# within 0.1 um2), so it is the real cell area, not the padded one.
#
# The pass-in-pilot filter is the control: a design that already failed for an
# unrelated reason cannot answer this question.
set -uo pipefail
D=/proj/workarea/yangao
LOG=$D/preflight_util.log
WORKERS=${WORKERS:-4}
say(){ printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG"; }

#          platform  fallback_period  pdn_floor
SPECS=("nangate45 2 32" "gf180 10 110" "sky130hd 5 32")

say "=== 利用率预检起  30 份 (三平台各 10)  worker=$WORKERS  目标利用率 50%"
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
    --platforms "$P" --periods "${P}=${PER}" \
    --utilization 50 \
    --workers "$WORKERS" --cores-per-design 8 \
    --timeout 21600 --pdn-core-floor-um "$FL" --force \
    > "$D/preflight_${P}.driver.log" 2>&1
  say "--- $P 结束 用时 $(( ($(date +%s)-t0)/60 ))min 产出 $(ls -d $OUT/runs/*/ 2>/dev/null | wc -l)"
  rm -rf "${OUT}_s" 2>/dev/null
done
say "=== 预检跑完，开始量实际利用率"
python3 -I "$D/preflight_tally.py" >> "$LOG" 2>&1 && say "量完" || say "量失败"
