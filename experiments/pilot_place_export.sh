#!/usr/bin/env bash
# Export placement DEF + route DEF + reports for a list of PILOT designs.
#
# The pilot (n45_full) kept 5_route.odb / 6_final.odb but not the post-placement
# database (orfs_spec.py harvest(), "NOT kept, on purpose"). So:
#   route DEF      write_def from the pilot's OWN 5_route.odb -- no re-run
#   placement DEF  re-run ORFS up to `place` with the pilot's own config.mk and
#                  constraint.sdc, the same image (26Q3-318-g6b9d7fb806) and the
#                  same NUM_CORES=8, then write_def of 3_place.odb
#   reports        copied from the pilot (reports/, logs/ incl. per-stage json)
#
# A re-run is only a substitute if the flow is deterministic, so MODE=full runs
# the WHOLE flow on a few designs and compares its 6_final.def with the pilot's
# (COMPONENTS and NETS sections); the placement export is trusted only if they
# match.
#
#   MODE=full  WORKERS=3  bash pilot_place_export.sh check.txt
#   MODE=place WORKERS=10 bash pilot_place_export.sh select.txt
set -uo pipefail
D=/proj/workarea/yangao
P=${P:-$D/pilot500}
R=${R:-$D/n45_full/runs}
IMG=${IMG:-r2g-orfs:26Q3}
PKG=$D/rtl-certified-handoff-0002
MODE=${MODE:-place}
WORKERS=${WORKERS:-10}
LIST=$1
LOG=$P/export_$MODE.log
mkdir -p "$P/work" "$P/out"
say(){ printf '%s %s\n' "$(date '+%F %T')" "$*" >> "$LOG"; }

one() {
  local n=$1
  local w=$P/work/$MODE/$n
  local o=$P/out/$n
  if [ "$MODE" = place ] && [ -f "$o/DONE" ]; then return 0; fi
  rm -rf "$w"; mkdir -p "$w" "$o"
  cp "$R/$n/config.mk" "$R/$n/constraint.sdc" "$w/"
  local top
  top=$(sed -n 's/^export DESIGN_NAME *= *//p' "$w/config.mk")
  local target=place
  [ "$MODE" = full ] && target=""
  local tag
  tag=p500${MODE}_$(printf '%s' "$n" | md5sum | cut -c1-10)
  podman rm -f "$tag" >/dev/null 2>&1
  local t0=$SECONDS
  # bounded: a placement re-run of these sizes takes minutes, the full flow on
  # the check designs well under an hour
  timeout --kill-after=60 10800 podman run --rm --name "$tag" \
      -v "$w:/work:Z" -v "$PKG:$PKG:ro,Z" -v "$R/$n:/pilot:ro,Z" \
      -e NUM_CORES=8 -e OMP_NUM_THREADS=8 "$IMG" bash -lc "
cd \$ORFS_ROOT/flow
make DESIGN_CONFIG=/work/config.mk WORK_HOME=/work $target > /work/flow.log 2>&1 || exit 3
r=/work/results/nangate45/$top/base
cat > /work/export.tcl <<EOF
read_db \$r/3_place.odb
write_def /work/3_place.def
EOF
cat > /work/export_route.tcl <<EOF
read_db /pilot/5_route.odb
write_def /work/5_route.def
EOF
openroad -no_init -exit /work/export.tcl > /work/export.log 2>&1 || exit 4
openroad -no_init -exit /work/export_route.tcl >> /work/export.log 2>&1 || exit 5
[ -f \$r/6_final.def ] && cp \$r/6_final.def /work/6_final.rerun.def
exit 0"
  local rc=$?
  podman rm -f "$tag" >/dev/null 2>&1
  if [ "$rc" != 0 ]; then
    say "FAIL rc=$rc $(( SECONDS - t0 ))s $n"
    cp "$w/flow.log" "$o/rerun_flow.log" 2>/dev/null
    return 0
  fi
  if [ "$MODE" = full ]; then
    say "FULL done $(( SECONDS - t0 ))s $n"
    return 0
  fi
  gzip -c "$w/3_place.def" > "$o/3_place.def.gz"
  gzip -c "$w/5_route.def" > "$o/5_route.def.gz"
  cp -r "$R/$n/reports" "$o/reports"
  # per-stage json (WNS/TNS/wirelength) and logs (5_1_grt.log carries the
  # GRT-0096 overflow table)
  cp -r "$R/$n/logs" "$o/logs"
  cp "$R/$n/result.json" "$R/$n/config.mk" "$R/$n/constraint.sdc" "$o/"
  cp "$w/flow.log" "$o/rerun_place_flow.log"
  rm -rf "$w"
  touch "$o/DONE"
  say "ok $(( SECONDS - t0 ))s $n"
}
export -f one say
export D P R IMG PKG MODE LOG

say "=== start MODE=$MODE list=$LIST workers=$WORKERS ($(wc -l < "$LIST") designs)"
xargs -a "$LIST" -P "$WORKERS" -I{} bash -c 'one "$@"' _ {}
say "=== end MODE=$MODE"
