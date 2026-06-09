#!/usr/bin/env bash
set -euo pipefail

# usage: run_orfs.sh <project-dir> [platform] [flow_variant]
# Runs OpenROAD-flow-scripts backend for the given project.
# Expects <project-dir>/constraints/config.mk and constraint.sdc to exist.
# Results are collected back into <project-dir>/backend/
# Optional flow_variant (default: derived from project dir) isolates ORFS work directories.
# Set ORFS_TIMEOUT (seconds) to limit runtime (default: 7200 = 2 hours).
# Set ORFS_MAX_CPUS to limit CPU cores (default: all available).

PROJECT_DIR="${1:-}"
PLATFORM="${2:-nangate45}"
# Derive FLOW_VARIANT from project directory basename to isolate ORFS work dirs
# per project config (e.g., swerv_cfg1 vs swerv_cfg2 get separate directories).
# This prevents directory collisions when multiple configs share the same DESIGN_NAME.
if [[ -n "${3:-}" ]]; then
  FLOW_VARIANT="$3"
elif [[ -n "$PROJECT_DIR" && -d "$PROJECT_DIR" ]]; then
  FLOW_VARIANT="$(basename "$(cd "$PROJECT_DIR" && pwd)")"
else
  FLOW_VARIANT="base"
fi
FROM_STAGE="${FROM_STAGE:-}"

# Auto-detect ORFS + tools (honors ORFS_ROOT / *_EXE env overrides)
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_env.sh"

if [[ -z "${ORFS_ROOT:-}" || ! -d "$FLOW_DIR" ]]; then
  echo "ERROR: ORFS not found. Set ORFS_ROOT to your OpenROAD-flow-scripts checkout." >&2
  exit 1
fi

if [[ -z "$PROJECT_DIR" ]]; then
  echo "usage: run_orfs.sh <project-dir> [platform]" >&2
  exit 1
fi

PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
CONFIG_MK="$PROJECT_DIR/constraints/config.mk"
SDC_FILE="$PROJECT_DIR/constraints/constraint.sdc"

if [[ ! -f "$CONFIG_MK" ]]; then
  echo "ERROR: config.mk not found at $CONFIG_MK" >&2
  exit 1
fi

if [[ ! -f "$SDC_FILE" ]]; then
  echo "ERROR: constraint.sdc not found at $SDC_FILE" >&2
  exit 1
fi

# Create a design directory inside ORFS for this project.
# Key fix: include FLOW_VARIANT in the path so concurrent runs that share
# DESIGN_NAME (e.g. all ICCAD benchmarks use DESIGN_NAME=top) do not overwrite
# each other's config.mk at the shared $FLOW_DIR/designs/<platform>/<name>/ path.
DESIGN_NAME=$(grep 'DESIGN_NAME' "$CONFIG_MK" | head -1 | sed 's/.*=\s*//' | tr -d ' ')
ORFS_DESIGN_DIR="$FLOW_DIR/designs/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
mkdir -p "$ORFS_DESIGN_DIR"

# Copy config.mk and constraint.sdc
cp "$CONFIG_MK" "$ORFS_DESIGN_DIR/config.mk"
cp "$SDC_FILE" "$ORFS_DESIGN_DIR/constraint.sdc"

# Ensure RTL path in config.mk is absolute
# (The config.mk should already use absolute paths, but let's verify)
if grep -q 'VERILOG_FILES' "$ORFS_DESIGN_DIR/config.mk"; then
  echo "config.mk has VERILOG_FILES entry"
else
  echo "WARNING: config.mk missing VERILOG_FILES" >&2
fi

# Create a timestamp for this run
RUN_TAG="RUN_$(date +%Y-%m-%d_%H-%M-%S)"
echo "Starting ORFS run: $RUN_TAG"
echo "Design: $DESIGN_NAME"
echo "Platform: $PLATFORM"
echo "Flow variant: $FLOW_VARIANT"
echo "Config: $ORFS_DESIGN_DIR/config.mk"

