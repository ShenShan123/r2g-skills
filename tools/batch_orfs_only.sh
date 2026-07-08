#!/usr/bin/env bash
set -uo pipefail

# Pass 1: ORFS backend only for all designs (no DRC/LVS/RCX)
# Defers failures — just records them and moves on
# Usage: ./batch_orfs_only.sh [max_parallel_jobs] [orfs_timeout]

MAX_JOBS="${1:-8}"
ORFS_TIMEOUT="${2:-3600}"
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CASES_DIR="$BASE_DIR/design_cases"
SKILL_DIR="$BASE_DIR/r2g-skills/signoff-loop"
FLOW_SCRIPTS="$SKILL_DIR/scripts/flow"
EXTRACT_SCRIPTS="$SKILL_DIR/scripts/extract"
BATCH_DIR="$CASES_DIR/_batch"
mkdir -p "$BATCH_DIR"
RESULTS_FILE="${RESULTS_FILE:-$BATCH_DIR/orfs_results.jsonl}"
LOG_DIR="$BATCH_DIR/logs"
mkdir -p "$LOG_DIR"

# Autodetect ORFS + tool paths (shared helper used by every flow script).
# User can override via ORFS_ROOT / *_EXE env vars or references/env.local.sh.
# shellcheck source=/dev/null
source "$SKILL_DIR/scripts/flow/_env.sh"

# Don't clear results — allows resume and multi-group append
touch "$RESULTS_FILE"

run_one_design() {
  local case_dir="$1"
  local case_name
  case_name=$(basename "$case_dir")

  local config_mk="$case_dir/constraints/config.mk"
  if [[ ! -f "$config_mk" ]]; then
    echo "{\"case\": \"$case_name\", \"status\": \"skip\", \"reason\": \"no config.mk\"}" >> "$RESULTS_FILE"
    return 0
  fi

  local platform
  platform=$(grep 'PLATFORM' "$config_mk" | head -1 | sed 's/.*=\s*//' | tr -d ' ')
  local design_name
  design_name=$(grep 'DESIGN_NAME' "$config_mk" | head -1 | sed 's/.*=\s*//' | tr -d ' ')

  # Skip if already has a successful ORFS run
  if ls "$case_dir/backend/RUN_"*/run-meta.json 2>/dev/null | while read f; do grep -q '"make_status": 0' "$f" && echo "found" && break; done | grep -q "found"; then
    echo "{\"case\": \"$case_name\", \"design\": \"$design_name\", \"platform\": \"$platform\", \"orfs\": \"pass\", \"status\": \"cached\", \"elapsed_s\": 0}" >> "$RESULTS_FILE"
    echo "[$(date '+%H:%M:%S')] SKIP  $case_name (already has successful ORFS run)"
    return 0
  fi

  local design_log="$LOG_DIR/${case_name}.log"
  local start_time
  start_time=$(date +%s)

  echo "[$(date '+%H:%M:%S')] START $case_name ($design_name on $platform)"

  # Lock per case_name (not DESIGN_NAME) since FLOW_VARIANT isolates ORFS work dirs.
  # Multiple designs can share a DESIGN_NAME (e.g. ICCAD "top") and still run in parallel.
  local lock_dir="/tmp/orfs_locks"
  mkdir -p "$lock_dir"
  local lock_file="$lock_dir/${case_name}.lock"

  local orfs_exit=1

  (
    flock -x 200

    echo "[$(date '+%H:%M:%S')] $case_name: ORFS starting..." >> "$design_log"
    ORFS_TIMEOUT="$ORFS_TIMEOUT" setsid timeout --signal=TERM --kill-after=60 "$((ORFS_TIMEOUT * 6 + 120))" \
      bash "$FLOW_SCRIPTS/run_orfs.sh" "$case_dir" "$platform" >> "$design_log" 2>&1
    echo "$?" > "$case_dir/.orfs_exit"
  ) 200>"$lock_file"

  orfs_exit=$(cat "$case_dir/.orfs_exit" 2>/dev/null || echo "1")

  local end_time
  end_time=$(date +%s)
  local elapsed=$((end_time - start_time))

  # Extract PPA if ORFS passed
  if [[ "$orfs_exit" -eq 0 ]]; then
    mkdir -p "$case_dir/reports"
    python3 "$EXTRACT_SCRIPTS/extract_ppa.py" "$case_dir" "$case_dir/reports/ppa.json" >> "$design_log" 2>&1 || true
    echo "{\"case\": \"$case_name\", \"design\": \"$design_name\", \"platform\": \"$platform\", \"orfs\": \"pass\", \"elapsed_s\": $elapsed}" >> "$RESULTS_FILE"
    echo "[$(date '+%H:%M:%S')] DONE  $case_name (${elapsed}s) — ORFS:pass"
  else
    echo "{\"case\": \"$case_name\", \"design\": \"$design_name\", \"platform\": \"$platform\", \"orfs\": \"fail($orfs_exit)\", \"elapsed_s\": $elapsed}" >> "$RESULTS_FILE"
    echo "[$(date '+%H:%M:%S')] DONE  $case_name (${elapsed}s) — ORFS:fail($orfs_exit)"
  fi

  rm -f "$case_dir/.orfs_exit"
}

