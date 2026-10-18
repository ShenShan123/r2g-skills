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
# PER-DESIGN, not per-stage, despite the name kept for compatibility with the
# callers that already set it. orfs_spec.py runs ONE container per design that
# executes the whole `make` (all stages) and arms a single deadline over it, so
# 21600 s is the budget for floorplan+place+cts+route+finish TOGETHER. A design
# needing three stages of three hours each is killed at six hours with no single
# stage having timed out. The reaper's GRACE_MIN=365 min is 360+5 and only
# sweeps containers the runner failed to clean up -- it cannot false-kill a live
# design, because the runner always kills first.
STAGE_TIMEOUT=${STAGE_TIMEOUT:-21600}
SIGNOFF_WORKERS=${SIGNOFF_WORKERS:-8}     # not 12: a >100K-cell LVS takes
                                          # 3-5 GB, and CLAUDE.md forbids
                                          # running several of those at once
SIGNOFF_TIMEOUT=${SIGNOFF_TIMEOUT:-7200}     # 6h, and reaper.sh GRACE_MIN must
                                          # stay above it (365 min) or it reaps
                                          # designs still legitimately routing
ONLY=${ONLY:-}                            # restrict to one platform, for a pilot
# --force is OFF by default so the campaign is RESUMABLE. The driver skips a
# design that already has a result.json, so a run killed at design 800 of 1249
# picks up at 801 instead of redoing a day's work. Pass FRESH=1 only to
# deliberately rebuild from scratch.
#
# The driver's skip is not naive: a record that hit PDN-0185 without a core
# floor is the first half of a retry whose second half never ran, and it counts
# as unfinished rather than done.
FRESH=${FRESH:-}
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

say "=== 正式重跑起  worker=$WORKERS  利用率=$UTIL  每设计总超时=${STAGE_TIMEOUT}s"

# Freeze the digests of everything that decides what gets built, and re-check
# them at the end. Not hypothetical: another session edited orfs_spec.py twice
# in one day, once making the spec-table hook a silent no-op, and I regenerated
# the spec table WHILE a measurement was running against it. The launch gate
# checks these at launch; nothing checked they held for the next four days.
DIGESTS=$D/rerun_${STAMP}.digests
: > "$DIGESTS"
for f in "$D/orfs_spec.py" "$D/signoff45.py" "$D/signoff_pass.py" \
         "$D/lvs130.py" "$D/rerun_tally.py" \
         "$D/spec/spec_nangate45.json" "$D/spec/spec_gf180.json" \
         "$D/spec/spec_sky130hd.json" "$D/spec/area_allowance.json" \
         "$D/splits/dev.txt" "$D/_rerun_all.select"; do
  [ -f "$f" ] && sha256sum "$f" >> "$DIGESTS"
