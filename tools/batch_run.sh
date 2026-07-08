#!/usr/bin/env bash
set -uo pipefail

# Batch runner for all design cases
# Runs: ORFS backend → LVS → RCX (skipping DRC)
# Usage: ./batch_run.sh [max_parallel_jobs] [timeout_per_design]

MAX_JOBS="${1:-4}"
ORFS_TIMEOUT="${2:-3600}"  # 1 hour per design
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CASES_DIR="$BASE_DIR/design_cases"
SKILL_SCRIPTS_DIR="$BASE_DIR/r2g-skills/signoff-loop/scripts/flow"
BATCH_DIR="$CASES_DIR/_batch"
mkdir -p "$BATCH_DIR"
RESULTS_FILE="$BATCH_DIR/batch_results.jsonl"
SUMMARY_FILE="$BATCH_DIR/batch_summary.txt"

# Source EDA environment
source /opt/openroad_tools_env.sh

# Clear previous results
> "$RESULTS_FILE"

run_one_design() {
  local case_dir="$1"
  local case_name
  case_name=$(basename "$case_dir")

  local config_mk="$case_dir/constraints/config.mk"
  local platform
  platform=$(grep 'PLATFORM' "$config_mk" | head -1 | sed 's/.*=\s*//' | tr -d ' ')
  local design_name
  design_name=$(grep 'DESIGN_NAME' "$config_mk" | head -1 | sed 's/.*=\s*//' | tr -d ' ')

  local result_json="{\"case\": \"$case_name\", \"design\": \"$design_name\", \"platform\": \"$platform\""
  local log_dir="$case_dir/batch_logs"
  mkdir -p "$log_dir"

  echo "[$(date '+%H:%M:%S')] START $case_name ($design_name)"

  # Lock per DESIGN_NAME to prevent concurrent ORFS runs that share config.mk
  local lock_dir="/tmp/orfs_locks"
  mkdir -p "$lock_dir"
  local lock_file="$lock_dir/${design_name}.lock"

  # Stage 1: ORFS backend (serialized per DESIGN_NAME)
  local orfs_status=0
  (
    flock -x 200
    ORFS_TIMEOUT="$ORFS_TIMEOUT" timeout --signal=TERM --kill-after=60 "$((ORFS_TIMEOUT * 6))" \
      bash "$SKILL_SCRIPTS_DIR/run_orfs.sh" "$case_dir" "$platform" > "$log_dir/orfs.log" 2>&1
    orfs_exit=$?

    # Also run LVS and RCX inside the lock to prevent result dir conflicts
    if [[ $orfs_exit -eq 0 ]]; then
      LVS_TIMEOUT=3600 timeout --signal=TERM --kill-after=30 3660 \
        bash "$SKILL_SCRIPTS_DIR/run_lvs.sh" "$case_dir" "$platform" > "$log_dir/lvs.log" 2>&1
      lvs_exit=$?

      RCX_TIMEOUT=3600 timeout --signal=TERM --kill-after=30 3660 \
        bash "$SKILL_SCRIPTS_DIR/run_rcx.sh" "$case_dir" "$platform" > "$log_dir/rcx.log" 2>&1
      rcx_exit=$?

      echo "$orfs_exit $lvs_exit $rcx_exit" > "$log_dir/exit_codes.txt"
    else
      echo "$orfs_exit -1 -1" > "$log_dir/exit_codes.txt"
    fi
  ) 200>"$lock_file"

  # Read exit codes from the locked subshell
  local codes
  codes=$(cat "$log_dir/exit_codes.txt" 2>/dev/null || echo "1 -1 -1")
  orfs_status=$(echo "$codes" | awk '{print $1}')
  local lvs_status=$(echo "$codes" | awk '{print $2}')
  local rcx_status=$(echo "$codes" | awk '{print $3}')

  if [[ "$orfs_status" -eq 0 ]]; then
    result_json="$result_json, \"orfs\": \"pass\""
  else
    result_json="$result_json, \"orfs\": \"fail($orfs_status)\""
    result_json="$result_json, \"lvs\": \"skipped\", \"rcx\": \"skipped\"}"
    echo "$result_json" >> "$RESULTS_FILE"
    echo "[$(date '+%H:%M:%S')] DONE  $case_name — ORFS FAILED ($orfs_status)"
    return "$orfs_status"
  fi

  if [[ "$lvs_status" -eq 0 ]]; then
    result_json="$result_json, \"lvs\": \"pass\""
  else
    result_json="$result_json, \"lvs\": \"fail($lvs_status)\""
  fi

  if [[ "$rcx_status" -eq 0 ]]; then
    result_json="$result_json, \"rcx\": \"pass\""
  else
    result_json="$result_json, \"rcx\": \"fail($rcx_status)\""
  fi

  result_json="$result_json}"
  echo "$result_json" >> "$RESULTS_FILE"
  echo "[$(date '+%H:%M:%S')] DONE  $case_name — ORFS:pass LVS:$([[ $lvs_status -eq 0 ]] && echo pass || echo fail) RCX:$([[ $rcx_status -eq 0 ]] && echo pass || echo fail)"
}

