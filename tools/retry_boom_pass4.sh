#!/usr/bin/env bash
# Pass-4 retry: any BOOM variant whose newest stage_log.jsonl ends with
# status=124 (timeout) and has not been resumed since.
#
# All configs now carry the full set of safety flags applied across passes:
#   * GPL_TIMING_DRIVEN=0 + GPL_ROUTABILITY_DRIVEN=0 (fast place_gp)
#   * REMOVE_ABC_BUFFERS=1 (skip repair_timing_helper inside floorplan)
#   * SKIP_REPAIR_TIE_FANOUT=1 (skip slow tie-fanout buffering)
#   * ROUTING_LAYER_ADJUSTMENT=0.10 (smallseboom only — congestion slack)
#
# Per-stage timeout bumped to 12h; for synth-bound mega designs, can be
# overridden externally.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/skills/r2g-rtl2gds/scripts/flow/_env.sh" >/dev/null 2>&1

: "${ORFS_TIMEOUT:=43200}"
: "${ORFS_MAX_CPUS:=8}"
: "${PLACE_FAST:=1}"
export ORFS_TIMEOUT ORFS_MAX_CPUS PLACE_FAST

# Discover designs whose newest stage_log ends in status=124 and skip ones
# that have an active openroad/yosys process for the same FLOW_VARIANT.
RETRY=()
for d in "$ROOT"/design_cases/boom_*; do
  v=$(basename "$d")
  rd=$(ls -dt "$d"/backend/RUN_2026-05-* 2>/dev/null | head -1)
  [[ -z "$rd" ]] && continue
  sl="$rd/stage_log.jsonl"
  [[ -f "$sl" && -s "$sl" ]] || continue
  last_status=$(tail -1 "$sl" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("status",-1))' 2>/dev/null)
  [[ "$last_status" != "124" ]] && continue
  # Skip if already running
  if pgrep -af "FLOW_VARIANT=$v|/$v/" 2>/dev/null | grep -q "openroad -exit\|yosys"; then
    echo "skip: $v already has an active process"
    continue
  fi
  RETRY+=("$v")
done

if (( ${#RETRY[@]} == 0 )); then
  echo "Nothing to retry."
  exit 0
fi
echo "Retry plan: ${RETRY[*]}"

infer_stage() {
  local v="$1"
  local rd="$FLOW_DIR/results/nangate45/ChipTop/$v"
  if [[ -f "$rd/3_place.odb" ]]; then echo "cts"
  elif [[ -f "$rd/3_3_place_gp.odb" ]]; then echo "place"
  elif [[ -f "$rd/2_floorplan.odb" ]]; then echo "place"
  elif [[ -f "$rd/1_synth.odb" ]]; then echo "floorplan"
  else echo ""
  fi
}

run_one() {
  local v="$1"
  local stage; stage="$(infer_stage "$v")"
  local case_dir="$ROOT/design_cases/$v"
  local log_dir="$case_dir/batch_logs"
  mkdir -p "$log_dir"
  local logf="$log_dir/retry_pass4_$(date +%Y%m%d_%H%M%S).log"
  local tag=""
  [[ -n "$stage" ]] && tag="FROM_STAGE=$stage"
  echo "[$(date '+%H:%M:%S')] PASS4 $v $tag (timeout=${ORFS_TIMEOUT}s, cpus=$ORFS_MAX_CPUS)" \
    | tee -a "$log_dir/sweep_history.log"
  (
    if [[ -n "$stage" ]]; then export FROM_STAGE="$stage"; fi
    bash "$ROOT/skills/r2g-rtl2gds/scripts/flow/run_orfs.sh" "$case_dir" nangate45
  ) > "$logf" 2>&1
  local rc=$?
  echo "[$(date '+%H:%M:%S')] PASS4 $v rc=$rc" | tee -a "$log_dir/sweep_history.log"
  return "$rc"
}

declare -A PIDS
for v in "${RETRY[@]}"; do
  run_one "$v" &
  PIDS[$!]="$v"
done
echo "Dispatched ${#PIDS[@]} pass-4 jobs."

for pid in "${!PIDS[@]}"; do
  wait "$pid" 2>/dev/null
done
echo "All pass-4 retries complete."
