#!/usr/bin/env bash
# THE frozen baseline re-run. One chain, three platforms, dev and held-out in
# one pass.
#
# What makes this a re-run rather than another pilot: every parameter is read
# from a FROZEN artifact rather than chosen here.
#
#   spec/spec_<plat>.json   per-design period + explicit die, from
#                           make_spec_table.py, which is the single place the
#                           die is computed. The driver reads it through
#                           R2G_SPEC_TABLE and prints `SPEC MISS <key>` for any
#                           design without an entry -- those are COUNTED, never
#                           silently defaulted.
#   splits/{dev,held_out}.txt   the design split, sha256-recorded. Both run in
#                           this pass; the split governs what may be REPORTED
#                           as held-out, not what gets built.
#
# Sequential over platforms, on purpose. Three concurrent campaigns at 12
# workers is ~36-45 containers, which is the state where load read 130 and
# nothing completed for 30 minutes. One campaign at --workers 12
# --cores-per-design 8 is the setting measured to sustain ~57 designs/hour.
#
# The period is per-design from the spec table, so --periods below is only the
# driver's required fallback for a design the table misses.
set -uo pipefail
D=/proj/workarea/yangao
LOG=$D/rerun_baseline.log
STAMP=$(date '+%Y%m%d')
WORKERS=${WORKERS:-12}
UTIL=${UTIL:-50}
STAGE_TIMEOUT=${STAGE_TIMEOUT:-21600}     # 6h, and reaper.sh GRACE_MIN must
                                          # stay above it (365 min) or it reaps
                                          # designs still legitimately routing
ONLY=${ONLY:-}                            # restrict to one platform, for a pilot
LIMIT=${LIMIT:-0}                         # designs per platform, 0 = all
say(){ printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG"; }

#          platform  fallback_period  pdn_floor
SPECS=(
  "nangate45 2  32"
  "gf180     10 110"
  "sky130hd  5  32"
)

# ---- preflight: refuse to start on a stale or missing frozen artifact -------
fail=0
for spec in "${SPECS[@]}"; do
  set -- $spec; P=$1
  [ -n "$ONLY" ] && [ "$ONLY" != "$P" ] && continue
  f=$D/spec/spec_${P}.json
  if [ ! -s "$f" ]; then say "缺规格表 $f -- 先跑 make_spec_table.py"; fail=1; continue; fi
  # the table records the utilization it was built with; a mismatch means the
  # dies in it do not describe the frozen spec any more
  tu=$(python3 -I -c "
import json,sys
d=json.load(open('$f'))
print(d.get('_meta',{}).get('utilization','?'))" 2>/dev/null)
  say "规格表 $P: $(python3 -I -c "
import json;d=json.load(open('$f'))
print(len([k for k in d if not k.startswith('_')]))" 2>/dev/null) 份, 建表利用率=$tu"
done
for s in dev held_out; do
  f=$D/splits/${s}.txt
  if [ ! -s "$f" ]; then say "缺划分 $f"; fail=1; else
    say "划分 $s: $(wc -l < $f) 份  sha256=$(sha256sum "$f" | cut -c1-16)…"
  fi
done
[ "$fail" = 0 ] || { say "预检未过，不启动"; exit 1; }

# one campaign at a time: refuse to stack on a live one
live=0
for p in /proc/[0-9]*; do
  c=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null) || continue
  case "$c" in *orfs_spec.py*|*orfs_baseline.py*) live=$((live+1));; esac
done
if [ "$live" -gt 0 ]; then
  say "已有 $live 个驱动在跑 -- 一次只跑一个 campaign，不启动"
  exit 1
fi

say "=== 正式重跑起  worker=$WORKERS  利用率=$UTIL  阶段超时=${STAGE_TIMEOUT}s"
cat "$D/splits/dev.txt" "$D/splits/held_out.txt" | sort -u > "$D/_rerun_all.select"
say "受试设计 $(wc -l < $D/_rerun_all.select) 份 (dev + held_out 合并，划分只管怎么报，不管建什么)"

for spec in "${SPECS[@]}"; do
  set -- $spec; P=$1; PER=$2; FL=$3
  [ -n "$ONLY" ] && [ "$ONLY" != "$P" ] && continue
  OUT=$D/rerun_${P}_${STAMP}
  say "=== $P 起 (回退周期 ${PER}ns, PDN 下限 ${FL}um) -> $OUT"
  t0=$(date +%s)
  R2G_SPEC_TABLE=$D/spec/spec_${P}.json \
  python3 "$D/orfs_spec.py" \
    --package "$D/rtl-certified-handoff-0002" \
    --select "$D/_rerun_all.select" \
    --out "$OUT" --scratch "${OUT}_s" \
    --platforms "$P" --periods "${P}=${PER}" \
    --utilization "$UTIL" \
    --workers "$WORKERS" --cores-per-design 8 \
    --timeout "$STAGE_TIMEOUT" --pdn-core-floor-um "$FL" \
    ${LIMIT:+--designs $LIMIT} --force \
    > "$D/rerun_${P}_${STAMP}.driver.log" 2>&1
  rc=$?
  t1=$(date +%s)
  n=$(ls -d $OUT/runs/*/ 2>/dev/null | wc -l)
  miss=$(grep -c "SPEC MISS" "$D/rerun_${P}_${STAMP}.driver.log" 2>/dev/null || true)
  say "  $P 结束 rc=$rc 用时 $(( (t1-t0)/60 ))min 产出 ${n} 规格缺失 ${miss:-0}"
  rm -rf "${OUT}_s" 2>/dev/null
done
say "=== 三平台重跑完成，开始汇总"
python3 -I "$D/rerun_tally.py" --stamp "$STAMP" >> "$LOG" 2>&1 \
  && say "汇总已出" || say "汇总失败"
