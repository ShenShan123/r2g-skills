#!/usr/bin/env bash
set -euo pipefail

# Pass 4 retry of the 19 remaining failures.
#
# Buckets:
#   A) 10 ethernet/axis designs — full re-run (synth already passed at 32768 MAX_BITS)
#   B) 3 place-timeout FIFO designs — full re-run with ORFS_TIMEOUT=14400
#   C) 4 iscas89 designs — full re-run (tiny, should complete in <5 min)
#   D) arm_core, koios_gemm_layer — full re-run with ORFS_TIMEOUT=14400
#   E) koios_lenet — handled separately (needs 8h budget + macro inference)
#   F) clog2_test — permanently skipped (zero-logic)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../r2g-rtl2gds/scripts/flow" && pwd)"
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESULTS_DIR="$BASE_DIR/design_cases/_batch"
RESULTS_FILE="$RESULTS_DIR/retry_pass4.jsonl"
LOG_DIR="$RESULTS_DIR/logs"
MAX_JOBS="${1:-3}"

mkdir -p "$LOG_DIR"

# Ensure ORFS env is loaded (the flow scripts will auto-detect, but keep it here for sanity)
export ORFS_ROOT="${ORFS_ROOT:-/proj/workarea/user5/OpenROAD-flow-scripts}"

run_one() {
  local case_name="$1"
  local timeout="${2:-7200}"
  local from_stage="${3:-}"
  local project_dir="$BASE_DIR/design_cases/$case_name"
  local logfile="$LOG_DIR/${case_name}_pass4.log"

  if [[ ! -d "$project_dir" ]]; then
    printf '{"case":"%s","orfs":"skip","reason":"missing_dir"}\n' "$case_name" >> "$RESULTS_FILE"
    return 0
  fi

  local start_time
  start_time=$(date +%s)
  echo "$(date '+%H:%M:%S') START $case_name (timeout=${timeout}, from_stage=${from_stage:-full})" | tee -a "$logfile"

  local status=0
  if [[ -n "$from_stage" ]]; then
    FROM_STAGE="$from_stage" ORFS_TIMEOUT="$timeout" \
      "$SCRIPT_DIR/run_orfs.sh" "$project_dir" nangate45 >> "$logfile" 2>&1 || status=$?
  else
    ORFS_TIMEOUT="$timeout" \
      "$SCRIPT_DIR/run_orfs.sh" "$project_dir" nangate45 >> "$logfile" 2>&1 || status=$?
  fi

  local elapsed=$(( $(date +%s) - start_time ))
  local result="fail($status)"
  [[ $status -eq 0 ]] && result="pass"

  echo "$(date '+%H:%M:%S') DONE  $case_name → $result (${elapsed}s)" | tee -a "$logfile"

  local design_name
  design_name=$(grep 'DESIGN_NAME' "$project_dir/constraints/config.mk" | head -1 | sed 's/.*=\s*//' | tr -d ' ')

  printf '{"case":"%s","design":"%s","orfs":"%s","elapsed_s":%d,"timeout":%d,"from_stage":"%s"}\n' \
    "$case_name" "$design_name" "$result" "$elapsed" "$timeout" "${from_stage:-full}" >> "$RESULTS_FILE"
}
export -f run_one
export SCRIPT_DIR BASE_DIR RESULTS_FILE LOG_DIR

echo "=== Pass 4 Retry: $(date) ==="
echo "Results: $RESULTS_FILE"
echo "Max parallel jobs: $MAX_JOBS"
echo ""

# Bucket A: 10 ethernet/axis designs, default 7200s
A=(
  verilog_ethernet_arp
  verilog_ethernet_axis_baser_rx_64
  verilog_ethernet_axis_baser_tx_64
  verilog_ethernet_eth_mac_10g
  verilog_ethernet_ip_complete
  verilog_ethernet_ip_complete_64
  verilog_ethernet_udp_complete
  verilog_ethernet_udp_complete_64
)

# Bucket B: 3 place-timeout FIFO designs, 14400s
B=(
  verilog_axis_axis_ram_switch
  verilog_ethernet_eth_mac_1g_fifo
  verilog_ethernet_eth_mac_mii_fifo
)

# Bucket C: 4 iscas89 designs (tiny)
C=(
  iscas89_s1196
  iscas89_s820
  iscas89_s832
  iscas89_s953
)

# Bucket D: arm_core + koios_gemm_layer (14400s)
D=(
  arm_core
  koios_gemm_layer
)

{
  for c in "${A[@]}"; do echo "$c|7200|"; done
  for c in "${B[@]}"; do echo "$c|14400|"; done
  for c in "${C[@]}"; do echo "$c|3600|"; done
  for c in "${D[@]}"; do echo "$c|14400|"; done
} | xargs -P "$MAX_JOBS" -I {} bash -c '
  IFS="|" read -r case_name timeout from_stage <<< "{}"
  run_one "$case_name" "$timeout" "$from_stage"
'

echo ""
echo "=== Pass 4 complete: $(date) ==="
wc -l "$RESULTS_FILE" 2>/dev/null || true