export -f run_one_design
export ORFS_TIMEOUT FLOW_SCRIPTS EXTRACT_SCRIPTS RESULTS_FILE LOG_DIR CASES_DIR

# Get design list (from DESIGNS_LIST file or all design_cases)
if [[ -n "${DESIGNS_LIST:-}" ]] && [[ -f "$DESIGNS_LIST" ]]; then
  mapfile -t ALL_CASES < <(while IFS= read -r name; do
    name=$(echo "$name" | tr -d ' ')
    [[ -n "$name" && -d "$CASES_DIR/$name" ]] && echo "$CASES_DIR/$name"
  done < "$DESIGNS_LIST")
else
  mapfile -t ALL_CASES < <(
    for d in "$CASES_DIR"/*/constraints/config.mk; do
      dirname "$(dirname "$d")"
    done | sort
  )
fi

TOTAL=${#ALL_CASES[@]}
echo "================================================================"
echo "Pass 1: ORFS-only batch — $TOTAL designs, $MAX_JOBS parallel jobs"
echo "ORFS timeout: ${ORFS_TIMEOUT}s per stage"
echo "Results: $RESULTS_FILE"
echo "Started: $(date)"
echo "================================================================"
echo ""

# Run with limited parallelism
for case_dir in "${ALL_CASES[@]}"; do
  while [[ $(jobs -r | wc -l) -ge $MAX_JOBS ]]; do
    sleep 1
  done
  run_one_design "$case_dir" &
done

wait

echo ""
echo "================================================================"
echo "Pass 1 complete: $(date)"
echo "================================================================"

# Summary
TOTAL_RESULTS=$(wc -l < "$RESULTS_FILE")
ORFS_PASS=$(grep -c '"orfs": "pass"' "$RESULTS_FILE" || true)
ORFS_FAIL=$(grep -c '"orfs": "fail' "$RESULTS_FILE" || true)
CACHED=$(grep -c '"status": "cached"' "$RESULTS_FILE" || true)

echo "Total: $TOTAL_RESULTS / $TOTAL"
echo "ORFS pass: $ORFS_PASS (cached: $CACHED)"
echo "ORFS fail: $ORFS_FAIL"

# List failures
echo ""
echo "Failures:"
grep '"orfs": "fail' "$RESULTS_FILE" | python3 -c "
import sys,json
for l in sys.stdin:
    try:
        d = json.loads(l)
        print(f'  {d[\"case\"]}: {d[\"orfs\"]} ({d[\"elapsed_s\"]}s)')
    except: pass
" 2>/dev/null || true

echo ""
echo "Run signoff on passed designs with: bash tools/batch_signoff.sh"
