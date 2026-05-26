#!/usr/bin/env bash
set -euo pipefail

# usage: run_lvs.sh <project-dir> [platform] [flow_variant]
# Runs KLayout LVS on a completed ORFS backend run.
# Compares GDS layout against the CDL netlist.
# Results are collected into <project-dir>/lvs/

PROJECT_DIR="${1:-}"
PLATFORM="${2:-nangate45}"
# Derive FLOW_VARIANT from project directory basename (matching run_orfs.sh logic)
if [[ -n "${3:-}" ]]; then
  FLOW_VARIANT="$3"
elif [[ -n "$PROJECT_DIR" && -d "$PROJECT_DIR" ]]; then
  FLOW_VARIANT="$(basename "$(cd "$PROJECT_DIR" && pwd)")"
else
  FLOW_VARIANT="base"
fi
# Auto-detect ORFS + tools (honors ORFS_ROOT / *_EXE env overrides)
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_env.sh"

if [[ -z "${ORFS_ROOT:-}" || ! -d "$FLOW_DIR" ]]; then
  echo "ERROR: ORFS not found. Set ORFS_ROOT to your OpenROAD-flow-scripts checkout." >&2
  exit 1
fi

if [[ -z "$PROJECT_DIR" ]]; then
  echo "usage: run_lvs.sh <project-dir> [platform]" >&2
  exit 1
fi

PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
CONFIG_MK="$PROJECT_DIR/constraints/config.mk"

if [[ ! -f "$CONFIG_MK" ]]; then
  echo "ERROR: config.mk not found at $CONFIG_MK" >&2
  exit 1
fi

DESIGN_NAME=$(grep 'DESIGN_NAME' "$CONFIG_MK" | head -1 | sed 's/.*=\s*//' | tr -d ' ')

# Verify GDS exists from a prior ORFS run
RESULTS_DIR="$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
if [[ ! -d "$RESULTS_DIR" ]]; then
  RESULTS_DIR="$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME"
fi

GDS_FILE=$(find "$RESULTS_DIR" -name "6_final.gds" 2>/dev/null | head -1)
if [[ -z "$GDS_FILE" ]]; then
  echo "ERROR: No 6_final.gds found in $RESULTS_DIR" >&2
  echo "Run the ORFS backend first: run_orfs.sh <project-dir>" >&2
  exit 1
fi

# Check if LVS rule file exists for this platform
PLATFORM_DIR="$FLOW_DIR/platforms/$PLATFORM"
KLAYOUT_LVS_FILE=$(grep 'KLAYOUT_LVS_FILE' "$PLATFORM_DIR/config.mk" 2>/dev/null | head -1 | sed 's/.*=\s*//' | sed "s|\$(PLATFORM_DIR)|$PLATFORM_DIR|g" | tr -d ' ')

# Resolve the actual file path
KLAYOUT_LVS_RESOLVED=""

# Try the path parsed from config.mk
if [[ -n "$KLAYOUT_LVS_FILE" && -f "$KLAYOUT_LVS_FILE" ]]; then
  KLAYOUT_LVS_RESOLVED="$KLAYOUT_LVS_FILE"
fi

# Fallback: scan common locations (even if config.mk had no KLAYOUT_LVS_FILE entry)
if [[ -z "$KLAYOUT_LVS_RESOLVED" ]]; then
  for candidate in \
    "$PLATFORM_DIR/lvs/"*.lylvs \
    "$PLATFORM_DIR/"*.lylvs; do
    if [[ -f "$candidate" ]]; then
      KLAYOUT_LVS_RESOLVED="$candidate"
      break
    fi
  done
fi

if [[ -z "$KLAYOUT_LVS_RESOLVED" ]]; then
  echo "WARNING: No KLayout LVS rule file found for platform $PLATFORM" >&2
  echo "LVS is not supported on this platform." >&2
  # Create a stub result
  LVS_DIR="$PROJECT_DIR/lvs"
  mkdir -p "$LVS_DIR"
  echo '{"status": "skipped", "reason": "No LVS rules available for platform '"$PLATFORM"'"}' > "$LVS_DIR/lvs_result.json"
  echo "LVS skipped: no rules for $PLATFORM"
  echo "Results: $LVS_DIR"
  exit 0
fi

echo "Running LVS for design: $DESIGN_NAME"
echo "Platform: $PLATFORM"
echo "GDS: $GDS_FILE"
echo "LVS rules: $KLAYOUT_LVS_RESOLVED"

# Run LVS via ORFS Makefile.
# Match run_orfs.sh: configs are placed at designs/<plat>/<design>/<variant>/config.mk
# so that two FLOW_VARIANT runs of the same DESIGN_NAME don't collide.
# Fall back to the legacy designs/<plat>/<design>/config.mk path for projects
# whose backend was driven by hand or by an older run_orfs.sh.
ORFS_DESIGN_DIR="$FLOW_DIR/designs/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
ORFS_CONFIG="$ORFS_DESIGN_DIR/config.mk"
if [[ ! -f "$ORFS_CONFIG" ]]; then
  ORFS_DESIGN_DIR="$FLOW_DIR/designs/$PLATFORM/$DESIGN_NAME"
  ORFS_CONFIG="$ORFS_DESIGN_DIR/config.mk"
fi

if [[ ! -f "$ORFS_CONFIG" ]]; then
  echo "ERROR: ORFS config not found at $ORFS_CONFIG" >&2
  echo "Run the ORFS backend first: run_orfs.sh <project-dir>" >&2
  exit 1
fi

cd "$FLOW_DIR"

# Prevent env collision: ORFS Makefile uses SCRIPTS_DIR internally
unset SCRIPTS_DIR 2>/dev/null || true

