#!/usr/bin/env bash
# pilot500 tight-clock re-run on 208 (native ORFS, same OpenROAD/Yosys/flow
# scripts/platform as the pilot's image -- fingerprints matched 2026-10-22).
#
# Per design: the pilot's own config.mk (paths re-pointed at the local mirror of
# its RTL), a NEW constraint.sdc (period = k x reference critical-path delay,
# port I/O delays 15 %, setup uncertainty 5 %; periods.json), the FULL flow, then
# write_def of the floorplan, placement, CTS and route databases. No sign-off.
#
#   WORKERS=12 bash run_tight.sh list.txt
set -uo pipefail
P=/home/yangao/p500_tight
ORFS=/home/yangao/r2g_toolchain/OpenROAD-flow-scripts
OR=$ORFS/tools/install/OpenROAD/bin/openroad
WORKERS=${WORKERS:-12}
LIST=$1
LOG=$P/run.log
mkdir -p $P/work $P/out
say(){ printf '%s %s\n' "$(date '+%F %T')" "$*" >> "$LOG"; }

one() {
  local n=$1 w=$P/work/$1 o=$P/out/$1
  [ -f "$o/DONE" ] && return 0
  rm -rf "$w" "$o"; mkdir -p "$w" "$o"
  local per
  per=$(python3 -c "
import json,sys
for r in json.load(open('$P/periods.json')):
    if r['design']=='$n': print(r['period_ns'] if r['period_ns'] else 2.0)")
  sed "s#/proj/workarea/yangao/#$P/mirror/proj/workarea/yangao/#g; s#/work/constraint.sdc#$w/constraint.sdc#" \
      "$P/cfg/$n/config.mk" > "$w/config.mk"
  sed "s/PER_NS/$per/" "$P/sdc_template.sdc" > "$w/constraint.sdc"
  local top; top=$(sed -n 's/^export DESIGN_NAME *= *//p' "$w/config.mk")
  local t0=$SECONDS
  ( cd $ORFS/flow && NUM_CORES=8 OMP_NUM_THREADS=8 timeout --kill-after=120 14400 \
      make DESIGN_CONFIG=$w/config.mk WORK_HOME=$w > $w/flow.log 2>&1 )
  local rc=$?
  local r=$w/results/nangate45/$top/base
  if [ $rc -ne 0 ] || [ ! -f $r/6_final.odb ]; then
    say "FAIL rc=$rc $((SECONDS-t0))s $n $(grep -oE '\[ERROR [A-Z]+-[0-9]+\][^"]{0,80}' $w/flow.log | tail -1)"
    cp $w/flow.log $o/flow.log 2>/dev/null; echo "rc=$rc" > $o/FAILED
    rm -rf "$w"; return 0
  fi
  local s ok=1
  for s in 2_floorplan 3_place 4_cts 5_route; do
    printf 'read_db %s\nwrite_def %s\n' "$r/$s.odb" "$w/$s.def" > $w/export_$s.tcl
    $OR -no_init -exit $w/export_$s.tcl >> $w/export.log 2>&1 && [ -s $w/$s.def ] || ok=0
  done
  if [ $ok -ne 1 ]; then
    say "EXPORT_FAIL $n"; cp $w/export.log $o/; echo export > $o/FAILED; rm -rf "$w"; return 0
  fi
  for s in 2_floorplan 3_place 4_cts 5_route; do gzip -c $w/$s.def > $o/$s.def.gz; done
  cp -r $w/reports $o/reports; cp -r $w/logs $o/logs
  cp $w/config.mk $w/constraint.sdc $o/
  python3 - "$P" "$n" "$o" "$per" <<'PY'
import glob, json, sys
P, n, o, per = sys.argv[1:5]
pil = json.load(open(f"{P}/cfg/{n}/result.json"))
ref = next(r for r in json.load(open(f"{P}/periods.json")) if r["design"] == n)
rep = json.load(open(glob.glob(f"{o}/logs/nangate45/*/base/6_report.json")[0]))
ws = rep.get("finish__timing__setup__ws"); tns = rep.get("finish__timing__setup__tns")
constrained = ws is not None and abs(ws) < 1e30
res = {"design": pil.get("design"), "platform": "nangate45", "status": "pass",
       "period_ns": float(per), "clock_k": ref["k"], "ref_delay_ns": ref["ref_delay_ns"],
       "timing_path_constrained": constrained,
       "wns_ns": ws if constrained else None, "tns_ns": tns if constrained else None,
       "violated": bool(constrained and ws < 0),
       "cells": rep.get("finish__design__instance__count__stdcell"),
       "utilization": rep.get("finish__design__instance__utilization"),
       "signoff": "not run (DRC/LVS not checked for this re-run)",
       "pilot_period_ns": float(pil.get("period", 2))}
json.dump(res, open(f"{o}/result.json", "w"), indent=1)
PY
  rm -rf "$w"
  touch $o/DONE
  say "ok $((SECONDS-t0))s $n per=$per"
}
export -f one say
export P ORFS OR LOG
say "=== start list=$LIST workers=$WORKERS ($(wc -l < $LIST))"
xargs -a "$LIST" -P "$WORKERS" -I{} bash -c 'one "$@"' _ {}
say "=== end"
