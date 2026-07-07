#!/usr/bin/env bash
set -euo pipefail

# usage: run_labels.sh <project-dir> [platform] [flow_variant]
# Extracts per-cell/per-net dataset labels (congestion, wirelength, timing,
# IR drop) from a completed ORFS backend run, plus a per-design stats JSON.
# Fail-soft: a missing input or per-label tool error is recorded, not fatal.
# Results: <project-dir>/labels/*.csv and <project-dir>/reports/labels_stats.json
# See references/label-extraction.md.

PROJECT_DIR="${1:-}"
PLATFORM="${2:-}"
FLOW_VARIANT_ARG="${3:-}"

if [[ -z "$PROJECT_DIR" ]]; then
  echo "usage: run_labels.sh <project-dir> [platform]" >&2
  exit 1
fi

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LABELS_SRC="$SKILL_DIR/scripts/extract/labels"
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_env.sh"

if [[ -z "${ORFS_ROOT:-}" || ! -d "$FLOW_DIR" ]]; then
  echo "ERROR: ORFS not found. Set ORFS_ROOT." >&2
  exit 1
fi

PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
CONFIG_MK="$PROJECT_DIR/constraints/config.mk"
SDC_FILE="$PROJECT_DIR/constraints/constraint.sdc"
LABELS_DIR="$PROJECT_DIR/labels"
REPORTS_DIR="$PROJECT_DIR/reports"
mkdir -p "$LABELS_DIR" "$REPORTS_DIR"

DESIGN_NAME="$(basename "$PROJECT_DIR")"
if [[ -f "$CONFIG_MK" ]]; then
  # `|| true` on every grep substitution: a missing key must not abort under
  # `set -euo pipefail` (grep exits 1 on no match -> pipefail -> set -e).
  _dn=$(grep -E '^\s*(export\s+)?DESIGN_NAME' "$CONFIG_MK" | head -1 | sed 's/.*=\s*//' | tr -d ' ' || true)
  [[ -n "$_dn" ]] && DESIGN_NAME="$_dn"
  if [[ -z "$PLATFORM" ]]; then
    _pl=$(grep -E '^\s*(export\s+)?PLATFORM\b' "$CONFIG_MK" | head -1 | sed 's/.*=\s*//' | tr -d ' ' || true)
    PLATFORM="${_pl:-asap7}"
  fi
fi
PLATFORM="${PLATFORM:-asap7}"

# --- Locate the collected 6_final.{odb,def} --------------------------------
ODB=""; DEF=""
BACKEND_DIR="$PROJECT_DIR/backend"
if [[ -d "$BACKEND_DIR" ]]; then
  for run in $(ls -d "$BACKEND_DIR"/RUN_* 2>/dev/null | sort -r); do
    for sub in final results; do
      [[ -z "$ODB" && -f "$run/$sub/6_final.odb" ]] && ODB="$run/$sub/6_final.odb"
      [[ -z "$DEF" && -f "$run/$sub/6_final.def" ]] && DEF="$run/$sub/6_final.def"
    done
    [[ -n "$ODB" || -n "$DEF" ]] && break
  done
fi
# Fallback: live ORFS results dir
if [[ -z "$ODB" || -z "$DEF" ]]; then
  VARIANT="${FLOW_VARIANT_ARG:-$(basename "$PROJECT_DIR")}"
  for rd in "$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME/$VARIANT" "$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME"; do
    [[ -z "$ODB" && -f "$rd/6_final.odb" ]] && ODB="$rd/6_final.odb"
    [[ -z "$DEF" && -f "$rd/6_final.def" ]] && DEF="$rd/6_final.def"
  done
fi

if [[ -z "$ODB" && -z "$DEF" ]]; then
  echo "SKIP: no 6_final.odb/def found for $DESIGN_NAME — backend not completed/collected." >&2
  printf '{"design":"%s","platform":"%s","labels":{},"status":"skipped","reason":"no backend artifacts"}\n' \
    "$DESIGN_NAME" "$PLATFORM" > "$REPORTS_DIR/labels_stats.json"
  exit 0
fi

echo "Design: $DESIGN_NAME  Platform: $PLATFORM"
echo "ODB: ${ODB:-<none>}"
echo "DEF: ${DEF:-<none>}"

# --- Resolve platform liberty/lef/voltage ----------------------------------
RESOLVED="$(bash "$(dirname "${BASH_SOURCE[0]}")/resolve_platform_paths.sh" "$CONFIG_MK" "$PLATFORM" 2>/dev/null || true)"
LIB_FILES=$(echo "$RESOLVED" | sed -n 's/^LIB_FILES=//p')
TECH_LEF=$(echo "$RESOLVED" | sed -n 's/^TECH_LEF=//p')
SC_LEF=$(echo "$RESOLVED" | sed -n 's/^SC_LEF=//p')
ADDITIONAL_LEFS=$(echo "$RESOLVED" | sed -n 's/^ADDITIONAL_LEFS=//p')
ADDITIONAL_LIBS=$(echo "$RESOLVED" | sed -n 's/^ADDITIONAL_LIBS=//p')
SUPPLY_VOLTAGE=$(echo "$RESOLVED" | sed -n 's/^SUPPLY_VOLTAGE=//p')
SUPPLY_VOLTAGE="${SUPPLY_VOLTAGE:-1.1}"