done
say "已冻结 $(wc -l < "$DIGESTS") 份输入的 sha256 -> $(basename "$DIGESTS")"
# dev ONLY. Held-out is deliberately not built yet: it is where repairs
# developed on dev get validated, and building its baseline now buys nothing
# while costing half the machine time (three days instead of six). The
# held-out baseline has to be run before any held-out claim, just not before
# there is anything to claim.
SET=${SET:-dev}
cp "$D/splits/${SET}.txt" "$D/_rerun_all.select"
say "受试设计 $(wc -l < $D/_rerun_all.select) 份 (只跑 ${SET}；held-out 等有东西要验证时再跑)"
say "  对照基准 = 试跑的 dev 双清数: nangate45 837 / gf180 754 / sky130hd 1032"

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
    ${LIMIT:+--designs $LIMIT} ${FRESH:+--force} \
    > "$D/rerun_${P}_${STAMP}.driver.log" 2>&1
  rc=$?
  t1=$(date +%s)
  n=$(ls -d $OUT/runs/*/ 2>/dev/null | wc -l)
  miss=$(grep -c "SPEC MISS" "$D/rerun_${P}_${STAMP}.driver.log" 2>/dev/null || true)
  say "  $P flow 结束 rc=$rc 用时 $(( (t1-t0)/60 ))min 产出 ${n} 规格缺失 ${miss:-0}"
  rm -rf "${OUT}_s" 2>/dev/null

  # ---- SIGNOFF. Without this the campaign produces no verdicts at all. -------
  #
  # The flow writes result.json with status/stage/cells; drc_status and
  # lvs_status are written by the SIGNOFF pass, and rerun_tally.py counts those.
  # An earlier version of this chain went flow -> tally and would have graded
  # 1249 x 3 designs as "no verdict, therefore fail" after 4.3 days.
  #
  # The scripts differ by platform because the LVS TOOL does:
  #   nangate45         signoff45.py      DRC + LVS together, LVS via klayout
  #                                       and r2g's FreePDK45.lylvs. netgen has
  #                                       no setup for this platform.
  #   gf180, sky130hd   signoff_pass.py   DRC only -- it says so: "LVS is NOT
  #                                       attempted"
  #                   + lvs130.py         LVS via r2g's run_netgen_lvs.sh
  #
  # Both of the latter write their verdict back into result.json, which is what
  # the tally reads. Their own json output is a per-pass snapshot and must not
  # be read as the corpus: the pilot's netgen_lvs.json holds 1475 rows against
  # 2497 designs (the pre-filter drops designs with no layout) and reading it as
  # the LVS truth undercounted sky130hd by 669.
  t2=$(date +%s)
  if [ "$P" = nangate45 ]; then
    python3 -I "$D/signoff45.py" \
        --runs "$OUT/runs" --projroot "$D/rerun_${P}_proj" \
        --out "$OUT/signoff.json" --workers "$SIGNOFF_WORKERS" \
        --timeout "$SIGNOFF_TIMEOUT" \
        > "$D/rerun_${P}_${STAMP}.signoff.log" 2>&1
    say "  $P 签核(DRC+LVS) rc=$? 用时 $(( ($(date +%s)-t2)/60 ))min"
  else
    python3 -I "$D/signoff_pass.py" \
        --runs "$OUT/runs" --projroot "$D/rerun_${P}_drcproj" \
        --out "$OUT/signoff.json" --platform "$P" \
        --workers "$SIGNOFF_WORKERS" --timeout "$SIGNOFF_TIMEOUT" \
        > "$D/rerun_${P}_${STAMP}.drc.log" 2>&1
    say "  $P DRC rc=$? 用时 $(( ($(date +%s)-t2)/60 ))min"
    # gf180 only: a SECOND DRC pass with the PDK's own run_drc.py, on the
    # designs the fast deck could not grade. The fast deck times out where
    # per-table execution finishes -- it graded nothing on C03506 (18 290 cells)
    # where the official runner returned 10 violations in 3891 s. Running the
    # official one on everything costs 75 h against 19 h for the hybrid, and the
    # two were measured to agree in both directions on ARTIFACTS: same rule
    # names and per-rule counts on three dirty designs, nothing found on 20 the
    # fast deck called clean.
    #
    # It never downgrades -- only fills a verdict that is absent -- and it skips
    # designs with no GDS, which no checker can grade. Each design records which
    # checker produced its verdict, so the two populations stay separable.
    if [ "$P" = gf180 ] && [ "${HYBRID_DRC:-1}" = 1 ]; then
      t4=$(date +%s)
      python3 -I "$D/gf180_hybrid_drc.py" \
          --runs "$OUT/runs" --out "$OUT/official_drc.json" \
          --work "$D/rerun_${P}_offdrc" \
          --workers "$SIGNOFF_WORKERS" --mp 4 --timeout "$STAGE_TIMEOUT" \
          > "$D/rerun_${P}_${STAMP}.officialdrc.log" 2>&1
      say "  $P 官方逐表补判 rc=$? 用时 $(( ($(date +%s)-t4)/60 ))min"
    fi

    t3=$(date +%s)
    python3 -I "$D/lvs130.py" \
        --runs "$OUT/runs" --projroot "$D/rerun_${P}_lvsproj" \
        --out "$OUT/netgen_lvs.json" --platform "$P" \
        --workers "$SIGNOFF_WORKERS" --timeout "$SIGNOFF_TIMEOUT" \
        > "$D/rerun_${P}_${STAMP}.lvs.log" 2>&1
    say "  $P LVS rc=$? 用时 $(( ($(date +%s)-t3)/60 ))min"
  fi

  # A campaign with no verdicts is the failure this section exists to prevent,
  # so check rather than assume: count result.json rows carrying drc_status.
  graded=$(python3 -I - <<PY
import glob, json
n = 0
for f in glob.glob("$OUT/runs/*/result.json"):
    try:
        if json.load(open(f)).get("drc_status"):
            n += 1
    except Exception:
        pass
print(n)
PY
)
  say "  $P 带 DRC 判决的设计: ${graded} / ${n}"
  if [ "${graded:-0}" = 0 ]; then
    say "!! $P 签核没有产生任何判决 -- 停止，后面的平台不要再跑"
    exit 1
  fi
done
say "=== 三平台重跑完成，先核对输入没被改过"
if sha256sum -c --quiet "$DIGESTS" 2>/dev/null; then
  say "  输入与起跑时一致 ✓"
else
  say "!! 有输入在跑的过程中被修改 -- 结果的来历不完整，逐项列出:"
  sha256sum -c "$DIGESTS" 2>&1 | grep -v ': OK$' | sed 's/^/     /' | tee -a "$LOG"
  say "   （不阻塞汇总，但任何结论都必须带上这一条）"
fi
say "=== 开始汇总"
python3 -I "$D/rerun_tally.py" --stamp "$STAMP" >> "$LOG" 2>&1 \
  && say "汇总已出" || say "汇总失败"