# Auto-scale timeout based on design cell count, unless user explicitly set LVS_TIMEOUT
if [[ -z "${LVS_TIMEOUT:-}" ]]; then
  # Try to detect cell count from 6_report.json in ORFS logs
  _LVS_LOGS_DIR="$FLOW_DIR/logs/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
  if [[ ! -d "$_LVS_LOGS_DIR" ]]; then
    _LVS_LOGS_DIR="$FLOW_DIR/logs/$PLATFORM/$DESIGN_NAME"
  fi
  _REPORT_JSON=$(find "$_LVS_LOGS_DIR" -name "6_report.json" 2>/dev/null | head -1)
  # Fallback: check the project backend directory
  if [[ -z "$_REPORT_JSON" ]]; then
    _REPORT_JSON=$(find "$PROJECT_DIR/backend" -name "6_report.json" 2>/dev/null | sort | tail -1)
  fi
  _CELL_COUNT=0
  if [[ -n "$_REPORT_JSON" ]]; then
    _CELL_COUNT=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(int(d.get('finish__design__instance__count',0)))" "$_REPORT_JSON" 2>/dev/null || echo 0)
  fi
  if [[ "$_CELL_COUNT" -gt 250000 ]] 2>/dev/null; then
    LVS_TIMEOUT=28800
    echo "Auto-scaled LVS timeout to ${LVS_TIMEOUT}s (cell count: $_CELL_COUNT > 250K)"
  elif [[ "$_CELL_COUNT" -gt 175000 ]] 2>/dev/null; then
    LVS_TIMEOUT=14400
    echo "Auto-scaled LVS timeout to ${LVS_TIMEOUT}s (cell count: $_CELL_COUNT > 175K)"
  elif [[ "$_CELL_COUNT" -gt 100000 ]] 2>/dev/null; then
    LVS_TIMEOUT=7200
    echo "Auto-scaled LVS timeout to ${LVS_TIMEOUT}s (cell count: $_CELL_COUNT > 100K)"
  else
    LVS_TIMEOUT=3600
  fi
fi
echo "Timeout: ${LVS_TIMEOUT}s"

# Use setsid so timeout can kill the entire process group (prevents zombie klayout)
LVS_STATUS=0
set +e +o pipefail
setsid timeout --signal=TERM --kill-after=60 "$LVS_TIMEOUT" \
  make DESIGN_CONFIG="$ORFS_CONFIG" FLOW_VARIANT="$FLOW_VARIANT" lvs 2>&1 | tee /tmp/lvs_run_$$.log
LVS_STATUS=${PIPESTATUS[0]}
set -e -o pipefail
if [[ $LVS_STATUS -eq 124 || $LVS_STATUS -eq 137 ]]; then
  echo "ERROR: LVS timed out after ${LVS_TIMEOUT}s (exit code $LVS_STATUS)" >&2
  # Kill any orphaned klayout processes from this LVS run to prevent memory leaks
  pkill -9 -f "klayout.*${FLOW_VARIANT}.*lvs" 2>/dev/null || true
  sleep 2
fi

# Collect results
LVS_DIR="$PROJECT_DIR/lvs"
mkdir -p "$LVS_DIR"
cp /tmp/lvs_run_$$.log "$LVS_DIR/lvs_run.log" 2>/dev/null || true
rm -f /tmp/lvs_run_$$.log

LOGS_DIR="$FLOW_DIR/logs/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
if [[ ! -d "$LOGS_DIR" ]]; then
  LOGS_DIR="$FLOW_DIR/logs/$PLATFORM/$DESIGN_NAME"
fi

# Copy LVS artifacts
if [[ -f "$RESULTS_DIR/6_lvs.lvsdb" ]]; then
  cp "$RESULTS_DIR/6_lvs.lvsdb" "$LVS_DIR/" 2>/dev/null || true
fi
if [[ -f "$LOGS_DIR/6_lvs.log" ]]; then
  cp "$LOGS_DIR/6_lvs.log" "$LVS_DIR/" 2>/dev/null || true
fi
if [[ -f "$RESULTS_DIR/6_final.cdl" ]]; then
  cp "$RESULTS_DIR/6_final.cdl" "$LVS_DIR/" 2>/dev/null || true
fi

# Also copy to latest backend run
BACKEND_DIR="$PROJECT_DIR/backend"
if [[ -d "$BACKEND_DIR" ]]; then
  LATEST_RUN=$(ls -d "$BACKEND_DIR"/RUN_* 2>/dev/null | sort | tail -1)
  if [[ -n "$LATEST_RUN" ]]; then
    mkdir -p "$LATEST_RUN/lvs"
    cp "$LVS_DIR"/* "$LATEST_RUN/lvs/" 2>/dev/null || true
  fi
fi

# Parse LVS result
if [[ -f "$LVS_DIR/6_lvs.log" ]]; then
  # Check for failure first (more specific patterns take priority)
  if grep -qi "don't match\|do not match\|mismatch\|ERROR.*Netlists\|NOT match" "$LVS_DIR/6_lvs.log" 2>/dev/null; then
    echo ""
    echo "LVS FAILED — netlist mismatch detected"
    echo "Review $LVS_DIR/6_lvs.lvsdb for details"
  elif grep -qi "Congratulations.*match\|Netlists match\|circuits match" "$LVS_DIR/6_lvs.log" 2>/dev/null; then
    echo ""
    echo "LVS CLEAN — netlists match"
  else
    echo ""
    echo "LVS completed — check logs for detailed results"
  fi
else
  echo ""
  echo "LVS completed but no log found"
fi

echo "Results: $LVS_DIR"
exit $LVS_STATUS