# --- Clock period / port from the design SDC -------------------------------
CLOCK_PERIOD="10.0"; CLOCK_PORT=""
if [[ -f "$SDC_FILE" ]]; then
  _cp=$(grep -E '^\s*set\s+clk_period\b' "$SDC_FILE" | head -1 | sed -E 's/.*set\s+clk_period\s+//' | awk '{print $1}' || true)
  [[ -n "$_cp" ]] && CLOCK_PERIOD="$_cp"
  _pn=$(grep -E '^\s*set\s+clk_port_name\b' "$SDC_FILE" | head -1 | sed -E 's/.*set\s+clk_port_name\s+//' | awk '{print $1}' | tr -d '"' || true)
  [[ -n "$_pn" ]] && CLOCK_PORT="$_pn"
fi
echo "clk_period=$CLOCK_PERIOD clk_port=${CLOCK_PORT:-<auto>} supply=$SUPPLY_VOLTAGE libs=$(echo $LIB_FILES | wc -w)"

OPENROAD="${OPENROAD_EXE:-openroad}"
LABEL_TIMEOUT="${LABEL_TIMEOUT:-2400}"

run_soft() {  # name + command...; never aborts the orchestrator
  local name="$1"; shift
  echo "--- $name ---"
  if timeout --signal=TERM --kill-after=30 "$LABEL_TIMEOUT" "$@" > "$LABELS_DIR/$name.log" 2>&1; then
    echo "  $name: ok"
  else
    echo "  $name: FAILED (see $LABELS_DIR/$name.log)" >&2
  fi
}

# --- Congestion (DEF + tech.lef) -------------------------------------------
# R2G_PLATFORM must be passed (run_features.sh exports it): extract_congestion
# reads it to pick the routing-layer fallback profile. Without it the extractor
# defaulted to asap7's profile — currently harmless (all platforms share one
# fallback table AND it only fires when the tech LEF yields no routing layers),
# but a latent cross-platform hazard the moment the fallback becomes
# platform-specific (2026-07-06 nangate45 audit).
if [[ -n "$DEF" ]]; then
  # SC_LEF (standard-cell LEF) + ADDITIONAL_LEFS (macro LEFs) carry per-MACRO SIZE,
  # which extract_congestion.py needs to build each cell's bounding box and average
  # congestion over the GCells its footprint overlaps (the Congestion_Parse method).
  # Absent/unparseable -> the extractor falls back to origin-GCell mapping and warns.
  R2G_PLATFORM="$PLATFORM" TECH_LEF="$TECH_LEF" SC_LEF="$SC_LEF" ADDITIONAL_LEFS="$ADDITIONAL_LEFS" \
    run_soft congestion \
    python3 "$LABELS_SRC/extract_congestion.py" "$DEF" "$LABELS_DIR/cell_congestion.csv" "$DESIGN_NAME"
fi

# --- Wirelength (DEF) ------------------------------------------------------
if [[ -n "$DEF" ]]; then
  run_soft wirelength \
    python3 "$LABELS_SRC/extract_wirelength.py" "$DEF" "$LABELS_DIR/wirelength.csv" "$DESIGN_NAME"
fi

# --- Timing (ODB preferred, DEF fallback) + liberty ------------------------
# Leading var-assignments before the run_soft FUNCTION call are exported into
# the openroad child (verified bash behavior). Do NOT wrap with `env` — env
# cannot exec a shell function. CLOCK_PORT is passed as a literal assignment
# (an expansion like ${X:+CLOCK_PORT=$X} would be parsed as a command word, not
# an assignment); extract_timing.tcl treats an empty CLOCK_PORT as auto-detect.
TIMING_LIBS="$LIB_FILES $ADDITIONAL_LIBS"
if [[ -n "$ODB" ]]; then
  ODB_FILE="$ODB" R2G_LIB_FILES="$TIMING_LIBS" OUTPUT_CSV="$LABELS_DIR/timing_features.csv" \
    CLOCK_PERIOD="$CLOCK_PERIOD" CLOCK_PORT="$CLOCK_PORT" DESIGN_NAME="$DESIGN_NAME" \
    run_soft timing "$OPENROAD" -no_splash -exit "$LABELS_SRC/extract_timing.tcl"
elif [[ -n "$DEF" ]]; then
  DEF_FILE="$DEF" R2G_LIB_FILES="$TIMING_LIBS" TECH_LEF="$TECH_LEF" OUTPUT_CSV="$LABELS_DIR/timing_features.csv" \
    CLOCK_PERIOD="$CLOCK_PERIOD" CLOCK_PORT="$CLOCK_PORT" DESIGN_NAME="$DESIGN_NAME" \
    run_soft timing "$OPENROAD" -no_splash -exit "$LABELS_SRC/extract_timing.tcl"
fi

# --- IR drop (ODB) — liberty needed so PDNSim can compute cell power -------
if [[ -n "$ODB" ]]; then
  ODB_FILE="$ODB" R2G_LIB_FILES="$TIMING_LIBS" OUTPUT_RPT="$LABELS_DIR/ir_drop.csv" \
    SUPPLY_VOLTAGE="$SUPPLY_VOLTAGE" DESIGN_NAME="$DESIGN_NAME" \
    run_soft irdrop "$OPENROAD" -no_splash -exit "$LABELS_SRC/extract_irdrop.tcl"
fi

# --- Stats roll-up ---------------------------------------------------------
python3 "$LABELS_SRC/compute_label_stats.py" "$LABELS_DIR" "$REPORTS_DIR/labels_stats.json" "$DESIGN_NAME" "$PLATFORM"

echo "Labels: $LABELS_DIR"
echo "Stats:  $REPORTS_DIR/labels_stats.json"
