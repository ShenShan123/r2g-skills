#!/usr/bin/env bash
set -uo pipefail

# Re-run any Pass 4 case that finished with "fail(124)" (stage timeout)
# using a doubled ORFS_TIMEOUT. Serial, 1 at a time, so we don't re-create
# the concurrency pressure that caused the original timeout.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../r2g-rtl2gds/scripts/flow" && pwd)"
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESULTS_FILE="$BASE_DIR/design_cases/_batch/retry_pass4.jsonl"
LOG_DIR="$BASE_DIR/design_cases/_batch/logs"
RECOVER_FILE="$BASE_DIR/design_cases/_batch/recover_pass4.jsonl"

export ORFS_ROOT="${ORFS_ROOT:-/proj/workarea/user5/OpenROAD-flow-scripts}"

mkdir -p "$LOG_DIR"

# Collect timed-out cases from pass4 JSONL
mapfile -t TIMEOUTS < <(python3 -c '
import json, sys
from pathlib import Path
p = Path("'"$RESULTS_FILE"'")
if not p.exists():
    sys.exit(0)
for line in p.read_text().splitlines():
    try:
        j = json.loads(line)
    except Exception:
        continue
    if j.get("orfs", "") == "fail(124)":
        print(j.get("case",""), j.get("timeout", 7200))
')

if [[ ${#TIMEOUTS[@]} -eq 0 ]]; then
    echo "No timeouts to recover."
    exit 0
fi

echo "Recovering ${#TIMEOUTS[@]} timed-out case(s) with doubled timeout..."
for entry in "${TIMEOUTS[@]}"; do
    read -r case_name old_timeout <<< "$entry"
    new_timeout=$((old_timeout * 2))
    # Cap at 8h (28800s)
    [[ $new_timeout -gt 28800 ]] && new_timeout=28800
    project="$BASE_DIR/design_cases/$case_name"
    logfile="$LOG_DIR/${case_name}_recover.log"
    echo "$(date '+%H:%M:%S') RECOVER $case_name (timeout $old_timeout -> $new_timeout)" | tee -a "$logfile"
    start=$(date +%s)
    status=0
    ORFS_TIMEOUT="$new_timeout" "$SCRIPT_DIR/run_orfs.sh" "$project" nangate45 >> "$logfile" 2>&1 || status=$?
    elapsed=$(( $(date +%s) - start ))
    result="fail($status)"; [[ $status -eq 0 ]] && result="pass"
    echo "$(date '+%H:%M:%S') RECOVER_DONE $case_name -> $result (${elapsed}s)" | tee -a "$logfile"
    design=$(grep 'DESIGN_NAME' "$project/constraints/config.mk" | head -1 | sed 's/.*=\s*//' | tr -d ' ')
    printf '{"case":"%s","design":"%s","orfs":"%s","elapsed_s":%d,"old_timeout":%d,"new_timeout":%d}\n' \
        "$case_name" "$design" "$result" "$elapsed" "$old_timeout" "$new_timeout" >> "$RECOVER_FILE"
done

echo ""
echo "Recovery complete. Results: $RECOVER_FILE"
wc -l "$RECOVER_FILE" 2>/dev/null || true
