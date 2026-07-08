#!/usr/bin/env bash
set -uo pipefail

# Full-flow batch runner for all designs in design_cases/
# Runs: ORFS backend → PPA extract → DRC → LVS → RCX → extract all results
# Usage: ./batch_flow.sh [max_parallel_jobs] [orfs_timeout]
#
# Environment:
#   DESIGNS_LIST=file.txt   — run only designs listed (one per line)
#   SKIP_EXISTING=1         — skip designs that already have backend results

MAX_JOBS="${1:-4}"
ORFS_TIMEOUT="${2:-3600}"
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CASES_DIR="$BASE_DIR/design_cases"
SKILL_DIR="$BASE_DIR/r2g-skills/signoff-loop"
FLOW_SCRIPTS="$SKILL_DIR/scripts/flow"
EXTRACT_SCRIPTS="$SKILL_DIR/scripts/extract"
REPORT_SCRIPTS="$SKILL_DIR/scripts/reports"
BATCH_DIR="$CASES_DIR/_batch"
mkdir -p "$BATCH_DIR"
# BATCH_TAG namespaces the results/summary/log files so multiple batch_flow.sh
# invocations (e.g. parallel worker agents) do not clobber each other's output.
BATCH_TAG="${BATCH_TAG:-}"
RESULTS_FILE="$BATCH_DIR/full_flow_results${BATCH_TAG:+_$BATCH_TAG}.jsonl"
SUMMARY_FILE="$BATCH_DIR/full_flow_summary${BATCH_TAG:+_$BATCH_TAG}.txt"
LOG_DIR="$BATCH_DIR/logs${BATCH_TAG:+_$BATCH_TAG}"
mkdir -p "$LOG_DIR"
SKIP_EXISTING="${SKIP_EXISTING:-0}"

# Source EDA environment
ORFS_ROOT="${ORFS_ROOT:-/proj/workarea/user5/OpenROAD-flow-scripts}"
if [[ -f "$ORFS_ROOT/env.sh" ]]; then
  source "$ORFS_ROOT/env.sh"
elif [[ -f /opt/openroad_tools_env.sh ]]; then
  source /opt/openroad_tools_env.sh
fi

# Clear previous results, unless BATCH_APPEND=1 (used when resuming a stalled
# run so prior result lines are preserved). SKIP_EXISTING avoids re-running
# designs whose backend already completed.
if [[ "${BATCH_APPEND:-0}" != "1" ]]; then
  > "$RESULTS_FILE"
