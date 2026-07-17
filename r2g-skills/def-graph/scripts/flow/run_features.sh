#!/usr/bin/env bash
set -euo pipefail

# usage: run_features.sh <project-dir> [platform] [flow_variant]
# Extracts per-node/per-edge/graph-level dataset *features* (the ML X side) from a
# completed ORFS backend run, plus a per-design stats JSON. Complements run_labels.sh
# (the Y side) — both read the same 6_final.def so rows join on graph_id + inst/net.
# Fail-soft: a missing input or per-worker error is recorded, not fatal.
# Results: <project-dir>/features/*.csv and <project-dir>/reports/features_stats.json
# See references/feature-extraction.md.

PROJECT_DIR="${1:-}"
PLATFORM="${2:-}"
FLOW_VARIANT_ARG="${3:-}"

if [[ -z "$PROJECT_DIR" ]]; then
  echo "usage: run_features.sh <project-dir> [platform]" >&2
  exit 1
fi

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FEATURES_SRC="$SKILL_DIR/scripts/extract/features"
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_env.sh"

if [[ -z "${ORFS_ROOT:-}" || ! -d "$FLOW_DIR" ]]; then
  echo "ERROR: ORFS not found. Set ORFS_ROOT." >&2
  exit 1
fi

PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
CONFIG_MK="$PROJECT_DIR/constraints/config.mk"
SDC_FILE="$PROJECT_DIR/constraints/constraint.sdc"
FEATURES_DIR="$PROJECT_DIR/features"
REPORTS_DIR="$PROJECT_DIR/reports"
mkdir -p "$FEATURES_DIR" "$REPORTS_DIR"

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

# --- Locate the collected 6_final.def (same artifact run_labels.sh uses) ---
# Override via the namespaced R2G_DEF only (NOT the bare ORFS variable DEF_FILE, which an
# operator may have exported — that would silently pin every batch design to one DEF).
DEF="${R2G_DEF:-}"
[[ -n "$DEF" ]] && echo "NOTE: DEF overridden via R2G_DEF=$DEF" >&2
RUN_DIR=""  # the backend RUN_* the DEF came from (SPEF is paired from the SAME run)
BACKEND_DIR="$PROJECT_DIR/backend"
if [[ -z "$DEF" && -d "$BACKEND_DIR" ]]; then
  for run in $(ls -d "$BACKEND_DIR"/RUN_* 2>/dev/null | sort -r); do
    for sub in final results; do
      [[ -z "$DEF" && -f "$run/$sub/6_final.def" ]] && { DEF="$run/$sub/6_final.def"; RUN_DIR="$run"; }
    done
    [[ -n "$DEF" ]] && break
  done
fi
# Provenance guard (failure-patterns.md #30; hardened 2026-07-16): the discovered
# artifacts are keyed to the platform they were BUILT on (backend run-meta.json);
# config.mk is mutable round state a campaign re-point rewrites. The guard now runs
# even for an EXPLICIT platform arg — an arg contradicting the DEF's build record
# used to win silently and stamp a wrong-platform manifest (sky130hd libs resolved
# against an hs DEF: every liberty-derived value wrong, caught only by the
# verifier). R2G_PLATFORM_FORCE=1 restores arg-wins for deliberate cross-platform
# reference builds. Shared logic: _provenance.sh (one copy).
if [[ -n "$RUN_DIR" && "${R2G_PLATFORM_FORCE:-0}" != "1" ]]; then
  PLATFORM=$(bash "$(dirname "${BASH_SOURCE[0]}")/_provenance.sh" "$RUN_DIR" "$PLATFORM")
fi
if [[ -z "$DEF" ]]; then  # fallback: live ORFS results dir
  VARIANT="${FLOW_VARIANT_ARG:-$(basename "$PROJECT_DIR")}"
  for rd in "$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME/$VARIANT" "$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME"; do
    [[ -z "$DEF" && -f "$rd/6_final.def" ]] && DEF="$rd/6_final.def"
  done
fi

