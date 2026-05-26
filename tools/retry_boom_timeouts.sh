#!/usr/bin/env bash
# Restart the 6 BOOM variants that hit per-stage timeouts in the first pass.
# All configs now carry REMOVE_ABC_BUFFERS=1 (skips the slow timing repair
# inside floorplan.tcl) and the existing GPL_TIMING_DRIVEN=0/ROUTABILITY=0
# flags (skips the gpl-internal timing-driven repair loop).
#
# Per-stage timeout bumped to 28800s (8h) — large BOOM variants legitimately
# need this; their old place stage took 3h57m on smallseboom.
#
# Each variant resumes from the last successful stage by reading existing
# ORFS *.odb artifacts.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/r2g-rtl2gds/scripts/flow/_env.sh" >/dev/null 2>&1

: "${ORFS_TIMEOUT:=28800}"
: "${ORFS_MAX_CPUS:=8}"
: "${PLACE_FAST:=1}"
export ORFS_TIMEOUT ORFS_MAX_CPUS PLACE_FAST

# Designs that timed out in the first pass
RETRY=(boom_largeboom boom_largeproboom boom_largeseboom boom_mediumproboom \
       boom_mediumboom boom_mediumseboom)

# Infer FROM_STAGE from existing ORFS artifacts
infer_stage() {
  local v="$1"
  local rd="$FLOW_DIR/results/nangate45/ChipTop/$v"
  if [[ -f "$rd/3_place.odb" ]]; then
    echo "cts"
  elif [[ -f "$rd/3_3_place_gp.odb" ]]; then
    # place_gp done, resume place stage from 3_4 (make dependency tracking
    # will skip already-done sub-targets). Run as place stage from start.
    echo "place"
  elif [[ -f "$rd/2_floorplan.odb" ]]; then
    echo "place"
  elif [[ -f "$rd/1_synth.odb" ]]; then
    echo "floorplan"
  else
    echo ""
  fi
}

run_one() {
  local v="$1"
  local stage
  stage="$(infer_stage "$v")"
  local case_dir="$ROOT/design_cases/$v"
  local log_dir="$case_dir/batch_logs"
  mkdir -p "$log_dir"
  local logf="$log_dir/retry_$(date +%Y%m%d_%H%M%S).log"
  local tag=""
  [[ -n "$stage" ]] && tag="FROM_STAGE=$stage"
  echo "[$(date '+%H:%M:%S')] RETRY $v $tag (timeout=${ORFS_TIMEOUT}s, cpus=$ORFS_MAX_CPUS)" \
    | tee -a "$log_dir/sweep_history.log"
  (
    if [[ -n "$stage" ]]; then export FROM_STAGE="$stage"; fi
    bash "$ROOT/r2g-rtl2gds/scripts/flow/run_orfs.sh" "$case_dir" nangate45
  ) > "$logf" 2>&1
  local rc=$?
  echo "[$(date '+%H:%M:%S')] RETRY $v rc=$rc" | tee -a "$log_dir/sweep_history.log"
  return "$rc"
}

# Launch all 6 in parallel — they have distinct FLOW_VARIANTs so no collisions
declare -A PIDS
for v in "${RETRY[@]}"; do
  run_one "$v" &
  PIDS[$!]="$v"
done
echo "Dispatched ${#PIDS[@]} retry jobs."

for pid in "${!PIDS[@]}"; do
  wait "$pid" 2>/dev/null
done
echo "All retries complete."