fi
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

  # Skip if already has results
  if [[ "$SKIP_EXISTING" == "1" ]] && ls "$case_dir/backend/RUN_"*/run-meta.json 2>/dev/null | head -1 | grep -q "make_status.*0"; then
    echo "{\"case\": \"$case_name\", \"status\": \"skip\", \"reason\": \"already completed\"}" >> "$RESULTS_FILE"
    return 0
  fi

  local design_log="$LOG_DIR/${case_name}.log"
  local start_time
  start_time=$(date +%s)

  echo "[$(date '+%H:%M:%S')] START $case_name ($design_name on $platform)" | tee -a "$design_log"

  local result_json="{\"case\": \"$case_name\", \"design\": \"$design_name\", \"platform\": \"$platform\""

  # Lock per DESIGN_NAME to prevent concurrent ORFS runs
  local lock_dir="/tmp/orfs_locks"
  mkdir -p "$lock_dir"
  local lock_file="$lock_dir/${design_name}.lock"

  local orfs_exit=1 drc_exit=1 lvs_exit=1 rcx_exit=1

  # Stage 1: ORFS backend (locked per DESIGN_NAME to prevent collisions)
  (
    flock -x 200

    echo "[$(date '+%H:%M:%S')] $case_name: ORFS starting..." >> "$design_log"
    ORFS_TIMEOUT="$ORFS_TIMEOUT" setsid timeout --signal=TERM --kill-after=60 "$((ORFS_TIMEOUT * 6 + 120))" \
      bash "$FLOW_SCRIPTS/run_orfs.sh" "$case_dir" "$platform" >> "$design_log" 2>&1
    orfs_exit=$?
    echo "$orfs_exit" > "$case_dir/.orfs_exit"

    if [[ $orfs_exit -ne 0 ]]; then
      echo "[$(date '+%H:%M:%S')] $case_name: ORFS FAILED ($orfs_exit)" >> "$design_log"
    else
      echo "[$(date '+%H:%M:%S')] $case_name: ORFS passed" >> "$design_log"
    fi
  ) 200>"$lock_file"

  # Read ORFS exit code (lock is now released — slot is free for next design)
  orfs_exit=$(cat "$case_dir/.orfs_exit" 2>/dev/null || echo "1")

  if [[ "$orfs_exit" -ne 0 ]]; then
    local end_time; end_time=$(date +%s); local elapsed=$((end_time - start_time))
    echo "{\"case\": \"$case_name\", \"design\": \"$design_name\", \"platform\": \"$platform\", \"orfs\": \"fail($orfs_exit)\", \"drc\": \"skipped\", \"lvs\": \"skipped\", \"rcx\": \"skipped\", \"elapsed_s\": $elapsed}" >> "$RESULTS_FILE"
    rm -f "$case_dir/.orfs_exit"
    echo "[$(date '+%H:%M:%S')] DONE  $case_name (${elapsed}s) — ORFS:fail($orfs_exit) DRC:skipped LVS:skipped RCX:skipped"
    return "$orfs_exit"
  fi

  # Signoff stages run OUTSIDE the lock (don't block the ORFS slot)
  # Stage 2: Extract PPA
  mkdir -p "$case_dir/reports"
  python3 "$EXTRACT_SCRIPTS/extract_ppa.py" "$case_dir" "$case_dir/reports/ppa.json" >> "$design_log" 2>&1 || true

  # Stage 3: DRC
  echo "[$(date '+%H:%M:%S')] $case_name: DRC starting..." >> "$design_log"
  setsid timeout --signal=TERM --kill-after=30 3660 \
    bash "$FLOW_SCRIPTS/run_drc.sh" "$case_dir" "$platform" >> "$design_log" 2>&1
  drc_exit=$?
  echo "[$(date '+%H:%M:%S')] $case_name: DRC exit=$drc_exit" >> "$design_log"

  # Stage 4: LVS
  echo "[$(date '+%H:%M:%S')] $case_name: LVS starting..." >> "$design_log"
  setsid timeout --signal=TERM --kill-after=30 3660 \
    bash "$FLOW_SCRIPTS/run_lvs.sh" "$case_dir" "$platform" >> "$design_log" 2>&1
  lvs_exit=$?
  echo "[$(date '+%H:%M:%S')] $case_name: LVS exit=$lvs_exit" >> "$design_log"

  # Stage 5: RCX
  echo "[$(date '+%H:%M:%S')] $case_name: RCX starting..." >> "$design_log"
  setsid timeout --signal=TERM --kill-after=30 3660 \
    bash "$FLOW_SCRIPTS/run_rcx.sh" "$case_dir" "$platform" >> "$design_log" 2>&1
  rcx_exit=$?
  echo "[$(date '+%H:%M:%S')] $case_name: RCX exit=$rcx_exit" >> "$design_log"

  # Stage 6: Extract results
  python3 "$EXTRACT_SCRIPTS/extract_drc.py" "$case_dir" "$case_dir/reports/drc.json" >> "$design_log" 2>&1 || true
  python3 "$EXTRACT_SCRIPTS/extract_lvs.py" "$case_dir" "$case_dir/reports/lvs.json" >> "$design_log" 2>&1 || true
  python3 "$EXTRACT_SCRIPTS/extract_rcx.py" "$case_dir" "$case_dir/reports/rcx.json" >> "$design_log" 2>&1 || true

  # Build result JSON (orfs_exit already known, drc/lvs/rcx captured above)
  local end_time
  end_time=$(date +%s)
  local elapsed=$((end_time - start_time))

  local orfs_s="pass" drc_s lvs_s rcx_s
  [[ "$drc_exit" -eq 0 ]] && drc_s="pass" || drc_s="fail($drc_exit)"
  [[ "$lvs_exit" -eq 0 ]] && lvs_s="pass" || lvs_s="fail($lvs_exit)"
  [[ "$rcx_exit" -eq 0 ]] && rcx_s="pass" || rcx_s="fail($rcx_exit)"

  result_json="$result_json, \"orfs\": \"$orfs_s\", \"drc\": \"$drc_s\", \"lvs\": \"$lvs_s\", \"rcx\": \"$rcx_s\", \"elapsed_s\": $elapsed}"
  echo "$result_json" >> "$RESULTS_FILE"

  # Clean up temp files
  rm -f "$case_dir/.orfs_exit"

  echo "[$(date '+%H:%M:%S')] DONE  $case_name (${elapsed}s) — ORFS:$orfs_s DRC:$drc_s LVS:$lvs_s RCX:$rcx_s"
}