# Run the ORFS flow
cd "$FLOW_DIR"

# Prevent env collision: ORFS Makefile uses SCRIPTS_DIR internally
unset SCRIPTS_DIR 2>/dev/null || true

if [[ -z "$FROM_STAGE" ]]; then
  echo "Cleaning previous ORFS state for variant=$FLOW_VARIANT ..."
  make DESIGN_CONFIG="$ORFS_DESIGN_DIR/config.mk" FLOW_VARIANT="$FLOW_VARIANT" clean_all 2>&1 | tail -5 || echo "WARNING: clean_all returned non-zero (may be first run)" >&2
else
  echo "Skipping clean_all (resuming from stage: $FROM_STAGE)"
fi

BACKEND_DIR="$PROJECT_DIR/backend/$RUN_TAG"
mkdir -p "$BACKEND_DIR"

# Timeout and CPU limit support
ORFS_TIMEOUT="${ORFS_TIMEOUT:-7200}"
MAKE_CMD="make DESIGN_CONFIG=\"$ORFS_DESIGN_DIR/config.mk\" FLOW_VARIANT=\"$FLOW_VARIANT\""

# Allow config.mk to opt into PLACE_FAST / ROUTE_FAST without requiring the
# caller to set the env var. A line like `export ROUTE_FAST = 1` in
# config.mk gets respected here. Env var still wins if already set.
# IMPORTANT: temporarily disable -e/pipefail around the grep|head|sed pipeline
# because a missing knob (most common case) makes grep exit 1, which would
# otherwise abort the entire script under `set -eo pipefail`.
set +e +o pipefail
for _knob in PLACE_FAST ROUTE_FAST ROUTE_FAST_SKIP_DRT ROUTE_FAST_DRT_ITERS; do
  if [[ -z "${!_knob:-}" ]]; then
    _val=$(grep -E "^[[:space:]]*export[[:space:]]+${_knob}[[:space:]]*=" "$CONFIG_MK" 2>/dev/null | head -1 | sed -E "s/^[[:space:]]*export[[:space:]]+${_knob}[[:space:]]*=[[:space:]]*//" | tr -d ' "')
    if [[ -n "$_val" ]]; then
      export "$_knob=$_val"
      echo "config.mk supplied $_knob=$_val"
    fi
  fi
done
set -e -o pipefail
unset _knob _val 2>/dev/null || true

# PLACE_FAST escape hatch: disable timing-driven + routability-driven global
# placement. Required for very-large netlists (>1M nets) where the timing
# repair loop in `gpl` would otherwise spin for hours after the placement
# overflow target is already met. Applies to BOOM-class CPUs and similar.
# Set PLACE_FAST=1 in the env OR add `export PLACE_FAST = 1` to config.mk.
if [[ "${PLACE_FAST:-0}" == "1" ]]; then
  MAKE_CMD="$MAKE_CMD GPL_TIMING_DRIVEN=0 GPL_ROUTABILITY_DRIVEN=0"
  echo "PLACE_FAST=1 → disabling GPL_TIMING_DRIVEN and GPL_ROUTABILITY_DRIVEN"
fi

