#!/usr/bin/env bash
# Pass-3 retry: 4 BOOM variants that hit per-stage timeouts in pass 2 of the
# sweep (after the main retry batch was already in flight).
#
#   * boom_megaboom    — synth timed out @4h on ABC step ~17 (BTBBranchPredictorBank)
#   * boom_megaproboom — synth timed out @4h on OPT step ~15
#   * boom_smallboomnol2 — place timed out @4h with overflow descending well
#   * boom_smallproboom  — floorplan stuck at setRC.tcl pre-SKIP_REPAIR_TIE_FANOUT
#
# All configs now carry REMOVE_ABC_BUFFERS=1 + SKIP_REPAIR_TIE_FANOUT=1
# (skip the slow repair_timing_helper and tie-fanout passes inside floorplan)
# plus GPL_TIMING_DRIVEN=0 / GPL_ROUTABILITY_DRIVEN=0 for place. Bumped
# per-stage timeout to 12h to cover BOOM-mega synth ABC budgets.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/r2g-rtl2gds/scripts/flow/_env.sh" >/dev/null 2>&1

: "${ORFS_TIMEOUT:=43200}"
: "${ORFS_MAX_CPUS:=8}"
: "${PLACE_FAST:=1}"
export ORFS_TIMEOUT ORFS_MAX_CPUS PLACE_FAST

RETRY=(boom_megaboom boom_megaproboom boom_smallboomnol2 boom_smallproboom)

infer_stage() {
  local v="$1"
  local rd="$FLOW_DIR/results/nangate45/ChipTop/$v"
  if [[ -f "$rd/3_place.odb" ]]; then
    echo "cts"
  elif [[ -f "$rd/3_3_place_gp.odb" ]]; then
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
  local logf="$log_dir/retry_pass3_$(date +%Y%m%d_%H%M%S).log"
  local tag=""
  [[ -n "$stage" ]] && tag="FROM_STAGE=$stage"
  echo "[$(date '+%H:%M:%S')] PASS3 $v $tag (timeout=${ORFS_TIMEOUT}s, cpus=$ORFS_MAX_CPUS)" \
    | tee -a "$log_dir/sweep_history.log"
  (
    if [[ -n "$stage" ]]; then export FROM_STAGE="$stage"; fi
    bash "$ROOT/r2g-rtl2gds/scripts/flow/run_orfs.sh" "$case_dir" nangate45
  ) > "$logf" 2>&1
  local rc=$?
  echo "[$(date '+%H:%M:%S')] PASS3 $v rc=$rc" | tee -a "$log_dir/sweep_history.log"
  return "$rc"
}

declare -A PIDS
for v in "${RETRY[@]}"; do
  run_one "$v" &
  PIDS[$!]="$v"
done
echo "Dispatched ${#PIDS[@]} pass-3 jobs."

for pid in "${!PIDS[@]}"; do
  wait "$pid" 2>/dev/null
done
echo "All pass-3 retries complete."
