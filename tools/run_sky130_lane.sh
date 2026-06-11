#!/usr/bin/env bash
# Run one "lane" of the sky130hd campaign: a list of source project dirs, processed
# sequentially through run_sky130_design.sh. Resumable (skips designs whose result
# file already exists). Each design is CPU-capped so several lanes can run politely
# in parallel on a shared box. Designed to be launched with nohup in the background;
# progress goes to the lane log, per-design results to the race-free results dir.
#
# usage: run_sky130_lane.sh <lane_file>            # lane_file: one source dir per line
set -uo pipefail
REPO="/proj/workarea/user5/agent-r2g"
LANE_FILE="${1:?usage: run_sky130_lane.sh <lane_file>}"
RESULTS="$REPO/design_cases/_batch/sky130hd_results"
mkdir -p "$RESULTS"
cd "$REPO"

# Per-design resource caps (polite neighbour: box is shared). Override via env.
export ORFS_MAX_CPUS="${ORFS_MAX_CPUS:-4}"
export ORFS_TIMEOUT="${ORFS_TIMEOUT:-5400}"
export NETGEN_TIMEOUT="${NETGEN_TIMEOUT:-1800}"

total=0; done_n=0
while IFS= read -r src; do
  [[ -z "$src" ]] && continue
  total=$((total+1))
  base="$(basename "$src")"
  rf="$RESULTS/$base.json"
  if [[ -f "$rf" ]]; then
    echo "[$(date +%H:%M:%S)] SKIP (already done): $base"
    done_n=$((done_n+1)); continue
  fi
  echo "[$(date +%H:%M:%S)] START: $base"
  bash tools/run_sky130_design.sh "$src" >/dev/null 2>&1 || true
  if [[ -f "$rf" ]]; then
    echo "[$(date +%H:%M:%S)] DONE: $base -> $(cat "$rf")"
    done_n=$((done_n+1))
  else
    echo "[$(date +%H:%M:%S)] NO-RESULT: $base (driver produced no result file)"
  fi
done < "$LANE_FILE"

echo "[$(date +%H:%M:%S)] LANE COMPLETE: $done_n/$total designs have result files"
