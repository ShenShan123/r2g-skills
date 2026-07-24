#!/usr/bin/env bash
set -euo pipefail

# usage: run_magic_drc.sh <project-dir> [platform]
# Runs Magic DRC on a completed ORFS backend run.
# Alternative to KLayout-based run_drc.sh — uses Magic's built-in DRC engine.
# Supported platforms: sky130hd, sky130hs (requires sky130A PDK at /opt/pdks/sky130A)
# Results are collected into <project-dir>/drc/

PROJECT_DIR="${1:-}"
PLATFORM="${2:-asap7}"
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
# Bounded process-group checker supervisor (RMD2-P0-01)
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_bounded_run.sh"
# Advisory or not, a cancelled/timed-out Magic must never survive as an orphan:
# reap the whole checker session on any exit path (same contract as run_drc.sh).
trap 'r2g_bounded_cleanup' EXIT
trap 'r2g_bounded_cleanup; exit 130' INT
trap 'r2g_bounded_cleanup; exit 143' TERM

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
# NOTE: run on a flattened ORFS P&R GDS, Magic re-checks foundry std-cell internal
# geometry + cell-abutment mcon/li spacing, so counts are typically MUCH higher than
# the ORFS KLayout sky130hd.lydrc deck (which is what the loop actually signs off on).
# See references/failure-patterns.md "Magic DRC Failure".
gds read "$GDS_FILE"
load "$DESIGN_NAME"
select top cell
drc catchup
# 'drc count total' PRINTS "Total DRC errors found: N" to stdout — parsed by the shell
# below (version-robust). It does NOT return a usable Tcl value, so never assign from it.
drc count total

# Per-rule breakdown. 'drc listall why' returns {rule {box box ...} rule {box ...} ...}:
# the 2nd item of each pair is a LIST OF BOXES, not a number. The pre-fix script did
# 'expr {\$total + \$count}' on that list and crashed ("non-numeric string as operand of +").
# Count the list length instead of adding the list as an integer.
set fout [open "$DRC_REPORT" w]
puts \$fout "Design: $DESIGN_NAME"
puts \$fout "Platform: $PLATFORM"
puts \$fout "GDS: $GDS_FILE"
puts \$fout "Tool: Magic"
puts \$fout "---"
if {[catch {drc listall why} why_list]} { set why_list {} }
set total 0
foreach {rule boxes} \$why_list {
    set n [llength \$boxes]
    puts \$fout "VIOLATION: \$rule (count=\$n)"
    set total [expr {\$total + \$n}]
}
puts \$fout "---"
puts \$fout "Total violations (per-rule box sum): \$total"
close \$fout

quit -noprompt
MAGIC_EOF

# Run Magic in batch mode
MAGIC_TIMEOUT="${MAGIC_TIMEOUT:-3600}"
echo "Timeout: ${MAGIC_TIMEOUT}s"

# Bounded session supervisor (RMD2-P0-01, 2026-07-24 — the last `timeout | tee`
# in the flow scripts): the old pipeline let a TERM-ignoring Magic descendant
# hold the tee pipe open past expiry; r2g_bounded_run logs directly to DRC_LOG,
# TERM→grace→KILLs the whole group, and reaps any session survivor.
DRC_STATUS=0
set +e
r2g_bounded_run "$MAGIC_TIMEOUT" "${MAGIC_KILL_GRACE:-60}" "$DRC_LOG" \
  "$MAGIC_EXE" -dnull -noconsole -T "$MAGIC_TECH" "$DRC_TCL"
DRC_STATUS=$?
set -e
tail -n 25 "$DRC_LOG" 2>/dev/null || true
if [[ $DRC_STATUS -eq 124 ]]; then
  echo "ERROR: Magic DRC timed out after ${MAGIC_TIMEOUT}s" >&2
fi

# Parse results — Magic prints "Total DRC errors found: N" (its authoritative error-tile
# count). Guard that the parsed value is numeric so we NEVER emit invalid JSON: the pre-fix
# script leaked the literal "magic_drc_total_violations:" (an empty Tcl var) into the JSON
# total_violations field, producing unparseable output.
VIOLATION_COUNT=0
if [[ -f "$DRC_LOG" ]]; then
  # `|| true`: under the script-wide pipefail a log WITHOUT the count line
  # (timeout/crash before Magic printed it) made this grep abort the whole
  # script at exit 1 — before the JSON below was ever written. Tolerate the
  # no-match; the numeric guard fail-closes the empty value to 0.
  COUNT_LINE=$(grep -i "Total DRC errors found:" "$DRC_LOG" 2>/dev/null | tail -1 || true)
  if [[ -n "$COUNT_LINE" ]]; then
    VIOLATION_COUNT=$(echo "$COUNT_LINE" | awk '{print $NF}')
  fi
fi
# Fail-closed to a numeric value (invalid/empty parse -> 0, with a loud warning).
if ! [[ "$VIOLATION_COUNT" =~ ^[0-9]+$ ]]; then
  echo "WARNING: could not parse a numeric Magic DRC count from $DRC_LOG; recording 0" >&2
  VIOLATION_COUNT=0
fi

# Write count report (compatible with extract_drc.py numeric parser)
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