export -f run_one_design
export ORFS_TIMEOUT SKILL_SCRIPTS_DIR RESULTS_FILE

# Get list of all design cases, interleaved by DESIGN_NAME for better parallelism
# Instead of running cfg1,cfg2,...cfg10 of same design sequentially, interleave:
# cfg1_of_designA, cfg1_of_designB, ..., cfg2_of_designA, cfg2_of_designB, ...
mapfile -t ALL_CASES < <(
  for d in "$CASES_DIR"/*/constraints/config.mk; do
    dir=$(dirname "$(dirname "$d")")
    name=$(basename "$dir")
    # Extract config number suffix for interleaving
    suffix=$(echo "$name" | grep -oP '(cfg|v)\K\d+$' || echo "0")
    printf "%s\t%s\n" "$suffix" "$dir"
  done | sort -t$'\t' -k1,1n -k2,2 | cut -f2
)

TOTAL=${#ALL_CASES[@]}
echo "================================================================"
echo "Batch run: $TOTAL designs, $MAX_JOBS parallel jobs"
echo "ORFS timeout: ${ORFS_TIMEOUT}s per design"
echo "Results: $RESULTS_FILE"
echo "================================================================"
echo ""

# Run with GNU parallel if available, otherwise use xargs
COMPLETED=0
FAILED=0

for case_dir in "${ALL_CASES[@]}"; do
  # Wait if we have too many background jobs
  while [[ $(jobs -r | wc -l) -ge $MAX_JOBS ]]; do
    sleep 2
  done
  run_one_design "$case_dir" &
done

# Wait for all background jobs
wait

echo ""
echo "================================================================"
echo "Batch run complete. Generating summary..."
echo "================================================================"

# Generate summary
TOTAL_RESULTS=$(wc -l < "$RESULTS_FILE")
ORFS_PASS=$(grep -c '"orfs": "pass"' "$RESULTS_FILE" || true)
ORFS_FAIL=$(grep -c '"orfs": "fail' "$RESULTS_FILE" || true)
LVS_PASS=$(grep -c '"lvs": "pass"' "$RESULTS_FILE" || true)
LVS_FAIL=$(grep -c '"lvs": "fail' "$RESULTS_FILE" || true)
LVS_SKIP=$(grep -c '"lvs": "skipped"' "$RESULTS_FILE" || true)
RCX_PASS=$(grep -c '"rcx": "pass"' "$RESULTS_FILE" || true)
RCX_FAIL=$(grep -c '"rcx": "fail' "$RESULTS_FILE" || true)
RCX_SKIP=$(grep -c '"rcx": "skipped"' "$RESULTS_FILE" || true)

cat > "$SUMMARY_FILE" <<EOF
Batch Run Summary ($(date))
=====================================
Total designs: $TOTAL_RESULTS / $TOTAL

ORFS Backend:
  Pass: $ORFS_PASS
  Fail: $ORFS_FAIL

LVS:
  Pass: $LVS_PASS
  Fail: $LVS_FAIL
  Skipped: $LVS_SKIP

RCX:
  Pass: $RCX_PASS
  Fail: $RCX_FAIL
  Skipped: $RCX_SKIP

Failed designs:
$(grep '"orfs": "fail' "$RESULTS_FILE" | python3 -c "import sys,json; [print(f'  ORFS: {json.loads(l)[\"case\"]}') for l in sys.stdin]" 2>/dev/null || grep '"orfs": "fail' "$RESULTS_FILE")

$(grep '"lvs": "fail' "$RESULTS_FILE" | python3 -c "import sys,json; [print(f'  LVS: {json.loads(l)[\"case\"]}') for l in sys.stdin]" 2>/dev/null || grep '"lvs": "fail' "$RESULTS_FILE")

$(grep '"rcx": "fail' "$RESULTS_FILE" | python3 -c "import sys,json; [print(f'  RCX: {json.loads(l)[\"case\"]}') for l in sys.stdin]" 2>/dev/null || grep '"rcx": "fail' "$RESULTS_FILE")
EOF

cat "$SUMMARY_FILE"