if [[ -z "$DEF" || ! -f "$DEF" ]]; then
  echo "SKIP: no 6_final.def found for $DESIGN_NAME — backend not completed/collected." >&2
  printf '{"design":"%s","platform":"%s","features":{},"status":"skipped","reason":"no backend artifacts"}\n' \
    "$DESIGN_NAME" "$PLATFORM" > "$REPORTS_DIR/features_stats.json"
  exit 0
fi

# --- Signoff gate (failure-patterns.md #34) ---------------------------------
# Default warn for the standalone extractor (records the verdict in
# reports/signoff_gate.json, proceeds); run_graphs.sh — the dataset builder —
# enforces. R2G_SIGNOFF_GATE=enforce makes this stage block too. An explicit
# R2G_DEF override downgrades to warn (deliberate operator decision).
# Shared logic: signoff_gate.py (one copy — same rule as _provenance.sh).
GATE_MODE="${R2G_SIGNOFF_GATE:-warn}"
GATE_FLAGS=()
[[ -n "${R2G_DEF:-}" ]] && GATE_FLAGS+=(--def-overridden)
# Bind the SELECTED DEF (P0-17) so this stage's gate call records binding=bound +
# a def_fingerprint and does NOT overwrite run_graphs.sh's bound verdict with an
# 'unknown' binding by re-gating without --def (agent-logic #5, 2026-07-16). DEF is
# guaranteed non-empty here (the no-DEF skip above), but guard anyway.
[[ -n "$DEF" ]] && GATE_FLAGS+=(--def "$DEF")
if ! python3 "$(dirname "${BASH_SOURCE[0]}")/signoff_gate.py" "$PROJECT_DIR" \
       --run-dir "$RUN_DIR" --mode "$GATE_MODE" "${GATE_FLAGS[@]}"; then
  echo "SKIP: signoff gate blocked (R2G_SIGNOFF_GATE=enforce) — see $REPORTS_DIR/signoff_gate.json" >&2
  printf '{"design":"%s","platform":"%s","features":{},"status":"skipped","reason":"signoff gate: not signed off"}\n' \
    "$DESIGN_NAME" "$PLATFORM" > "$REPORTS_DIR/features_stats.json"
  exit 0
fi

# --- Locate the SPEF (optional — features degrade gracefully if absent) ----
# Prefer the SAME run the DEF came from so a fresh DEF is never paired with a stale SPEF
# from a different run; only scan across runs as a fallback, with a loud warning.
SPEF="${R2G_SPEF:-}"
if [[ -z "$SPEF" && -n "$RUN_DIR" ]]; then
  for sub in rcx results; do
    [[ -z "$SPEF" && -f "$RUN_DIR/$sub/6_final.spef" ]] && SPEF="$RUN_DIR/$sub/6_final.spef"
  done
