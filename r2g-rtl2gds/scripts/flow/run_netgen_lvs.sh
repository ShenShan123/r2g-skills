#!/usr/bin/env bash
set -euo pipefail

# usage: run_netgen_lvs.sh <project-dir> [platform]
# Runs Netgen LVS on a completed ORFS backend run.
# Alternative to KLayout-based run_lvs.sh — uses Netgen for layout-vs-schematic.
# Workflow: Magic extracts SPICE from GDS, then Netgen compares against Verilog netlist.
# Supported platforms: sky130hd, sky130hs (requires sky130A PDK at /opt/pdks/sky130A)
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
# Auto-detect ORFS + tools (honors ORFS_ROOT / PDK_ROOT / *_EXE env overrides)
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_env.sh"

if [[ -z "${ORFS_ROOT:-}" || ! -d "$FLOW_DIR" ]]; then
  echo "ERROR: ORFS not found. Set ORFS_ROOT to your OpenROAD-flow-scripts checkout." >&2
  exit 1
fi

if [[ -z "$PROJECT_DIR" ]]; then
  echo "usage: run_netgen_lvs.sh <project-dir> [platform]" >&2
  exit 1
fi

PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
CONFIG_MK="$PROJECT_DIR/constraints/config.mk"

if [[ ! -f "$CONFIG_MK" ]]; then
  echo "ERROR: config.mk not found at $CONFIG_MK" >&2
  exit 1
fi

# Verify tools are installed (honor MAGIC_EXE / NETGEN_EXE overrides)
if [[ -z "${MAGIC_EXE:-}" ]] && ! command -v magic &>/dev/null; then
  echo "ERROR: magic not found. Set MAGIC_EXE or install magic." >&2
  exit 1
fi
: "${MAGIC_EXE:=$(command -v magic)}"

if [[ -z "${NETGEN_EXE:-}" ]]; then
  if command -v netgen &>/dev/null; then
    NETGEN_EXE="$(command -v netgen)"
  elif command -v netgen-lvs &>/dev/null; then
    NETGEN_EXE="$(command -v netgen-lvs)"
  else
    echo "ERROR: netgen/netgen-lvs not found. Set NETGEN_EXE or install netgen." >&2
    exit 1
  fi
fi
NETGEN_CMD="$NETGEN_EXE"

DESIGN_NAME=$(grep 'DESIGN_NAME' "$CONFIG_MK" | head -1 | sed 's/.*=\s*//' | tr -d ' ')

# Map platform to PDK files
MAGIC_TECH=""
NETGEN_SETUP=""
case "$PLATFORM" in
  sky130hd|sky130hs)
    MAGIC_TECH="$PDK_ROOT/sky130A/libs.tech/magic/sky130A.tech"
    NETGEN_SETUP="$PDK_ROOT/sky130A/libs.tech/netgen/sky130A_setup.tcl"
    ;;
  *)
    echo "WARNING: Netgen LVS not supported for platform $PLATFORM" >&2
    echo "Supported platforms: sky130hd, sky130hs" >&2
    LVS_DIR="$PROJECT_DIR/lvs"
    mkdir -p "$LVS_DIR"
    echo '{"tool": "netgen", "status": "skipped", "reason": "Netgen LVS not supported for platform '"$PLATFORM"'"}' > "$LVS_DIR/netgen_lvs_result.json"
    echo "Netgen LVS skipped: no setup file for $PLATFORM"
    exit 0
    ;;
esac

if [[ ! -f "$MAGIC_TECH" ]]; then
  echo "ERROR: Magic tech file not found at $MAGIC_TECH" >&2
  exit 1
fi

if [[ ! -f "$NETGEN_SETUP" ]]; then
  echo "ERROR: Netgen setup file not found at $NETGEN_SETUP" >&2
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

# Find the Verilog netlist (gate-level from synthesis or ORFS)
VERILOG_NETLIST=""
# Try ORFS result first
for candidate in \
  "$RESULTS_DIR/6_final.v" \
  "$RESULTS_DIR/6_1_fill.v" \
  "$RESULTS_DIR/5_route.v" \
  "$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME/base/6_final.v" \
  "$PROJECT_DIR/synth/synth_output.v"; do
  if [[ -f "$candidate" ]]; then
    VERILOG_NETLIST="$candidate"
    break
  fi
done