# ROUTE_FAST escape hatch: cap GRT/DRT iterations and skip the optional
# repair/antenna passes that dominate runtime on >M-net netlists. Required
# for BOOM ChipTop class where each GRT extra-iteration phase has 30
# iterations × 2 phases × ~2.4M nets and never converges in <24h.
# Set ROUTE_FAST=1 in the env to enable.
#
# Knobs applied (read by ORFS Makefile from env):
#   GLOBAL_ROUTE_ARGS=-congestion_iterations 5 -allow_congestion -verbose
#     -congestion_report_iter_step 1
#       — cap initial GRT extra-iteration phase at 5 (vs default 30) and
#         accept the result even with congestion violations.
#   SKIP_INCREMENTAL_REPAIR=1
#       — skip repair_design_helper + incremental GRT + repair_timing_helper
#         block inside global_route.tcl. Dominates GRT stage runtime.
#   SKIP_ANTENNA_REPAIR=1
#       — skip antenna repair iterations (each rebuilds affected nets).
#   DETAILED_ROUTE_END_ITERATION=10  (default 64)
#       — cap detailed-routing iterations.
#
# Optional further fallback: ROUTE_FAST_SKIP_DRT=1 also enables
# SKIP_DETAILED_ROUTE=1 — produces DEF + global routes but no GDS.
if [[ "${ROUTE_FAST:-0}" == "1" ]]; then
  # GLOBAL_ROUTE_ARGS is passed as a quoted make cmdline arg so it survives
  # ORFS's per-step variable scrub (see references/orfs-playbook.md).
  GRT_FAST_ARGS='-congestion_iterations 5 -allow_congestion -verbose -congestion_report_iter_step 1'
  # GRT_INCREMENTAL_ALLOW_CONGESTION enables a SKILL-LOCAL patch in
  # OpenROAD-flow-scripts/flow/scripts/global_route.tcl that adds
  # -allow_congestion to the post-recover_power -end_incremental GRT call.
  # Without this patch, the initial GRT call may pass with congestion
  # (allowed via GLOBAL_ROUTE_ARGS) but the recover_power_helper's
  # incremental GRT then aborts with ERROR GRT-0116 on the same residual
  # congestion. ChipTop-class designs cannot reach 0 overflow on this
  # OpenROAD/nangate45, so this is required for any ROUTE_FAST run.
  # DRT iteration cap: default 10. Override with ROUTE_FAST_DRT_ITERS for an
  # even faster (dirtier) detailed-route pass — e.g. =1 produces a GDS quickly
  # for congestion-bound designs that would never converge.
  DRT_ITERS="${ROUTE_FAST_DRT_ITERS:-10}"
  MAKE_CMD="$MAKE_CMD SKIP_INCREMENTAL_REPAIR=1 SKIP_ANTENNA_REPAIR=1 DETAILED_ROUTE_END_ITERATION=$DRT_ITERS GLOBAL_ROUTE_ARGS='$GRT_FAST_ARGS' GRT_INCREMENTAL_ALLOW_CONGESTION=1"
  echo "ROUTE_FAST=1 → SKIP_INCREMENTAL_REPAIR + SKIP_ANTENNA_REPAIR + DRT_END_ITER=$DRT_ITERS"
  echo "             → GLOBAL_ROUTE_ARGS='$GRT_FAST_ARGS'"
  echo "             → GRT_INCREMENTAL_ALLOW_CONGESTION=1 (requires patched global_route.tcl)"
  if [[ "${ROUTE_FAST_SKIP_DRT:-0}" == "1" ]]; then
    MAKE_CMD="$MAKE_CMD SKIP_DETAILED_ROUTE=1"
    echo "ROUTE_FAST_SKIP_DRT=1 → SKIP_DETAILED_ROUTE=1 (no GDS, DEF only)"
  fi
fi

# Apply CPU core limit if specified
if [[ -n "${ORFS_MAX_CPUS:-}" ]]; then
  # Build a CPU list 0-(N-1)
  CPU_LIST="0-$((ORFS_MAX_CPUS - 1))"
  MAKE_CMD="taskset -c $CPU_LIST $MAKE_CMD"
  echo "Limiting to $ORFS_MAX_CPUS CPU cores ($CPU_LIST)"
fi

echo "Timeout: ${ORFS_TIMEOUT}s"

# Stage-by-stage execution support
ORFS_STAGES_LIST="${ORFS_STAGES:-synth floorplan place cts route finish}"

