#!/usr/bin/env bash
set -euo pipefail

# usage: run_magic_drc.sh <project-dir> [platform]
# Runs Magic DRC on a completed ORFS backend run.
# Alternative to KLayout-based run_drc.sh — uses Magic's built-in DRC engine.
# Supported platforms: sky130hd, sky130hs (requires sky130A PDK at /opt/pdks/sky130A)
# Results are collected into <project-dir>/drc/

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
# Auto-detect ORFS + tools (honors ORFS_ROOT / PDK_ROOT / *_EXE env overrides)
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_env.sh"

if [[ -z "${ORFS_ROOT:-}" || ! -d "$FLOW_DIR" ]]; then
  echo "ERROR: ORFS not found. Set ORFS_ROOT to your OpenROAD-flow-scripts checkout." >&2
  exit 1
fi

if [[ -z "$PROJECT_DIR" ]]; then
  echo "usage: run_magic_drc.sh <project-dir> [platform]" >&2
  exit 1
fi

PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
CONFIG_MK="$PROJECT_DIR/constraints/config.mk"

if [[ ! -f "$CONFIG_MK" ]]; then
  echo "ERROR: config.mk not found at $CONFIG_MK" >&2
  exit 1
fi

# Verify Magic is installed
if [[ -z "${MAGIC_EXE:-}" ]] && ! command -v magic &>/dev/null; then
  echo "ERROR: magic not found. Install Magic or set MAGIC_EXE to the magic binary." >&2
  exit 1
fi
: "${MAGIC_EXE:=$(command -v magic)}"

DESIGN_NAME=$(grep 'DESIGN_NAME' "$CONFIG_MK" | head -1 | sed 's/.*=\s*//' | tr -d ' ')

# Map platform to Magic tech file
MAGIC_TECH=""
case "$PLATFORM" in
  sky130hd|sky130hs)
    MAGIC_TECH="$PDK_ROOT/sky130A/libs.tech/magic/sky130A.tech"
    ;;
  *)
    echo "WARNING: Magic DRC not supported for platform $PLATFORM" >&2
    echo "Supported platforms: sky130hd, sky130hs" >&2
    DRC_DIR="$PROJECT_DIR/drc"
    mkdir -p "$DRC_DIR"
    echo '{"tool": "magic", "status": "skipped", "reason": "Magic DRC not supported for platform '"$PLATFORM"'"}' > "$DRC_DIR/magic_drc_result.json"
    echo "Magic DRC skipped: no tech file for $PLATFORM"
    exit 0
    ;;
esac

if [[ ! -f "$MAGIC_TECH" ]]; then
  echo "ERROR: Magic tech file not found at $MAGIC_TECH" >&2
  echo "Install sky130 PDK: download tech files to $PDK_ROOT/sky130A/" >&2
  exit 1
fi

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

echo "Running Magic DRC for design: $DESIGN_NAME"
echo "Platform: $PLATFORM"
echo "GDS: $GDS_FILE"
echo "Tech: $MAGIC_TECH"

DRC_DIR="$PROJECT_DIR/drc"
mkdir -p "$DRC_DIR"

# Generate Magic DRC Tcl script
DRC_TCL="$DRC_DIR/run_magic_drc.tcl"
DRC_REPORT="$DRC_DIR/magic_drc.rpt"
DRC_LOG="$DRC_DIR/magic_drc.log"

cat > "$DRC_TCL" << MAGIC_EOF
# Magic DRC script — auto-generated
gds read "$GDS_FILE"
load "$DESIGN_NAME"
select top cell
drc catchup
drc count
set drc_count [drc count total]
puts "magic_drc_total_violations: \$drc_count"

# Write DRC report
set fout [open "$DRC_REPORT" w]
puts \$fout "Design: $DESIGN_NAME"
puts \$fout "Platform: $PLATFORM"
puts \$fout "GDS: $GDS_FILE"
puts \$fout "Tool: Magic [version]"
puts \$fout "---"
set why_list [drc listall why]
set total 0
foreach {rule count} \$why_list {
    puts \$fout "VIOLATION: \$rule (\$count)"
    set total [expr {\$total + \$count}]
}
puts \$fout "---"
puts \$fout "Total violations: \$total"
close \$fout

quit -noprompt
MAGIC_EOF

# Run Magic in batch mode
MAGIC_TIMEOUT="${MAGIC_TIMEOUT:-3600}"
echo "Timeout: ${MAGIC_TIMEOUT}s"

DRC_STATUS=0
set +e +o pipefail
timeout --signal=TERM --kill-after=60 "$MAGIC_TIMEOUT" \
  magic -dnull -noconsole -T "$MAGIC_TECH" "$DRC_TCL" 2>&1 | tee "$DRC_LOG"
DRC_STATUS=${PIPESTATUS[0]}
set -e -o pipefail
if [[ $DRC_STATUS -eq 124 ]]; then
  echo "ERROR: Magic DRC timed out after ${MAGIC_TIMEOUT}s" >&2
fi

# Parse results
VIOLATION_COUNT=0
if [[ -f "$DRC_LOG" ]]; then
  # Extract count from magic output
  COUNT_LINE=$(grep "magic_drc_total_violations:" "$DRC_LOG" 2>/dev/null | tail -1)
  if [[ -n "$COUNT_LINE" ]]; then
    VIOLATION_COUNT=$(echo "$COUNT_LINE" | awk '{print $NF}')
  fi
fi

# Write count report (compatible with extract_drc.py)
echo "$VIOLATION_COUNT" > "$DRC_DIR/magic_drc_count.rpt"

# Write JSON result
cat > "$DRC_DIR/magic_drc_result.json" << JSON_EOF
{
  "tool": "magic",
  "design": "$DESIGN_NAME",
  "platform": "$PLATFORM",
  "status": "$([ "$VIOLATION_COUNT" = "0" ] && echo "clean" || echo "violations")",
  "total_violations": $VIOLATION_COUNT,
  "report_file": "$DRC_REPORT",
  "log_file": "$DRC_LOG"
}
JSON_EOF

# Also copy to latest backend run
BACKEND_DIR="$PROJECT_DIR/backend"
if [[ -d "$BACKEND_DIR" ]]; then
  LATEST_RUN=$(ls -d "$BACKEND_DIR"/RUN_* 2>/dev/null | sort | tail -1)
  if [[ -n "$LATEST_RUN" ]]; then
    mkdir -p "$LATEST_RUN/drc"
    cp "$DRC_DIR"/magic_drc* "$LATEST_RUN/drc/" 2>/dev/null || true
  fi
fi

echo ""
if [[ "$VIOLATION_COUNT" == "0" ]]; then
  echo "Magic DRC CLEAN"
else
  echo "Magic DRC: $VIOLATION_COUNT violations found"
  echo "Review $DRC_REPORT for details"
fi
echo "Results: $DRC_DIR"
exit $DRC_STATUS