export -f run_one_design
export ORFS_TIMEOUT FLOW_SCRIPTS EXTRACT_SCRIPTS REPORT_SCRIPTS RESULTS_FILE LOG_DIR SKIP_EXISTING

# Get design list
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
echo "Full-flow batch: $TOTAL designs, $MAX_JOBS parallel jobs"
echo "ORFS timeout: ${ORFS_TIMEOUT}s per stage"
echo "Results: $RESULTS_FILE"
echo "Logs: $LOG_DIR/"
echo "Skip existing: $SKIP_EXISTING"
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

# Wait for all
wait

echo ""
echo "================================================================"
echo "Full-flow batch complete: $(date)"
echo "Generating summary..."
echo "================================================================"

# Generate summary
TOTAL_RESULTS=$(wc -l < "$RESULTS_FILE")
ORFS_PASS=$(grep -c '"orfs": "pass"' "$RESULTS_FILE" || true)
ORFS_FAIL=$(grep -c '"orfs": "fail' "$RESULTS_FILE" || true)
DRC_PASS=$(grep -c '"drc": "pass"' "$RESULTS_FILE" || true)
DRC_FAIL=$(grep -c '"drc": "fail' "$RESULTS_FILE" || true)
LVS_PASS=$(grep -c '"lvs": "pass"' "$RESULTS_FILE" || true)
LVS_FAIL=$(grep -c '"lvs": "fail' "$RESULTS_FILE" || true)
RCX_PASS=$(grep -c '"rcx": "pass"' "$RESULTS_FILE" || true)
RCX_FAIL=$(grep -c '"rcx": "fail' "$RESULTS_FILE" || true)
SKIPPED=$(grep -c '"status": "skip"' "$RESULTS_FILE" || true)

# Average time for completed designs
AVG_TIME=$(grep '"elapsed_s"' "$RESULTS_FILE" | python3 -c "
import sys, json, re
times = []
for line in sys.stdin:
    m = re.search(r'\"elapsed_s\": (\d+)', line)
    if m: times.append(int(m.group(1)))
if times:
    print(f'{sum(times)/len(times):.0f}s avg, {min(times)}s min, {max(times)}s max, {sum(times)/3600:.1f}h total')
else:
    print('no timing data')
" 2>/dev/null || echo "N/A")

cat > "$SUMMARY_FILE" <<EOF
Full-Flow Batch Run Summary
$(date)
=====================================
Total designs: $TOTAL_RESULTS / $TOTAL
Skipped: $SKIPPED
Timing: $AVG_TIME

ORFS Backend:  Pass=$ORFS_PASS  Fail=$ORFS_FAIL
DRC:           Pass=$DRC_PASS  Fail=$DRC_FAIL
LVS:           Pass=$LVS_PASS  Fail=$LVS_FAIL
RCX:           Pass=$RCX_PASS  Fail=$RCX_FAIL

All-pass (ORFS+DRC+LVS+RCX): $(grep '"orfs": "pass"' "$RESULTS_FILE" | grep '"drc": "pass"' | grep '"lvs": "pass"' | grep -c '"rcx": "pass"' || true)

ORFS Failures:
$(grep '"orfs": "fail' "$RESULTS_FILE" | python3 -c "
import sys,json
for l in sys.stdin:
    try:
        d = json.loads(l)
        print(f'  {d[\"case\"]}: {d[\"orfs\"]}')
    except: pass
" 2>/dev/null || true)

DRC Failures:
$(grep '"drc": "fail' "$RESULTS_FILE" | python3 -c "
import sys,json
for l in sys.stdin:
    try:
        d = json.loads(l)
        print(f'  {d[\"case\"]}: {d[\"drc\"]}')
    except: pass
" 2>/dev/null || true)

LVS Failures:
$(grep '"lvs": "fail' "$RESULTS_FILE" | python3 -c "
import sys,json
for l in sys.stdin:
    try:
        d = json.loads(l)
        print(f'  {d[\"case\"]}: {d[\"lvs\"]}')
    except: pass
" 2>/dev/null || true)

RCX Failures:
$(grep '"rcx": "fail' "$RESULTS_FILE" | python3 -c "
import sys,json
for l in sys.stdin:
    try:
        d = json.loads(l)
        print(f'  {d[\"case\"]}: {d[\"rcx\"]}')
    except: pass
" 2>/dev/null || true)
EOF

cat "$SUMMARY_FILE"