if [[ -z "$VERILOG_NETLIST" ]]; then
  echo "ERROR: No Verilog netlist found for LVS comparison" >&2
  echo "Searched: $RESULTS_DIR/6_final.v, synth_output.v" >&2
  exit 1
fi

echo "Running Netgen LVS for design: $DESIGN_NAME"
echo "Platform: $PLATFORM"
echo "GDS: $GDS_FILE"
echo "Netlist: $VERILOG_NETLIST"
echo "Tech: $MAGIC_TECH"
echo "Netgen setup: $NETGEN_SETUP"

LVS_DIR="$PROJECT_DIR/lvs"
mkdir -p "$LVS_DIR"

# Resolve the standard-cell SPICE library for the schematic side of LVS.
# Without this, Netgen reads 6_final.v with the std cells as hollow black boxes
# ("Circuit sky130_fd_sc_hd__<cell> contains no devices") and nets explode, giving a
# spurious mismatch even when device counts match. See references/failure-patterns.md
# "sky130 LVS" (2026-06-11). Production fix: load the cell library into the schematic
# circuit so both sides expand to transistors.
case "$PLATFORM" in
  sky130hd) SC_LIB_NAME="sky130_fd_sc_hd" ;;
  sky130hs) SC_LIB_NAME="sky130_fd_sc_hs" ;;
esac
SC_SPICE="$PDK_ROOT/sky130A/libs.ref/$SC_LIB_NAME/spice/$SC_LIB_NAME.spice"
if [[ ! -f "$SC_SPICE" ]]; then
  echo "WARNING: std-cell SPICE not found at $SC_SPICE — schematic cells will be hollow" >&2
  SC_SPICE=""
fi

# Step 1: Extract SPICE netlist from GDS using Magic (hierarchical — no flatten, so
# each std cell stays a subckt that matches the cell-library definition on the
# schematic side). Run Magic inside a scratch dir so its per-cell *.ext files land
# there instead of polluting the caller's CWD (repo root) — ~50 stray files/design.
EXTRACTED_SPICE="$LVS_DIR/extracted.spice"
EXTRACT_TCL="$LVS_DIR/run_magic_extract.tcl"
EXTRACT_LOG="$LVS_DIR/magic_extract.log"
EXT_SCRATCH="$LVS_DIR/magic_ext"
rm -rf "$EXT_SCRATCH"; mkdir -p "$EXT_SCRATCH"

cat > "$EXTRACT_TCL" << MAGIC_EOF
gds read "$GDS_FILE"
load "$DESIGN_NAME"
select top cell
extract all
ext2spice lvs
ext2spice -o "$EXTRACTED_SPICE"
quit -noprompt
MAGIC_EOF

NETGEN_TIMEOUT="${NETGEN_TIMEOUT:-3600}"
echo "Timeout: ${NETGEN_TIMEOUT}s per step"
echo "Step 1: Extracting SPICE netlist from GDS with Magic..."
( cd "$EXT_SCRATCH" && timeout --signal=TERM --kill-after=30 "$NETGEN_TIMEOUT" \
    "$MAGIC_EXE" -dnull -noconsole -T "$MAGIC_TECH" "$EXTRACT_TCL" ) 2>&1 | tee "$EXTRACT_LOG"

if [[ ! -f "$EXTRACTED_SPICE" ]]; then
  echo "ERROR: Magic SPICE extraction failed — $EXTRACTED_SPICE not created" >&2
  echo '{"tool": "netgen", "status": "error", "reason": "Magic SPICE extraction failed"}' > "$LVS_DIR/netgen_lvs_result.json"
  exit 1
fi
echo "Extracted: $EXTRACTED_SPICE ($(wc -l < "$EXTRACTED_SPICE") lines)"

# Step 2: Run Netgen LVS comparison
NETGEN_LOG="$LVS_DIR/netgen_lvs.log"
NETGEN_REPORT="$LVS_DIR/netgen_lvs.rpt"

echo "Step 2: Running Netgen LVS comparison..."
# Drive Netgen from a TCL script (not -batch lvs) so we can load the std-cell SPICE
# library into the *schematic* circuit (circuit2 = the Verilog netlist). This is the
# OpenLane-style sky130 LVS pattern: readnet the cell library into circuit2 so its
# black-box cells expand to transistors, matching the layout-extracted circuit1.
NETGEN_TCL="$LVS_DIR/run_netgen_lvs.tcl"
if [[ -n "$SC_SPICE" ]]; then
  # Load the std-cell SPICE library FIRST so circuit2 already holds the transistor-level
  # cell definitions; then read the Verilog netlist INTO THE SAME circuit handle so its
  # cell instances bind to those definitions. (Reading the Verilog first makes netgen
  # create empty placeholder cells that shadow a later library read — the cause of the
  # "Circuit sky130_fd_sc_hd__<cell> contains no devices" mismatch.)
  cat > "$NETGEN_TCL" << NETGEN_EOF