fi
if [[ -z "$SPEF" && -d "$BACKEND_DIR" ]]; then
  for run in $(ls -d "$BACKEND_DIR"/RUN_* 2>/dev/null | sort -r); do
    for sub in rcx results; do
      [[ -z "$SPEF" && -f "$run/$sub/6_final.spef" ]] && SPEF="$run/$sub/6_final.spef"
    done
    [[ -n "$SPEF" ]] && break
  done
  [[ -n "$SPEF" && -n "$RUN_DIR" && "$SPEF" != "$RUN_DIR"/* ]] && \
    echo "WARN: SPEF $SPEF is from a different run than the DEF ($RUN_DIR) — cap features may be stale." >&2
fi
[[ -z "$SPEF" && -f "$PROJECT_DIR/rcx/6_final.spef" ]] && SPEF="$PROJECT_DIR/rcx/6_final.spef"
SPEF_PRESENT=0; [[ -n "$SPEF" && -f "$SPEF" ]] && SPEF_PRESENT=1

echo "Design: $DESIGN_NAME  Platform: $PLATFORM"
echo "DEF:  $DEF"
echo "SPEF: ${SPEF:-<none>}"

# --- Resolve platform liberty/lef/voltage ----------------------------------
RESOLVED="$(bash "$(dirname "${BASH_SOURCE[0]}")/resolve_platform_paths.sh" "$CONFIG_MK" "$PLATFORM" 2>/dev/null || true)"
LIB_FILES=$(echo "$RESOLVED" | sed -n 's/^LIB_FILES=//p')
TECH_LEF=$(echo "$RESOLVED" | sed -n 's/^TECH_LEF=//p')
ADDITIONAL_LIBS=$(echo "$RESOLVED" | sed -n 's/^ADDITIONAL_LIBS=//p')
# Cell/macro LEFs (per-MACRO SIZE + PIN geometry) — feed techlib.lef.cell_lef_paths
# so nodes_pin/nodes_net place pins at their true intra-cell LEF positions
# (pin_x/y_std_um, hpwl_um) instead of the instance origin.
SC_LEF=$(echo "$RESOLVED" | sed -n 's/^SC_LEF=//p')
ADDITIONAL_LEFS=$(echo "$RESOLVED" | sed -n 's/^ADDITIONAL_LEFS=//p')
echo "libs=$(echo "$LIB_FILES $ADDITIONAL_LIBS" | wc -w) tech_lef=$([[ -f "$TECH_LEF" ]] && echo yes || echo no)"

# --- Environment shared by every worker (positional args carry DEF/out/id) -
export R2G_SDC="$([[ -f "$SDC_FILE" ]] && echo "$SDC_FILE" || echo "")"
export R2G_SPEF="$([[ "$SPEF_PRESENT" == 1 ]] && echo "$SPEF" || echo "")"
export R2G_CONFIG="$([[ -f "$CONFIG_MK" ]] && echo "$CONFIG_MK" || echo "")"
export R2G_LIB_FILES="$LIB_FILES $ADDITIONAL_LIBS"
# Std-cell liberty only — the cell-type-id map is built from this so per-design macro
# libs (ADDITIONAL_LIBS) don't reshuffle std-cell ids across a platform dataset.
# ORFS's resolved LIB_FILES ALREADY folds ADDITIONAL_LIBS in, so subtract them —
# otherwise the std-cell "subset" contains the macro libs, macro_cell_keys() sees an
# empty difference, and connects_macro_flag is 0 on every macro design (2026-07-06
# nangate45 fakeram audit; failure-patterns.md "Dataset-Extraction Silent-Value
# Defects" #10).
SC_LIB_FILES=""
for _lf in $LIB_FILES; do
  _is_macro_lib=0
  for _al in $ADDITIONAL_LIBS; do
    [[ "$_lf" == "$_al" ]] && _is_macro_lib=1 && break
  done
  [[ "$_is_macro_lib" == 0 ]] && SC_LIB_FILES="${SC_LIB_FILES:+$SC_LIB_FILES }$_lf"
done
export R2G_SC_LIB_FILES="$SC_LIB_FILES"
export R2G_TECH_LEF="$TECH_LEF"
export SC_LEF="$SC_LEF"
export ADDITIONAL_LEFS="$ADDITIONAL_LEFS"
export R2G_PLATFORM="$PLATFORM"

FEATURE_TIMEOUT="${FEATURE_TIMEOUT:-2400}"

run_soft() {  # name + command...; never aborts the orchestrator
  local name="$1"; shift
  echo "--- $name ---"
  if timeout --signal=TERM --kill-after=30 "$FEATURE_TIMEOUT" "$@" > "$FEATURES_DIR/$name.log" 2>&1; then
    echo "  $name: ok"
  else
    echo "  $name: FAILED (see $FEATURES_DIR/$name.log)" >&2
  fi
}

for w in metadata nodes_gate nodes_net nodes_iopin nodes_pin \
         edges_gate_pin edges_pin_net edges_iopin_net; do
  run_soft "$w" python3 "$FEATURES_SRC/$w.py" "$DEF" "$FEATURES_DIR/$w.csv" "$DESIGN_NAME"
done

# --- Stats roll-up ---------------------------------------------------------
python3 "$FEATURES_SRC/compute_feature_stats.py" "$FEATURES_DIR" \
  "$REPORTS_DIR/features_stats.json" "$DESIGN_NAME" "$PLATFORM" "$SPEF_PRESENT"

echo "Features: $FEATURES_DIR"
echo "Stats:    $REPORTS_DIR/features_stats.json"
