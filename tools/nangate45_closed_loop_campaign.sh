#!/bin/bash
# Closed-loop nangate45 campaign relaunch — 2026-06-24, AFTER the loop-closure fix
# (timing+place A/B now diverge & promote; judge integrity; Fmax wiring).
#
# Phase 1  Fmax pre-pass: proxy-search the best closing period for each pending design
#          and stamp its SDC (engineer_loop fmax-drain). Bounded cost (no --verify).
# Phase 2  Bounded WAVES of `engineer_loop run` (flow@Fmax -> DRC/LVS fix -> ingest ->
#          learn -> Gate-A -> A/B drain -> judge -> promote/demote), one honesty
#          snapshot per wave, until the ledger has 0 pending.
#
# Host is now FREE (~96 cores; finesim cleared). Good-neighbour sizing via pool.env,
# re-sourced every wave (drop WORKERS=.. NUM_CORES=.. WAVE_MAX=.. there to retune the
# NEXT wave with no restart). Keep WORKERS*NUM_CORES <= host cores (skill hard rule).
set -u
cd /proj/workarea/user5/agent-r2g

LEDGER=design_cases/_batch/campaign.jsonl
EL=r2g-rtl2gds/scripts/loop/engineer_loop.py
KDB=r2g-rtl2gds/knowledge/knowledge.sqlite
LOGDIR=tools/_closed_loop_logs
mkdir -p "$LOGDIR"
PROG="$LOGDIR/waves.log"
POOL_ENV="$LOGDIR/pool.env"

export NUM_CORES=${NUM_CORES:-4}
WORKERS=${WORKERS:-24}
WAVE_MAX=${WAVE_MAX:-20}
FMAX_WORKERS=${FMAX_WORKERS:-16}
RUN_FMAX=${RUN_FMAX:-1}

pending_count() {
  python3 - "$LEDGER" <<'PY'
import json,sys
e={}
for ln in open(sys.argv[1]):
    ln=ln.strip()
    if not ln: continue
    r=json.loads(ln); e.setdefault(r["design"],{}).update(r)
print(sum(1 for v in e.values() if v["state"]=="pending" and "_abA_" not in v["design"] and "_abB_" not in v["design"]))
PY
}

honesty() {
  sqlite3 "$KDB" "
    SELECT 'fail='||(SELECT COUNT(*) FROM runs WHERE orfs_status='fail')
       ||' fe='||(SELECT COUNT(DISTINCT run_id) FROM failure_events WHERE signature LIKE 'orfs-fail-%')
       ||' partial='||(SELECT COUNT(*) FROM runs WHERE orfs_status='partial')
       ||' ab_trials='||(SELECT COUNT(*) FROM ab_trials)
       ||' fix_ev='||(SELECT COUNT(*) FROM fix_events)
       ||' cand='||(SELECT COUNT(*) FROM recipe_status WHERE status='candidate')
       ||' promo='||(SELECT COUNT(*) FROM recipe_status WHERE status='promoted')
       ||' promo_ng='||(SELECT COUNT(*) FROM recipe_status WHERE status='promoted' AND platform='nangate45');"
}

# ── Interleaved waves: per wave, Fmax-search the next WAVE_MAX pending designs (stamp
#    their SDCs) THEN run() that same prefix (flow@Fmax -> DRC/LVS -> A/B -> promote).
#    Fmax-drain on the SAME prefix run() consumes => each design is characterized just
#    before it flows; fast loop-closure signal instead of an hours-long global pre-pass. ──
wave=0
while true; do
  # shellcheck disable=SC1090
  [ -f "$POOL_ENV" ] && { source "$POOL_ENV"; export NUM_CORES; }
  p=$(pending_count)
  if [ "$p" -le 0 ]; then echo "ALL_DONE pending=0"; echo "$(date -u +%FT%TZ) ALL_DONE" >>"$PROG"; break; fi
  wave=$((wave+1))
  ts=$(date +%Y%m%d_%H%M%S)
  echo "$(date -u +%FT%TZ) WAVE_START wave=$wave pending=$p max=$WAVE_MAX workers=$WORKERS fmax_workers=$FMAX_WORKERS num_cores=$NUM_CORES" >>"$PROG"
  if [ "$RUN_FMAX" = "1" ]; then
    python3 "$EL" fmax-drain --ledger "$LEDGER" --platform nangate45 \
        --max "$WAVE_MAX" --workers "$FMAX_WORKERS" \
        >"$LOGDIR/fmax_${wave}_${ts}.log" 2>&1
    echo "$(date -u +%FT%TZ) FMAX_DONE wave=$wave rc=$? -> $(tail -1 "$LOGDIR/fmax_${wave}_${ts}.log")" >>"$PROG"
  fi
  python3 "$EL" run --ledger "$LEDGER" --max "$WAVE_MAX" --workers "$WORKERS" \
      >"$LOGDIR/wave_${wave}_${ts}.log" 2>&1
  rc=$?
  h=$(honesty)
  before=$p; after=$(pending_count); did=$((before-after))
  line="WAVE_DONE wave=$wave rc=$rc processed~=$did pending_now=$after | $h"
  echo "$line"
  echo "$(date -u +%FT%TZ) $line" >>"$PROG"
done
