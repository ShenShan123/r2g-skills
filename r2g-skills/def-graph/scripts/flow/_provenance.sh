#!/usr/bin/env bash
set -euo pipefail

# usage: _provenance.sh <run_dir> <config_platform>
# Prints the EFFECTIVE platform for a backend run dir on stdout (one token).
#
# Provenance guard (failure-patterns.md #30): a discovered ODB/DEF is keyed to
# the platform it was BUILT on — recorded in the backend's run-meta.json — while
# constraints/config.mk is mutable round state that a campaign re-point
# (setup_rtl_designs.py --platform X --force) rewrites for the WHOLE corpus.
# Resolving libs from config.mk would key an old platform's dataset to another
# platform's liberty/LEF (per-platform cell_type_id vocabularies make that a
# silent-value defect, not an error). run-meta.json wins when the two disagree —
# INCLUDING over an explicit platform arg (2026-07-16: an explicit-but-wrong arg
# silently stamped a wrong-platform manifest; the DEF is what it is). Callers
# honor R2G_PLATFORM_FORCE=1 to restore arg-wins for deliberate reference builds.
#
# Shared by run_labels.sh / run_features.sh / run_graphs.sh — one copy, per the
# techlib lesson: a worker-local patch fixes one consumer and silently leaves
# the others wrong.

RUN_DIR="${1:-}"
PLATFORM="${2:-}"

if [[ -n "$RUN_DIR" && -f "$RUN_DIR/run-meta.json" ]]; then
  _meta_pl=$(sed -n 's/.*"platform"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
             "$RUN_DIR/run-meta.json" | head -1)
  if [[ -n "$_meta_pl" && "$_meta_pl" != "$PLATFORM" ]]; then
    echo "WARNING: requested/config platform=$PLATFORM contradicts the DEF's build" \
         "provenance ($_meta_pl in $(basename "$RUN_DIR")/run-meta.json) — using $_meta_pl" \
         "(build provenance wins; failure-patterns.md #30; R2G_PLATFORM_FORCE=1 to override)" >&2
    PLATFORM="$_meta_pl"
  fi
fi
printf '%s\n' "$PLATFORM"