# Guard: if FROM_STAGE is set but doesn't match any known stage, abort loudly.
# Without this, the stage loop silently skips every stage and exits 0, which has
# caused ghost "passes" in batch runners that accidentally passed a timeout value
# (e.g. "14400") to FROM_STAGE.
if [[ -n "$FROM_STAGE" ]]; then
  stage_known=false
  for _s in $ORFS_STAGES_LIST; do
    if [[ "$_s" == "$FROM_STAGE" ]]; then
      stage_known=true
      break
    fi
  done
  if [[ "$stage_known" != "true" ]]; then
    echo "ERROR: FROM_STAGE='$FROM_STAGE' does not match any stage in ORFS_STAGES_LIST='$ORFS_STAGES_LIST'" >&2
    echo "       Valid stages: $ORFS_STAGES_LIST" >&2
    exit 2
  fi
fi

run_stage() {
  local stage="$1"
  echo ""
  echo "=== Running ORFS stage: $stage ==="
  local stage_start
  stage_start=$(date +%s)

  local STAGE_STATUS=0
  set +e +o pipefail
  # Use setsid so timeout can kill the entire process group (prevents zombie processes)
  setsid timeout --signal=TERM --kill-after=60 "$ORFS_TIMEOUT" \
    bash -c "$MAKE_CMD $stage" 2>&1 | tee -a "$BACKEND_DIR/flow.log"
  STAGE_STATUS=${PIPESTATUS[0]}
  set -e -o pipefail

  local stage_end
  stage_end=$(date +%s)
  local stage_elapsed=$((stage_end - stage_start))
  echo "{\"stage\": \"$stage\", \"status\": $STAGE_STATUS, \"elapsed_s\": $stage_elapsed}" >> "$BACKEND_DIR/stage_log.jsonl"

  if [[ $STAGE_STATUS -ne 0 ]]; then
    echo "ERROR: Stage '$stage' failed (exit code $STAGE_STATUS) after ${stage_elapsed}s" | tee -a "$BACKEND_DIR/flow.log"
    if [[ $STAGE_STATUS -eq 124 || $STAGE_STATUS -eq 137 ]]; then
      echo "  (timed out after ${ORFS_TIMEOUT}s, exit code $STAGE_STATUS)" | tee -a "$BACKEND_DIR/flow.log"
    fi
    return $STAGE_STATUS
  fi
  echo "Stage '$stage' completed in ${stage_elapsed}s"
  return 0
}

# Run stages
MAKE_STATUS=0
SKIP_STAGES=true
if [[ -z "$FROM_STAGE" ]]; then
  SKIP_STAGES=false
fi

for stage in $ORFS_STAGES_LIST; do
  if [[ "$SKIP_STAGES" == "true" ]]; then
    if [[ "$stage" == "$FROM_STAGE" ]]; then
      SKIP_STAGES=false
    else
      echo "Skipping stage: $stage (resuming from $FROM_STAGE)"
      continue
    fi
  fi

  run_stage "$stage" || { MAKE_STATUS=$?; break; }
done

