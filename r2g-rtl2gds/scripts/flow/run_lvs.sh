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

# Re-stage project artifacts into ORFS workspace if missing. This makes the
# script idempotent across re-runs even if the ORFS scratch dirs were cleaned.
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_restage_for_signoff.sh"
RESULTS_DIR="$ORFS_RESULTS_DIR"

GDS_FILE="$RESULTS_DIR/6_final.gds"
if [[ ! -f "$GDS_FILE" ]]; then
  echo "ERROR: No 6_final.gds found at $GDS_FILE after restage" >&2
  echo "Re-run the ORFS backend first: run_orfs.sh <project-dir>" >&2
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

# ORFS_DESIGN_DIR / ORFS_CONFIG were set up by _restage_for_signoff.sh above.
ORFS_CONFIG="$ORFS_DESIGN_DIR/config.mk"
if [[ ! -f "$ORFS_CONFIG" ]]; then
  echo "ERROR: failed to stage ORFS config at $ORFS_CONFIG" >&2
  exit 1
fi

cd "$FLOW_DIR"

# Prevent env collision: ORFS Makefile uses SCRIPTS_DIR internally
unset SCRIPTS_DIR 2>/dev/null || true

# Detect design cell count from 6_report.json — drives BOTH timeout auto-scaling
# and crash-retry gating, so compute it unconditionally (init to 0 for set -u safety).
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

# Auto-scale timeout unless the user explicitly set LVS_TIMEOUT. KLayout's layout
# netlist EXTRACTION (not just the compare) is super-linear: ~2700s @51K and
# ~10200s @62K cells (verified 2026-06-03), so the old 3600s default SIGTERM'd every
# >=50K design *mid-extraction* (mis-reported as "incomplete"). Tiers now clear the
# extraction wall. See references/failure-patterns.md "LVS incomplete is mostly a
# comparer bug, not honest slowness".
if [[ -z "${LVS_TIMEOUT:-}" ]]; then
  if   [[ "$_CELL_COUNT" -gt 250000 ]] 2>/dev/null; then LVS_TIMEOUT=28800
  elif [[ "$_CELL_COUNT" -gt 100000 ]] 2>/dev/null; then LVS_TIMEOUT=21600
  elif [[ "$_CELL_COUNT" -gt  50000 ]] 2>/dev/null; then LVS_TIMEOUT=14400
  else                                                   LVS_TIMEOUT=5400
  fi
  echo "Auto-scaled LVS timeout to ${LVS_TIMEOUT}s (cell count: $_CELL_COUNT)"
fi
echo "Timeout: ${LVS_TIMEOUT}s"

# Where ORFS writes 6_lvs.log — needed below for crash-retry detection AND for the
# result-collection copy further down. Computed once, here, before the run loop.
LOGS_DIR="$FLOW_DIR/logs/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
if [[ ! -d "$LOGS_DIR" ]]; then
  LOGS_DIR="$FLOW_DIR/logs/$PLATFORM/$DESIGN_NAME"
fi

# KLayout 0.30.7's netlist comparer has a NON-DETERMINISTIC SIGSEGV heisenbug in
# db::NetlistCrossReference::sort_circuit()/gen_log_entry() (it reads a corrupted Net
# pointer while sorting the cross-reference, AFTER extraction succeeds). A surviving
# run yields the TRUE verdict — clean OR fail — so retry past the crash. Validated
# 2026-06-03: fifo_basic/verilog_axi_axi_fifo_wr -> clean; aximwr2wbsp/core_usb_host_top
# -> (symmetric) fail. Single-thread, verbose(false), tcmalloc do NOT fix it; `flat`
# mode dodges the crash but yields garbage mismatches. See references/failure-patterns.md
# "LVS KLayout sort_circuit/gen_log_entry SIGSEGV (non-deterministic)".
# Gated low for very large designs — each retry re-runs `make lvs`, which re-extracts.
if [[ -z "${LVS_CRASH_RETRIES:-}" ]]; then
  if [[ "$_CELL_COUNT" -gt 150000 ]] 2>/dev/null; then LVS_CRASH_RETRIES=1; else LVS_CRASH_RETRIES=4; fi
fi

