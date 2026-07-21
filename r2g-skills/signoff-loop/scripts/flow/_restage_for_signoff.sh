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

# Full restage of results/, logs/, reports/, objects/ (identity-aware).
#
# The .r2g_restaged marker is IDENTITY-BEARING (full-pipeline Issue 7): it records the
# basename of the backend RUN it staged FROM. The old empty boolean marker skipped ALL
# copying once present, so a NEWER backend run (correctly picked by _restage_pick_run_dir)
# was never staged — signoff kept verifying a stale older layout. Now a differing (or
# empty/legacy) recorded identity re-stages the newer run with clobber; a same-identity
# marker stays the fast-path no-op.
_restage_dir() {
  local subdir="$1"; local dst="$2"
  local src="$R2G_BACKEND_RUN/$subdir"
  local marker="$dst/.r2g_restaged"
  local pick_id staged_id=""
  [[ -d "$src" ]] || return 0
  pick_id="$(basename "$R2G_BACKEND_RUN")"
  [[ -f "$marker" ]] && staged_id="$(head -1 "$marker" 2>/dev/null || true)"
  [[ "$staged_id" == "$pick_id" ]] && return 0    # already staged from this exact run
  mkdir -p "$dst"
  # Clobber-copy: a differing/legacy marker means the newer pick's artifacts (6_final.*)
  # must overwrite the stale staged set (src carries no marker, so it survives + is restamped).
  cp -r "$src"/. "$dst"/ 2>/dev/null || true
  printf '%s\n' "$pick_id" > "$marker"
}

if [[ -n "$R2G_BACKEND_RUN" ]]; then
  _restage_dir results        "$ORFS_RESULTS_DIR"
  _restage_dir logs           "$ORFS_LOGS_DIR"
  # objects/ carries stage prerequisites too (merged libs, klayout .lyt, ABC
  # scripts); without it `make drc` can implicitly rebuild the flow (pilot P1-2).
  # Preserved by run_orfs.sh since 2026-07-21; older backends simply lack it.
  _restage_dir objects        "$ORFS_OBJECTS_DIR"
  _restage_dir reports_orfs   "$FLOW_DIR/reports/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
  _restage_dir reports        "$FLOW_DIR/reports/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"

  # Older r2g runs only kept the final/ subset; fall back to those if results/
  # wasn't preserved. Identity-aware (full-pipeline Issue 7): a newer pick re-stages
  # even when a stale 6_final.gds from an older run is already present (the old cp -n +
  # gds-present guard would otherwise pin the workspace to the older layout forever).
  if [[ -f "$R2G_BACKEND_RUN/final/6_final.gds" ]]; then
    _fb_marker="$ORFS_RESULTS_DIR/.r2g_restaged"
    _fb_pick="$(basename "$R2G_BACKEND_RUN")"
    _fb_staged=""; [[ -f "$_fb_marker" ]] && _fb_staged="$(head -1 "$_fb_marker" 2>/dev/null || true)"
    if [[ "$_fb_staged" != "$_fb_pick" || ! -f "$ORFS_RESULTS_DIR/6_final.gds" ]]; then
      mkdir -p "$ORFS_RESULTS_DIR"
      cp -r "$R2G_BACKEND_RUN/final"/. "$ORFS_RESULTS_DIR"/ 2>/dev/null || true
      printf '%s\n' "$_fb_pick" > "$_fb_marker"
    fi
  fi
fi

# Bump mtimes so make sees the staged artifacts as up-to-date — in DEPENDENCY
# ORDER (pilot P1-2, 2026-07-21). The old bulk `find -exec touch` stamped files in
# filesystem-enumeration order, so an EARLIER stage artifact (e.g. 2_floorplan.odb)
# could land NEWER than a later one (6_final.gds); ORFS's mtime cascade then judged
# downstream targets stale and a DRC-only `make drc` silently rebuilt synth→finish
# before KLayout — nondeterministically, which is why only one pilot fixture hit it.
# Stamp design inputs (config.mk/SDC) OLDEST, then objects/ + logs, then results
# grouped by stage-number prefix (1_… → 6_…) with strictly increasing timestamps,
# so every consumer is provably newer than its producers and inputs.
_r2g_now=$(date +%s)
if [[ -d "$ORFS_DESIGN_DIR" ]]; then
  find "$ORFS_DESIGN_DIR" -maxdepth 1 -type f -exec touch -d "@$((_r2g_now - 120))" {} + 2>/dev/null || true
fi
for _r2g_dir in "$ORFS_OBJECTS_DIR" "$ORFS_LOGS_DIR"; do
  if [[ -d "$_r2g_dir" ]]; then
    find "$_r2g_dir" -type f -exec touch -d "@$((_r2g_now - 90))" {} + 2>/dev/null || true
  fi
done
if [[ -d "$ORFS_RESULTS_DIR" ]]; then
  _r2g_epoch=$((_r2g_now - 60))
  for _r2g_stage in 1 2 3 4 5 6; do
    _r2g_epoch=$((_r2g_epoch + 5))
    find "$ORFS_RESULTS_DIR" -type f -name "${_r2g_stage}_*" \
      -exec touch -d "@$_r2g_epoch" {} + 2>/dev/null || true
  done
  # Non-stage-prefixed outputs (updated SDCs, mem.json, …) newest of all.
  find "$ORFS_RESULTS_DIR" -type f ! -name "[1-6]_*" \
    -exec touch -d "@$((_r2g_epoch + 5))" {} + 2>/dev/null || true
fi

unset _restage_dir _restage_pick_run_dir _fb_marker _fb_pick _fb_staged \
      _r2g_now _r2g_epoch _r2g_stage _r2g_dir 2>/dev/null || true
