#!/usr/bin/env bash
set -euo pipefail

# usage: run_lint.sh <rtl-file> <log-file>
RTL_FILE="${1:-}"
LOG_FILE="${2:-lint.log}"

if [[ -z "$RTL_FILE" ]]; then
  echo "usage: run_lint.sh <rtl-file> <log-file>" >&2
  exit 1
fi

mkdir -p "$(dirname "$LOG_FILE")"

LINT_STATUS=0
if command -v verilator >/dev/null 2>&1; then
  verilator --lint-only "$RTL_FILE" >"$LOG_FILE" 2>&1 || LINT_STATUS=$?
elif command -v iverilog >/dev/null 2>&1; then
  iverilog -t null "$RTL_FILE" >"$LOG_FILE" 2>&1 || LINT_STATUS=$?
else
  echo "No lint-capable tool found (need verilator or iverilog)" >"$LOG_FILE"
  exit 2
fi

if [[ $LINT_STATUS -eq 0 ]]; then
  echo "lint_ok" >>"$LOG_FILE"
else
  echo "lint_failed (exit code $LINT_STATUS)" >>"$LOG_FILE"
fi

exit $LINT_STATUS