set circuit1 [readnet spice "$EXTRACTED_SPICE"]
set circuit2 [readnet spice "$SC_SPICE"]
readnet verilog "$VERILOG_NETLIST" \$circuit2
lvs "\$circuit1 $DESIGN_NAME" "\$circuit2 $DESIGN_NAME" "$NETGEN_SETUP" "$NETGEN_REPORT"
NETGEN_EOF
else
  cat > "$NETGEN_TCL" << NETGEN_EOF
set circuit1 [readnet spice "$EXTRACTED_SPICE"]
set circuit2 [readnet verilog "$VERILOG_NETLIST"]
lvs "\$circuit1 $DESIGN_NAME" "\$circuit2 $DESIGN_NAME" "$NETGEN_SETUP" "$NETGEN_REPORT"
NETGEN_EOF
fi

LVS_STATUS=0
set +e +o pipefail
timeout --signal=TERM --kill-after=60 "$NETGEN_TIMEOUT" $NETGEN_CMD -batch source "$NETGEN_TCL" 2>&1 | tee "$NETGEN_LOG"
LVS_STATUS=${PIPESTATUS[0]}
set -e -o pipefail

# Parse results
LVS_RESULT="unknown"
MATCH_STATUS="unknown"
if [[ -f "$NETGEN_LOG" ]]; then
  if grep -qi "Circuits match uniquely\|Result: PASS\|netlists match" "$NETGEN_LOG" 2>/dev/null; then
    LVS_RESULT="clean"
    MATCH_STATUS="match"
  elif grep -qi "mismatch\|NOT match\|Result: FAIL\|netlists do not match" "$NETGEN_LOG" 2>/dev/null; then
    LVS_RESULT="mismatch"
    MATCH_STATUS="mismatch"
  fi
fi

# Also check the report file
if [[ -f "$NETGEN_REPORT" ]] && [[ "$MATCH_STATUS" == "unknown" ]]; then
  if grep -qi "Circuits match\|PASS" "$NETGEN_REPORT" 2>/dev/null; then
    LVS_RESULT="clean"
    MATCH_STATUS="match"
  elif grep -qi "mismatch\|FAIL" "$NETGEN_REPORT" 2>/dev/null; then
    LVS_RESULT="mismatch"
    MATCH_STATUS="mismatch"
  fi
fi

# Write JSON result
cat > "$LVS_DIR/netgen_lvs_result.json" << JSON_EOF
{
  "tool": "netgen",
  "design": "$DESIGN_NAME",
  "platform": "$PLATFORM",
  "status": "$LVS_RESULT",
  "match": "$MATCH_STATUS",
  "extracted_spice": "$EXTRACTED_SPICE",
  "reference_netlist": "$VERILOG_NETLIST",
  "report_file": "$NETGEN_REPORT",
  "log_file": "$NETGEN_LOG"
}
JSON_EOF

# Clean up Magic temp files
rm -f "$LVS_DIR"/*.ext 2>/dev/null || true

# Copy to latest backend run
BACKEND_DIR="$PROJECT_DIR/backend"
if [[ -d "$BACKEND_DIR" ]]; then
  LATEST_RUN=$(ls -d "$BACKEND_DIR"/RUN_* 2>/dev/null | sort | tail -1)
  if [[ -n "$LATEST_RUN" ]]; then
    mkdir -p "$LATEST_RUN/lvs"
    cp "$LVS_DIR"/netgen_lvs* "$LATEST_RUN/lvs/" 2>/dev/null || true
  fi
fi

echo ""
if [[ "$LVS_RESULT" == "clean" ]]; then
  echo "Netgen LVS CLEAN — circuits match"
elif [[ "$LVS_RESULT" == "mismatch" ]]; then
  echo "Netgen LVS FAILED — netlist mismatch detected"
  echo "Review $NETGEN_REPORT for details"
else
  echo "Netgen LVS completed — check $NETGEN_LOG for results"
fi
echo "Results: $LVS_DIR"
exit $LVS_STATUS
