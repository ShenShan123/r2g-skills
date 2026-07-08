#!/usr/bin/env bash
set -euo pipefail

# usage: run_sim.sh <rtl-file> <tb-file> <work-dir>
RTL_FILE="${1:-}"
TB_FILE="${2:-}"
WORK_DIR="${3:-sim}"

if [[ -z "$RTL_FILE" || -z "$TB_FILE" ]]; then
  echo "usage: run_sim.sh <rtl-file> <tb-file> <work-dir>" >&2
  exit 1
fi

mkdir -p "$WORK_DIR"

# Compile
COMPILE_STATUS=0
iverilog -o "$WORK_DIR/sim.out" "$RTL_FILE" "$TB_FILE" >"$WORK_DIR/compile.log" 2>&1 || COMPILE_STATUS=$?

if [[ $COMPILE_STATUS -ne 0 ]]; then
  echo "ERROR: iverilog compilation failed (exit code $COMPILE_STATUS)" >&2
  echo "Check log: $WORK_DIR/compile.log" >&2
  exit $COMPILE_STATUS
fi

# Simulate
SIM_STATUS=0
(
  cd "$WORK_DIR"
  vvp ./sim.out > sim.log 2>&1
) || SIM_STATUS=$?

if [[ $SIM_STATUS -ne 0 ]]; then
  echo "ERROR: Simulation failed (exit code $SIM_STATUS)" >&2
  echo "Check log: $WORK_DIR/sim.log" >&2
  exit $SIM_STATUS
fi

echo "simulation_ok" >> "$WORK_DIR/sim.log"
exit 0