# Detect routing failure and suggest recovery
if [[ $MAKE_STATUS -ne 0 ]]; then
  FAILED_STAGE=$(tail -1 "$BACKEND_DIR/stage_log.jsonl" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('stage','unknown'))" 2>/dev/null || echo "unknown")
  if [[ "$FAILED_STAGE" == "grt" || "$FAILED_STAGE" == "route" ]]; then
    echo "" | tee -a "$BACKEND_DIR/flow.log"
    echo "HINT: Routing congestion detected. Try re-running with:" | tee -a "$BACKEND_DIR/flow.log"
    echo "  1. Add to config.mk: export ROUTING_LAYER_ADJUSTMENT = 0.10" | tee -a "$BACKEND_DIR/flow.log"
    echo "  2. Resume: FROM_STAGE=route scripts/flow/run_orfs.sh $PROJECT_DIR $PLATFORM" | tee -a "$BACKEND_DIR/flow.log"
    # Auto-suggest ROUTE_FAST when failure is on a ChipTop/BOOM-scale design
    # (large 4_cts.odb is the cheapest signal; no need to walk the netlist).
    CTS_ODB="$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT/4_cts.odb"
    CTS_SIZE=0
    if [[ -f "$CTS_ODB" ]]; then
      CTS_SIZE=$(stat -c%s "$CTS_ODB" 2>/dev/null || echo 0)
    fi
    # >1 GB CTS database ≈ ChipTop/BOOM scale. Recommend ROUTE_FAST.
    if (( CTS_SIZE > 1073741824 )); then
      echo "  3. ChipTop-scale CTS ODB detected (${CTS_SIZE} bytes). Add ROUTE_FAST=1:" | tee -a "$BACKEND_DIR/flow.log"
      echo "       ROUTE_FAST=1 FROM_STAGE=route scripts/flow/run_orfs.sh $PROJECT_DIR $PLATFORM" | tee -a "$BACKEND_DIR/flow.log"
      echo "       (skips post-GRT incremental repair + antenna; caps DRT to 10 iters)" | tee -a "$BACKEND_DIR/flow.log"
    fi
  elif [[ "$FAILED_STAGE" == "floorplan" ]]; then
    if grep -q "PDN-0179\|Insufficient width to add straps\|Unable to repair all channels" "$BACKEND_DIR/flow.log" 2>/dev/null; then
      echo "" | tee -a "$BACKEND_DIR/flow.log"
      echo "HINT: PDN channel repair failure (PDN-0179) detected during floorplan." | tee -a "$BACKEND_DIR/flow.log"
      echo "  The design has too many cells for the current die area." | tee -a "$BACKEND_DIR/flow.log"
      echo "  Possible fixes:" | tee -a "$BACKEND_DIR/flow.log"
      echo "  1. Increase DIE_AREA/CORE_AREA by 10-20% in config.mk" | tee -a "$BACKEND_DIR/flow.log"
      echo "  2. Reduce PLACE_DENSITY in config.mk" | tee -a "$BACKEND_DIR/flow.log"
      echo "  3. Remove SYNTH_HIERARCHICAL=1 if set (reduces cell count)" | tee -a "$BACKEND_DIR/flow.log"
      echo "  4. Remove ABC_AREA=1 if set (changes cell mix)" | tee -a "$BACKEND_DIR/flow.log"
    fi
  elif [[ "$FAILED_STAGE" == "place" ]]; then
    # Two distinct stalls live inside the place stage. Diagnose which:
    # - 3_3_place_gp stuck in `Timing-driven iteration N/2` (gpl resizer pass)
    #   → PLACE_FAST=1 fixes this (disables GPL_TIMING_DRIVEN/ROUTABILITY_DRIVEN).
    # - 3_4_place_resized stuck in `repair_design -verbose` (resize.tcl) on a
    #   multi-M-net design (Iteration|Area|Resized|Buffers|Nets repaired|Remaining).
    #   PLACE_FAST does NOT help here — repair_design is a separate code path.
    #   Observed on arm_core (2026-05-26, 8h budget exhausted at iter 785K/1.36M).
    GP_STUCK=0
    RESIZED_STUCK=0
    if grep -qE "Timing-driven iteration .*virtual.*false" "$BACKEND_DIR/flow.log" 2>/dev/null; then
      GP_STUCK=1
    fi
    if [[ -n "${FLOW_DIR:-}" ]]; then
      LATEST_PLACE_TMP=$(ls -t "$FLOW_DIR/logs/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT/3_4_place_resized.tmp.log" 2>/dev/null | head -1)
      if [[ -f "$LATEST_PLACE_TMP" ]] && tail -200 "$LATEST_PLACE_TMP" 2>/dev/null | grep -qE "Iteration\s+\|.*Resized.*Buffers.*Nets repaired"; then
        RESIZED_STUCK=1
      fi
    fi
    if [[ $GP_STUCK -eq 1 ]]; then
      echo "" | tee -a "$BACKEND_DIR/flow.log"
      echo "HINT: Place_gp timing-driven repair appears stuck on a very large netlist." | tee -a "$BACKEND_DIR/flow.log"
      echo "  Validated workaround for BOOM-class designs (place_gp only):" | tee -a "$BACKEND_DIR/flow.log"
      echo "  1. PLACE_FAST=1 FROM_STAGE=place scripts/flow/run_orfs.sh $PROJECT_DIR $PLATFORM" | tee -a "$BACKEND_DIR/flow.log"
      echo "  2. Or add to config.mk: export GPL_TIMING_DRIVEN=0; export GPL_ROUTABILITY_DRIVEN=0" | tee -a "$BACKEND_DIR/flow.log"
    fi
    if [[ $RESIZED_STUCK -eq 1 ]]; then
      echo "" | tee -a "$BACKEND_DIR/flow.log"
      echo "HINT: 3_4_place_resized's repair_design appears stuck on buffer insertion." | tee -a "$BACKEND_DIR/flow.log"
      echo "  This is a DIFFERENT hang from place_gp — PLACE_FAST does not fix it." | tee -a "$BACKEND_DIR/flow.log"
      echo "  No ORFS knob currently skips repair_design at place stage." | tee -a "$BACKEND_DIR/flow.log"
      echo "  Reduce design size (smaller CORE_UTILIZATION, less aggressive synth)" | tee -a "$BACKEND_DIR/flow.log"
      echo "  or accept the design is intractable on this OpenROAD version." | tee -a "$BACKEND_DIR/flow.log"
      echo "  Reference: arm_core (Amber a25 + 4 single_port_ram_*) hit this 2026-05-26." | tee -a "$BACKEND_DIR/flow.log"
    fi
  elif [[ "$FAILED_STAGE" == "synth" ]]; then
    # Synth-stage failures fall into three documented shapes, none fixable by a P&R
    # knob (see references/failure-patterns.md). Emit a targeted HINT so the operator
    # does not waste budget re-running with bigger timeouts / lower utilization.
    if grep -qE "Executing AST frontend in derive mode" "$BACKEND_DIR/flow.log" 2>/dev/null \
       && [[ $STAGE_STATUS -eq 124 || $STAGE_STATUS -eq 137 ]]; then
      echo "" | tee -a "$BACKEND_DIR/flow.log"
      echo "HINT: Synth timed out inside Yosys AST 'derive mode' — a const-function" | tee -a "$BACKEND_DIR/flow.log"
      echo "  elaboration blowup (classic parametric LFSR/CRC lfsr_mask), NOT scale." | tee -a "$BACKEND_DIR/flow.log"
      echo "  A longer ORFS_TIMEOUT / lower utilization will NOT help (pre-floorplan)." | tee -a "$BACKEND_DIR/flow.log"
      echo "  Intractable without RTL surgery. See failure-patterns.md:" | tee -a "$BACKEND_DIR/flow.log"
      echo "  'LFSR / CRC parametric function expansion in Yosys AST frontend'." | tee -a "$BACKEND_DIR/flow.log"
    elif grep -qE "GTECH_[A-Z0-9_]+.* referenced .* not part of the design" "$BACKEND_DIR/flow.log" 2>/dev/null; then
      echo "" | tee -a "$BACKEND_DIR/flow.log"
      echo "HINT: Synth failed on a missing Synopsys GTECH/DesignWare primitive —" | tee -a "$BACKEND_DIR/flow.log"
      echo "  the RTL bundle is incomplete (vendor cell library absent). No config" | tee -a "$BACKEND_DIR/flow.log"
      echo "  knob supplies it; do NOT stub sequential/MUX cells (corrupts netlist)." | tee -a "$BACKEND_DIR/flow.log"
      echo "  See failure-patterns.md: 'Missing proprietary primitive library'." | tee -a "$BACKEND_DIR/flow.log"
    elif [[ $STAGE_STATUS -eq 124 || $STAGE_STATUS -eq 137 ]]; then
      echo "" | tee -a "$BACKEND_DIR/flow.log"
      echo "HINT: Synth timed out with no AST-derive or GTECH signature — likely a pure" | tee -a "$BACKEND_DIR/flow.log"
      echo "  SCALE timeout (huge multiplier/array design stuck in OPT/FLATTEN/ABC)." | tee -a "$BACKEND_DIR/flow.log"
      echo "  Triage per failure-patterns.md 'Synth timeout triage: AST pathology vs" | tee -a "$BACKEND_DIR/flow.log"
      echo "  scale timeout'. If genuinely scale-bound (e.g. koios_lenet LeNet CNN), it" | tee -a "$BACKEND_DIR/flow.log"
      echo "  may be intractable on this host; a longer ORFS_TIMEOUT only sometimes helps." | tee -a "$BACKEND_DIR/flow.log"
    fi
  fi
