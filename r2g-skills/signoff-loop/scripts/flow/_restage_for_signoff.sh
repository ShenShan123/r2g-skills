#!/usr/bin/env bash
# Shared helper to re-stage a project's backend artifacts into the ORFS workspace
# so DRC / LVS can run after the original ORFS scratch area was cleaned up.
#
# Sourced (not executed). Expects these to be set by the caller:
#   PROJECT_DIR      — absolute path to design_cases/<project>
#   PLATFORM         — e.g. nangate45
#   DESIGN_NAME      — DESIGN_NAME from config.mk
#   FLOW_VARIANT     — typically basename of PROJECT_DIR
#   FLOW_DIR         — $ORFS_ROOT/flow
#   CONFIG_MK        — $PROJECT_DIR/constraints/config.mk
#
# After sourcing, the caller can rely on:
#   ORFS_DESIGN_DIR/config.mk        present
#   ORFS_RESULTS_DIR/6_final.gds     present (if a project backend GDS exists)
#   ORFS_RESULTS_DIR/6_final.odb     present (if available — needed by LVS)
#
# Idempotent: if the target paths already have the artifacts, nothing is copied.

_R2G_RESTAGE_FOR_SIGNOFF=1

# Validate inputs.
: "${PROJECT_DIR:?PROJECT_DIR must be set}"
: "${PLATFORM:?PLATFORM must be set}"
: "${DESIGN_NAME:?DESIGN_NAME must be set}"
: "${FLOW_VARIANT:?FLOW_VARIANT must be set}"
: "${FLOW_DIR:?FLOW_DIR must be set}"
: "${CONFIG_MK:?CONFIG_MK must be set}"

ORFS_DESIGN_DIR="$FLOW_DIR/designs/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
ORFS_RESULTS_DIR="$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
ORFS_LOGS_DIR="$FLOW_DIR/logs/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
ORFS_OBJECTS_DIR="$FLOW_DIR/objects/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"

# 1. Stage config.mk + constraint.sdc into the design dir (only if missing).
if [[ ! -f "$ORFS_DESIGN_DIR/config.mk" ]]; then
  mkdir -p "$ORFS_DESIGN_DIR"
  cp "$CONFIG_MK" "$ORFS_DESIGN_DIR/config.mk"
  SDC_SRC="$PROJECT_DIR/constraints/constraint.sdc"
  if [[ -f "$SDC_SRC" ]]; then
    cp "$SDC_SRC" "$ORFS_DESIGN_DIR/constraint.sdc"
  fi
  # Also stage any platform-extras the original ORFS run might have shipped
  # (macro placement, fakeram CDL, etc.). These live next to config.mk in the
  # project's constraints/ dir under signoff-loop convention.
  for extra in macro_placement.tcl combined.cdl; do
    if [[ -f "$PROJECT_DIR/constraints/$extra" ]]; then
      cp "$PROJECT_DIR/constraints/$extra" "$ORFS_DESIGN_DIR/$extra"
    fi
  done
fi

# 2. Stage backend artifacts into ORFS results/, logs/, reports/.
#
#    ORFS Makefile uses dependency timestamps: `make drc` cascades back through
#    6_final.gds → 6_final.def → 5_route → ... If only 6_final.* are present,
#    make will rebuild the entire backend (40+ minutes). To avoid that we
#    restage the *full* preserved intermediates from the project backend
#    directory.
#
#    Source-of-truth: the project backend RUN that actually contains
#    results/6_final.gds (NOT necessarily the newest mtime — earlier runs
#    sometimes have artifacts while a later "empty" RUN dir exists from a
#    crashed re-attempt).
_restage_pick_run_dir() {
  local run_dir
  for run_dir in $(ls -dt "$PROJECT_DIR"/backend/RUN_* 2>/dev/null); do
    if [[ -f "$run_dir/results/6_final.gds" ]]; then
      echo "$run_dir"
      return 0
    fi
  done
  # fallback: any run with a final/6_final.gds (older r2g layout)
  for run_dir in $(ls -dt "$PROJECT_DIR"/backend/RUN_* 2>/dev/null); do
    if [[ -f "$run_dir/final/6_final.gds" ]]; then
      echo "$run_dir"
      return 0
    fi
  done
  return 1
}

R2G_BACKEND_RUN="$(_restage_pick_run_dir || true)"

if [[ -z "$R2G_BACKEND_RUN" ]]; then
  echo "WARNING: no backend RUN dir contains a final GDS for $DESIGN_NAME ($PROJECT_DIR)" >&2
fi

# Full restage of results/, logs/, reports/, objects/ (if missing).
_restage_dir() {
  local subdir="$1"; local dst="$2"
  local src="$R2G_BACKEND_RUN/$subdir"
  if [[ -d "$src" && ! -f "$dst/.r2g_restaged" ]]; then
    mkdir -p "$dst"
    # Use cp -n to avoid clobbering anything already present.
    cp -rn "$src"/. "$dst"/ 2>/dev/null || true
    : > "$dst/.r2g_restaged"
  fi
}

if [[ -n "$R2G_BACKEND_RUN" ]]; then
  _restage_dir results        "$ORFS_RESULTS_DIR"
  _restage_dir logs           "$ORFS_LOGS_DIR"
  _restage_dir reports_orfs   "$FLOW_DIR/reports/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
  _restage_dir reports        "$FLOW_DIR/reports/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"

  # Older r2g runs only kept the final/ subset; fall back to those if results/
  # wasn't preserved.
  if [[ ! -f "$ORFS_RESULTS_DIR/6_final.gds" && -f "$R2G_BACKEND_RUN/final/6_final.gds" ]]; then
    mkdir -p "$ORFS_RESULTS_DIR"
    cp -n "$R2G_BACKEND_RUN/final"/* "$ORFS_RESULTS_DIR"/ 2>/dev/null || true
  fi
fi

# Bump mtimes so make sees the staged artifacts as up-to-date relative to
# config.mk / sources. Without this the Makefile may still rebuild because the
# (just-copied) intermediates appear older than config.mk.
if [[ -d "$ORFS_RESULTS_DIR" ]]; then
  find "$ORFS_RESULTS_DIR" -type f -exec touch {} + 2>/dev/null || true
fi
if [[ -d "$ORFS_LOGS_DIR" ]]; then
  find "$ORFS_LOGS_DIR" -type f -exec touch {} + 2>/dev/null || true
fi

unset _restage_dir _restage_pick_run_dir
