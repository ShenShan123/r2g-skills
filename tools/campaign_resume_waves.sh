#!/bin/bash
# Platform-generic wave driver for the r2g-rtl2gds sign-off campaign.
#
# Generalizes tools/nangate45_resume_waves.sh to ANY ORFS platform (asap7,
# nangate45, sky130hd, gf180, ihp-sg13g2, ...) and, unlike that driver, runs
# the FULL per-wave learning sequence rather than just `engineer_loop run`:
#
#     fmax-drain  (best closing period -> SDC, same --max prefix as run)
#   -> run        (flow + fix + ingest + learn + Gate-A enqueue)
#   -> ab-drain   (judge pending A/B candidates -> promote/shadow)
#   -> check_db_integrity  (BOTH DBs tell the same story; ALARM => a bug to fix)
#
# This is the sequence /r2g-debug Step 2 documents as the ideal; the legacy
# nangate45 driver only ran `run`, so Fmax + A/B had to be driven by hand.
#
# Platform is carried PER-LEDGER-ENTRY (engineer_loop `run` reads entry["platform"],
# it has no --platform flag), so a campaign is scoped to ONE platform by pointing
# at that platform's own ledger. Build it first (a technology re-target / new round):
#     python3 tools/setup_rtl_designs.py --platform asap7 --force            # config.mk -> asap7
#     python3 tools/build_pending_ledger.py --platform asap7 \
#         --out design_cases/_batch/asap7_campaign.jsonl
#
# Knobs (env or pool.env): PLATFORM, LEDGER, WAVE_MAX, WORKERS, NUM_CORES.
# Host is SHARED (user4 finesim often pins ~80/96). Keep WORKERS*NUM_CORES <= free
# cores (skill hard rule); default to the good-neighbour 3x4 ~= 12.
#
# Each wave emits a single-line WAVE_DONE summary on stdout (Monitor-friendly),
# appends an honesty snapshot + integrity verdict to the wave log, and loops until
# the ledger has 0 pending. To stop: kill -9 the process GROUP (run_orfs.sh wraps
# stages in `setsid timeout`, so killing the driver alone orphans the ORFS tree).
set -u
cd /proj/workarea/user5/agent-r2g

PLATFORM=${PLATFORM:-sky130hd}
# Per-platform ledger so each round's history stays immutable. The original
# nangate45 round historically lives in campaign.jsonl — override LEDGER=... to
# resume it (asap7 in asap7_campaign.jsonl); new rounds use <platform>_campaign.jsonl.
LEDGER=${LEDGER:-design_cases/_batch/${PLATFORM}_campaign.jsonl}
EL=r2g-rtl2gds/scripts/loop/engineer_loop.py
KDB=r2g-rtl2gds/knowledge/knowledge.sqlite
INTEG=tools/check_db_integrity.py
WAVE_MAX=${WAVE_MAX:-24}
export NUM_CORES=${NUM_CORES:-4}
WORKERS=${WORKERS:-3}
LOGDIR=tools/_${PLATFORM}_resume_logs
mkdir -p "$LOGDIR"
PROG="$LOGDIR/waves.log"
# Live pool-tuning hook (re-sourced at the top of every wave): dropping
# `WORKERS=.. NUM_CORES=.. WAVE_MAX=..` into this file retunes the NEXT wave with
# NO restart. Keep WORKERS*NUM_CORES <= free cores. PLATFORM/LEDGER are NOT
# re-sourced mid-campaign — a ledger swap would conflate two technology rounds.
POOL_ENV="$LOGDIR/pool.env"

if [ ! -f "$LEDGER" ]; then
  echo "ERROR: ledger $LEDGER not found. Build it first:" >&2
  echo "  python3 tools/setup_rtl_designs.py --platform $PLATFORM --force" >&2
  echo "  python3 tools/build_pending_ledger.py --platform $PLATFORM --out $LEDGER" >&2
  exit 2
fi

pending_count() {
  python3 - "$LEDGER" <<'PY'
import json,sys
e={}
for ln in open(sys.argv[1]):
    ln=ln.strip()
    if not ln: continue
    r=json.loads(ln); e.setdefault(r["design"],{}).update(r)
print(sum(1 for v in e.values() if v["state"]=="pending"))
PY
}

honesty() {
  # Global counts + per-platform promotions (the 2026-06-24 "arms identical" alarm
  # hides in flat per-platform promo, not in ab_trials).
  sqlite3 "$KDB" "
    SELECT 'fail='||(SELECT COUNT(*) FROM runs WHERE orfs_status='fail')
       ||' fe='||(SELECT COUNT(DISTINCT run_id) FROM failure_events WHERE signature LIKE 'orfs-fail-%')
       ||' partial='||(SELECT COUNT(*) FROM runs WHERE orfs_status='partial')
       ||' ab_trials='||(SELECT COUNT(*) FROM ab_trials)
       ||' fix_ev='||(SELECT COUNT(*) FROM fix_events)
       ||' cand='||(SELECT COUNT(*) FROM recipe_status WHERE status='candidate')
       ||' promo='||(SELECT COUNT(*) FROM recipe_status WHERE status='promoted')
       ||' promo_${PLATFORM}='||(SELECT COUNT(*) FROM recipe_status WHERE status='promoted' AND platform='${PLATFORM}');"
}

wave=0
while true; do
  # shellcheck disable=SC1090
  [ -f "$POOL_ENV" ] && { source "$POOL_ENV"; export NUM_CORES; }
  p=$(pending_count)
  if [ "$p" -le 0 ]; then echo "ALL_DONE platform=$PLATFORM pending=0"; echo "$(date -u +%FT%TZ) ALL_DONE platform=$PLATFORM" >>"$PROG"; break; fi
  wave=$((wave+1))
  echo "$(date -u +%FT%TZ) WAVE_START platform=$PLATFORM wave=$wave pending=$p max=$WAVE_MAX workers=$WORKERS num_cores=$NUM_CORES" >>"$PROG"
  ts=$(date +%Y%m%d_%H%M%S)
  wlog="$LOGDIR/wave_${wave}_${ts}.log"
  {
    # 1) Best Fmax -> SDC for the SAME first-N-pending prefix `run --max N` picks,
    #    so characterization and sign-off interleave per wave (not all front-loaded).
    python3 "$EL" fmax-drain --ledger "$LEDGER" --platform "$PLATFORM" \
        --max "$WAVE_MAX" --workers "$WORKERS"
    # 2) Flow + fix + ingest + learn + Gate-A candidate enqueue.
    python3 "$EL" run        --ledger "$LEDGER" --max "$WAVE_MAX" --workers "$WORKERS"
    # 3) Judge pending A/B candidates -> promote / shadow.
    python3 "$EL" ab-drain   --ledger "$LEDGER" --workers "$WORKERS"
  } >"$wlog" 2>&1
  rc=$?
  # 4) BOTH-DBs integrity after the wave's moves. Non-zero = a HARD invariant
  #    tripped (the loop is lying/blind) -> the next bug to fix.
  integ=PASS
  python3 "$INTEG" --platform "$PLATFORM" >>"$wlog" 2>&1 || integ=ALARM
  h=$(honesty)
  echo "$(date -u +%FT%TZ) HONESTY $h" >>"$wlog"
  before=$p; after=$(pending_count); did=$((before-after))
  line="WAVE_DONE platform=$PLATFORM wave=$wave rc=$rc integrity=$integ processed~=$did pending_now=$after | $h"
  echo "$line"
  echo "$(date -u +%FT%TZ) $line" >>"$PROG"
done