fi

# Collect results (ORFS uses FLOW_VARIANT as subdirectory)
RESULTS_DIR="$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
LOGS_DIR="$FLOW_DIR/logs/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
OBJECTS_DIR="$FLOW_DIR/objects/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
REPORTS_DIR="$FLOW_DIR/reports/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"

# Fallback: if variant dir doesn't exist, try without it
if [[ ! -d "$RESULTS_DIR" ]]; then
  RESULTS_DIR="$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME"
  LOGS_DIR="$FLOW_DIR/logs/$PLATFORM/$DESIGN_NAME"
  OBJECTS_DIR="$FLOW_DIR/objects/$PLATFORM/$DESIGN_NAME"
  REPORTS_DIR="$FLOW_DIR/reports/$PLATFORM/$DESIGN_NAME"
fi

# Copy results to project backend directory
if [[ -d "$RESULTS_DIR" ]]; then
  cp -r "$RESULTS_DIR" "$BACKEND_DIR/results" 2>/dev/null || true
fi

if [[ -d "$LOGS_DIR" ]]; then
  cp -r "$LOGS_DIR" "$BACKEND_DIR/logs" 2>/dev/null || true
fi

if [[ -d "$REPORTS_DIR" ]]; then
  cp -r "$REPORTS_DIR" "$BACKEND_DIR/reports_orfs" 2>/dev/null || true