# sky130 CDL slash-fix (PD issue: sky130hd/sky130hs LVS pin-count mismatch).
# KLayout's CDL reader counts the ' / ' node/model separator in the platform CDL's
# *_macro_sparecell subckt instance lines as an extra pin -> "6 expected, got 7" and
# LVS aborts on EVERY sky130 design. Fix: feed `make lvs` a slash-normalized copy of
# the platform CDL (separator removed; netlist semantics unchanged). Validated against
# cordic (LVS clean). Command-line CDL_FILE= overrides the platform config.mk's plain
# `export CDL_FILE`; we SKIP injection when the project config.mk already defines its
# own (override) CDL_FILE, so an explicit operator choice always wins.
# See references/failure-patterns.md "sky130 LVS macro_sparecell slash pin-count".
_CDL_MAKE_ARGS=()
if [[ "$PLATFORM" == "sky130hd" || "$PLATFORM" == "sky130hs" ]]; then
  _PLAT_CDL="$FLOW_DIR/platforms/$PLATFORM/cdl/$PLATFORM.cdl"
  if grep -qE '^[[:space:]]*(override[[:space:]]+)?export[[:space:]]+CDL_FILE' "$CONFIG_MK"; then
    echo "sky130 LVS: project config.mk sets its own CDL_FILE -> not injecting slash-fix"
  elif [[ -f "$_PLAT_CDL" ]]; then
    _CDL_FIX="$PROJECT_DIR/lvs/${PLATFORM}_cdl_fix.cdl"
    mkdir -p "$PROJECT_DIR/lvs"
    # Two KLayout CDL-reader fixes for the sky130 platform netlist:
    #  1) ' / ' node/model separator in *_macro_sparecell -> drop (pin-count "6->7").
    #  2) zero-ohm 'short' resistors in tie/power cells (conb_1 etc.) -> '0'. KLayout's
    #     default SPICE reader rejects the non-numeric 'short' value ("Can't find a value
    #     for a R, C or L device"); a numeric 0 parses and KLayout's device simplification
    #     reduces a 0-ohm resistor to a net short (the layout rule extracts no resistors).
    sed -E -e 's| / | |g' -e 's|^([rclRCL][A-Za-z0-9_]* [^ ]+ [^ ]+) short$|\1 0|' \
        "$_PLAT_CDL" > "$_CDL_FIX"
    _CDL_MAKE_ARGS=("CDL_FILE=$_CDL_FIX")
    echo "sky130 LVS: injected fixed CDL (slash + short-resistor) -> $_CDL_FIX"
  fi
  # Use the r2g-corrected LVS rule (adds a SPICE-reader delegate that shorts the
  # now-0-ohm tie/power resistors so they are not unmatched schematic-only devices;
  # the stock rule extracts MOS only). Override KLAYOUT_LVS_FILE on the make line.
  _R2G_LVS_RULE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/assets/platforms/$PLATFORM/lvs/${PLATFORM}_r2g.lylvs"
  if [[ -f "$_R2G_LVS_RULE" ]]; then
    _CDL_MAKE_ARGS+=("KLAYOUT_LVS_FILE=$_R2G_LVS_RULE")
    echo "sky130 LVS: using r2g-corrected rule -> $_R2G_LVS_RULE"
  fi
fi

# Use setsid so timeout can kill the entire process group (prevents zombie klayout)
LVS_STATUS=0
for _attempt in $(seq 1 "$LVS_CRASH_RETRIES"); do
  set +e +o pipefail
  setsid timeout --signal=TERM --kill-after=60 "$LVS_TIMEOUT" \
    make DESIGN_CONFIG="$ORFS_CONFIG" FLOW_VARIANT="$FLOW_VARIANT" "${_CDL_MAKE_ARGS[@]}" lvs 2>&1 | tee /tmp/lvs_run_$$.log
  LVS_STATUS=${PIPESTATUS[0]}
  set -e -o pipefail

  # Reap orphaned klayout from THIS run on ANY nonzero exit. A SIGSEGV gives make
  # "Error 11" (exit 2) — the old 124/137-only cleanup left a multi-GB klayout child
  # still spinning (a real leak observed 2026-06-03). Always reap on failure.
  if [[ $LVS_STATUS -ne 0 ]]; then
    pkill -9 -f "klayout.*${FLOW_VARIANT}.*lvs" 2>/dev/null || true
    pkill -9 -f "klayout.*${DESIGN_NAME}.*FreePDK45" 2>/dev/null || true
    sleep 2
  fi

  # A timeout/external-kill will just recur — do not spend retries on it.
  if [[ $LVS_STATUS -eq 124 || $LVS_STATUS -eq 137 ]]; then
    echo "ERROR: LVS timed out after ${LVS_TIMEOUT}s (exit code $LVS_STATUS)" >&2
    break
  fi

  # Crash signature in the ORFS 6_lvs.log or the tee'd run log -> retry for a survivor.
  if grep -qa "Signal number" "$LOGS_DIR/6_lvs.log" /tmp/lvs_run_$$.log 2>/dev/null; then
    if [[ $_attempt -lt $LVS_CRASH_RETRIES ]]; then
      echo "LVS crashed (KLayout sort_circuit SIGSEGV heisenbug); retry $_attempt/$LVS_CRASH_RETRIES ..." >&2
      continue
    fi
    echo "ERROR: LVS still crashing after $LVS_CRASH_RETRIES attempts (KLayout 0.30.7 comparer bug, no newer build on host)" >&2
    break
  fi

  # No crash this attempt -> the verdict (clean or fail) is trustworthy. Stop.
  break
done

# Collect results
LVS_DIR="$PROJECT_DIR/lvs"
mkdir -p "$LVS_DIR"
cp /tmp/lvs_run_$$.log "$LVS_DIR/lvs_run.log" 2>/dev/null || true
rm -f /tmp/lvs_run_$$.log
# Drop any stale skip-marker from a prior `no rules available` run — once we
# have a real lvs_run.log/6_lvs.log the skip marker is no longer authoritative.
rm -f "$LVS_DIR/lvs_result.json"

# LOGS_DIR was computed before the run loop (used for crash-retry detection).

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
