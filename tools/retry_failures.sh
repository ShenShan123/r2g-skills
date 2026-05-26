#!/usr/bin/env bash
set -euo pipefail

# Retry script for the 24 actionable failure cases from Pass 2
# Groups:
#   A) 4 config-fixed designs (wrong top / sizing) — full re-run
#   B) 7 route-stage resume cases — FROM_STAGE=route
#   C) 3 place-stage resume cases — FROM_STAGE=place
#   D) 10 synthesis timeout cases — full re-run with ORFS_TIMEOUT=14400

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../r2g-rtl2gds/scripts/flow" && pwd)"
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESULTS_DIR="$BASE_DIR/design_cases/_batch"
RESULTS_FILE="$RESULTS_DIR/retry_pass3.jsonl"
LOG_DIR="$RESULTS_DIR/logs"
MAX_JOBS="${1:-4}"

mkdir -p "$LOG_DIR"

run_one() {
  local case_name="$1"
  local from_stage="${2:-}"
  local timeout="${3:-7200}"
  local project_dir="$BASE_DIR/design_cases/$case_name"
  local logfile="$LOG_DIR/${case_name}_retry3.log"

  if [[ ! -d "$project_dir" ]]; then
    echo "SKIP: $case_name — project dir not found" | tee -a "$logfile"
    return 1
  fi

  local start_time
  start_time=$(date +%s)

  echo "$(date '+%H:%M:%S') START $case_name (from_stage=${from_stage:-full}, timeout=${timeout})" | tee -a "$logfile"

  local status=0
  if [[ -n "$from_stage" ]]; then
    FROM_STAGE="$from_stage" ORFS_TIMEOUT="$timeout" "$SCRIPT_DIR/run_orfs.sh" "$project_dir" nangate45 >> "$logfile" 2>&1 || status=$?
  else
    ORFS_TIMEOUT="$timeout" "$SCRIPT_DIR/run_orfs.sh" "$project_dir" nangate45 >> "$logfile" 2>&1 || status=$?
  fi

  local end_time elapsed
  end_time=$(date +%s)
  elapsed=$((end_time - start_time))

  local result="fail($status)"
  [[ $status -eq 0 ]] && result="pass"

  echo "$(date '+%H:%M:%S') DONE  $case_name → $result (${elapsed}s)" | tee -a "$logfile"

  # Read DESIGN_NAME from config.mk
  local design_name
  design_name=$(grep 'DESIGN_NAME' "$project_dir/constraints/config.mk" | head -1 | sed 's/.*= *//')

  printf '{"case":"%s","design":"%s","platform":"nangate45","orfs":"%s","elapsed_s":%d,"from_stage":"%s","timeout":%d}\n' \
    "$case_name" "$design_name" "$result" "$elapsed" "${from_stage:-full}" "$timeout" >> "$RESULTS_FILE"
}

export -f run_one
export SCRIPT_DIR BASE_DIR RESULTS_FILE LOG_DIR

echo "=== Pass 3 Retry: $(date) ==="
echo "Max parallel jobs: $MAX_JOBS"
echo ""

# --- Group A: Config-fixed designs (full re-run, default timeout) ---
CONFIG_FIXED=(
  koios_lenet
  vtr_verilog_to_routing_min_odin_ii_regression_test_benchmark_verilog_large_mac1
  vtr_verilog_to_routing_min_odin_ii_regression_test_benchmark_verilog_large_mac2
  vtr_verilog_to_routing_min_odin_ii_regression_test_benchmark_verilog_c_functions_clog2_clog2_test
)

# --- Group B: Route-stage resume (ORFS_TIMEOUT=14400) ---
ROUTE_RESUME=(
  verilog_axis_axis_async_fifo_adapter
  verilog_axis_axis_fifo
  verilog_axis_axis_fifo_adapter
  verilog_axis_axis_frame_length_adjust_fifo
  wbscope_axil
  wbscope_wishbone
  zipcpu_wbdmac
)

# --- Group C: Place-stage resume (ORFS_TIMEOUT=14400) ---
PLACE_RESUME=(
  verilog_axis_axis_ram_switch
  verilog_ethernet_eth_mac_1g_fifo
  verilog_ethernet_eth_mac_mii_fifo
)

# --- Group D: Synthesis timeout (full re-run, ORFS_TIMEOUT=14400) ---
SYNTH_TIMEOUT=(
  arm_core
  koios_gemm_layer
  verilog_ethernet_arp
  verilog_ethernet_axis_baser_rx_64
  verilog_ethernet_axis_baser_tx_64
  verilog_ethernet_eth_mac_10g
  verilog_ethernet_ip_complete
  verilog_ethernet_ip_complete_64
  verilog_ethernet_udp_complete
  verilog_ethernet_udp_complete_64
)

echo "Group A (config-fixed, full re-run): ${#CONFIG_FIXED[@]} designs"
echo "Group B (route resume, 4h timeout): ${#ROUTE_RESUME[@]} designs"
echo "Group C (place resume, 4h timeout): ${#PLACE_RESUME[@]} designs"
echo "Group D (synth timeout, 4h timeout): ${#SYNTH_TIMEOUT[@]} designs"
echo "Total: $(( ${#CONFIG_FIXED[@]} + ${#ROUTE_RESUME[@]} + ${#PLACE_RESUME[@]} + ${#SYNTH_TIMEOUT[@]} )) designs"
echo ""

# Build a combined job list with pipe-separated arguments
# Format: case|from_stage|timeout  (empty from_stage = full re-run)
{
  for c in "${CONFIG_FIXED[@]}"; do echo "$c||7200"; done
  for c in "${ROUTE_RESUME[@]}"; do echo "$c|route|14400"; done
  for c in "${PLACE_RESUME[@]}"; do echo "$c|place|14400"; done
  for c in "${SYNTH_TIMEOUT[@]}"; do echo "$c||14400"; done
} | xargs -P "$MAX_JOBS" -I {} bash -c '
  IFS="|" read -r case_name from_stage timeout <<< "{}"
  run_one "$case_name" "$from_stage" "$timeout"
'

echo ""
echo "=== Pass 3 complete: $(date) ==="
echo "Results: $RESULTS_FILE"
wc -l "$RESULTS_FILE" 2>/dev/null || true