fi

# Copy key artifacts
GDS_FILES=$(find "$RESULTS_DIR" -name "*.gds" 2>/dev/null || true)
DEF_FILES=$(find "$RESULTS_DIR" -name "*.def" 2>/dev/null || true)
ODB_FILES=$(find "$RESULTS_DIR" -name "*.odb" 2>/dev/null || true)

mkdir -p "$BACKEND_DIR/final"

for f in $GDS_FILES; do
  cp "$f" "$BACKEND_DIR/final/" 2>/dev/null || true
done
for f in $DEF_FILES; do
  cp "$f" "$BACKEND_DIR/final/" 2>/dev/null || true
done
for f in $ODB_FILES; do
  cp "$f" "$BACKEND_DIR/final/" 2>/dev/null || true
done

# Write run metadata
cat > "$BACKEND_DIR/run-meta.json" <<METAEOF
{
  "run_tag": "$RUN_TAG",
  "design_name": "$DESIGN_NAME",
  "platform": "$PLATFORM",
  "config_mk": "$CONFIG_MK",
  "sdc_file": "$SDC_FILE",
  "make_status": $MAKE_STATUS,
  "orfs_results": "$RESULTS_DIR",
  "orfs_logs": "$LOGS_DIR"
}
METAEOF

if [[ $MAKE_STATUS -eq 0 ]]; then
  echo ""
  echo "ORFS run completed successfully: $RUN_TAG"
  echo "Results: $BACKEND_DIR"
else
  echo ""
  echo "ORFS run FAILED (exit code $MAKE_STATUS): $RUN_TAG"
  echo "Check logs: $BACKEND_DIR/flow.log"
fi

exit $MAKE_STATUS
