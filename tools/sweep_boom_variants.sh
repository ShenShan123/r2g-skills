#!/usr/bin/env bash
# Parallel BOOM ORFS sweep. Each variant has DESIGN_NAME=ChipTop but distinct
# FLOW_VARIANT (the project basename), so run_orfs.sh isolates their ORFS work
# directories. Safe to run concurrently.
#
# Usage:
#   tools/sweep_boom_variants.sh [max_parallel]
#
# Env knobs (all optional):
#   ORFS_TIMEOUT     per-stage timeout (default 14400)
#   ORFS_MAX_CPUS    per-job CPU budget (default 8)
#   PLACE_FAST       inject GPL_*_DRIVEN=0 (default 1; configs already
#                    set these export-vars too as a belt-and-suspenders)

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAX_PARALLEL="${1:-6}"
: "${ORFS_TIMEOUT:=14400}"
: "${ORFS_MAX_CPUS:=8}"
: "${PLACE_FAST:=1}"
export ORFS_TIMEOUT ORFS_MAX_CPUS PLACE_FAST

source "$ROOT/r2g-rtl2gds/scripts/flow/_env.sh" >/dev/null 2>&1

# Discover BOOM variants and infer resume stage
declare -a JOBS=()
for d in "$ROOT"/design_cases/boom_*; do
  name=$(basename "$d")
  # smallseboom is already running (from prior PLACE_FAST resume) — skip if so
  if [[ "$name" == "boom_smallseboom" ]]; then
    if pgrep -af "boom_smallseboom" >/dev/null; then
      echo "SKIP $name (already running)"
      continue
    fi
  fi
  rd="$FLOW_DIR/results/nangate45/ChipTop/$name"
  stage=""
  if [[ -f "$rd/2_floorplan.odb" ]]; then
    if [[ -f "$rd/3_place.odb" ]]; then
      stage="cts"
    else
      stage="place"
    fi
  elif [[ -f "$rd/1_synth.odb" ]]; then
    stage="floorplan"
  fi
  JOBS+=("$name|$stage")
done

echo "Plan:"
for j in "${JOBS[@]}"; do
  printf "  %s\n" "$j"
done
echo "Concurrency: $MAX_PARALLEL  Per-stage timeout: ${ORFS_TIMEOUT}s  CPUs/job: $ORFS_MAX_CPUS"
echo ""

run_one() {
  local name="$1"
  local stage="$2"
  local case_dir="$ROOT/design_cases/$name"
  local log_dir="$case_dir/batch_logs"
  mkdir -p "$log_dir"
  local logf="$log_dir/sweep_$(date +%Y%m%d_%H%M%S).log"
  local from_arg=""
  [[ -n "$stage" ]] && from_arg="FROM_STAGE=$stage"
  echo "[$(date '+%H:%M:%S')] START $name $from_arg" | tee -a "$log_dir/sweep_history.log"
  (
    if [[ -n "$stage" ]]; then export FROM_STAGE="$stage"; fi
    bash "$ROOT/r2g-rtl2gds/scripts/flow/run_orfs.sh" "$case_dir" nangate45
  ) > "$logf" 2>&1
  local rc=$?
  echo "[$(date '+%H:%M:%S')] DONE  $name rc=$rc" | tee -a "$log_dir/sweep_history.log"
  return "$rc"
}

# Simple worker pool
running=0
declare -A PIDS
for j in "${JOBS[@]}"; do
  name="${j%%|*}"
  stage="${j##*|}"
  while [[ $running -ge $MAX_PARALLEL ]]; do
    sleep 5
    for pid in "${!PIDS[@]}"; do
      if ! kill -0 "$pid" 2>/dev/null; then
        wait "$pid" 2>/dev/null
        unset 'PIDS[$pid]'
        running=$((running - 1))
      fi
    done
  done
  run_one "$name" "$stage" &
  PIDS[$!]="$name"
  running=$((running + 1))
done

# Wait for remaining
for pid in "${!PIDS[@]}"; do
  wait "$pid" 2>/dev/null
done
echo ""
echo "Sweep complete. See design_cases/boom_*/batch_logs/sweep_*.log for per-design output."
