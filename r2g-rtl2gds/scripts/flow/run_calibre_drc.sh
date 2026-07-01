#!/usr/bin/env bash
set -euo pipefail

# usage: run_calibre_drc.sh <project-dir> [platform] [flow_variant]
#
# Runs SIGNOFF-GRADE Calibre DRC on a completed ORFS backend run, using the
# official (encrypted) ASAP7 Calibre deck. This is the AUTHORITATIVE asap7 DRC
# path — unlike the community KLayout `asap7.lydrc` deck (run_drc.sh), which is a
# reverse-engineering of the DRM and carries an irreducible false-violation floor
# (V*.M*.AUX / LIG / V0 tech-LEF-vs-deck artifacts; see references/failure-patterns.md
# "ASAP7 residual-DRC-by-design"). Calibre + the ASU deck is the only way to reach a
# genuinely clean-able asap7 DRC signoff.
#
# ── SCAFFOLD STATUS (2026-07-01) ────────────────────────────────────────────────
# The ASAP7 Calibre decks are NOT redistributable and are NOT on this machine (the
# PDK ships only placeholder READMEs at
#   $ASAP7_PDK_DIR/calibre/ruledirs/{drc,lvs,rcx}/README.txt).
# They must be requested from https://asap.asu.edu/download/ (.edu email + license
# agreement) and dropped in at the exact path this script resolves below. This
# script is therefore GUARDED: with no deck (or no Calibre) it writes
# calibre_drc_result.json{status:skipped} and exits 0 — a clean no-op — so it is
# safe to wire into the flow now and becomes live the moment the deck lands.
#
# ── KNOWN INTEGRATION RISKS (validate once the deck is in hand) ──────────────────
#  1. Calibre VERSION: the ASAP7 deck was tested with `aoi_cal_2017.4_19.14` and the
#     usage notes warn newer Calibre "may be incompatible". This machine has 2025.1.
#     Encrypted SVRF is version-sensitive, so the deck may refuse to load — SMOKE-TEST
#     first (R2G_CALIBRE_SMOKE=1 runs the deck on one design and reports load status).
#  2. LAYER MAP: the ORFS asap7 GDS layer/datatype numbering must match the deck's
#     expectations. If Calibre reports "empty layers" set CALIBRE_LAYERMAP=<file>.
#  3. TOP CELL: resolved from metadata.json top_module -> DESIGN_NAME; override with
#     CALIBRE_TOP_CELL if the merged GDS top differs.
#
# Env knobs:
#   ASAP7_PDK_DIR      default /proj/workarea/LIB/asap7/asap7_pdk_r1p7
#   CALIBRE_DRC_RULES  full path to drcRules_calibre_asap7.rul (overrides deck resolution)
#   CALIBRE_TOP_CELL   GDS primary/top cell name (overrides metadata/DESIGN_NAME)
#   CALIBRE_LAYERMAP   optional GDS layer-map file for `LAYOUT SYSTEM GDSII` remap
#   CALIBRE_EXE        calibre binary (else `calibre` on PATH / $MGC_HOME/bin/calibre)
#   CALIBRE_DRC_TIMEOUT seconds (default 7200)
# Results are collected into <project-dir>/drc/calibre/ + <project-dir>/drc/calibre_drc_result.json

PROJECT_DIR="${1:-}"
PLATFORM="${2:-asap7}"
if [[ -n "${3:-}" ]]; then
  FLOW_VARIANT="$3"
elif [[ -n "$PROJECT_DIR" && -d "$PROJECT_DIR" ]]; then
  FLOW_VARIANT="$(basename "$(cd "$PROJECT_DIR" && pwd)")"
else
  FLOW_VARIANT="base"
fi

# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_env.sh"

if [[ -z "$PROJECT_DIR" ]]; then
  echo "usage: run_calibre_drc.sh <project-dir> [platform]" >&2
  exit 1
fi
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
CONFIG_MK="$PROJECT_DIR/constraints/config.mk"
DRC_DIR="$PROJECT_DIR/drc"
CAL_DIR="$DRC_DIR/calibre"
RESULT_JSON="$DRC_DIR/calibre_drc_result.json"
mkdir -p "$CAL_DIR"

# ── helper: write a skipped/failed result JSON and exit 0 (never break the flow) ──
_emit_skip() {  # $1=status $2=reason
  python3 - "$RESULT_JSON" "$1" "$2" "$PLATFORM" <<'PY'
import json, sys
out, status, reason, platform = sys.argv[1:5]
json.dump({"status": status, "reason": reason, "platform": platform,
           "engine": "calibre", "violations": None}, open(out, "w"), indent=2)
open(out, "a").write("\n")
PY
  echo "Calibre DRC $1: $2"
  echo "Results: $RESULT_JSON"
}

# ── 1. resolve Calibre binary (guard) ──
CALIBRE_EXE="${CALIBRE_EXE:-}"
if [[ -z "$CALIBRE_EXE" ]]; then
  if command -v calibre >/dev/null 2>&1; then CALIBRE_EXE="$(command -v calibre)"
  elif [[ -n "${MGC_HOME:-}" && -x "$MGC_HOME/bin/calibre" ]]; then CALIBRE_EXE="$MGC_HOME/bin/calibre"
  fi
fi
if [[ -z "$CALIBRE_EXE" ]]; then
  _emit_skip skipped "calibre_not_found (set CALIBRE_EXE or MGC_HOME)"
  exit 0
fi

# ── 2. resolve the ASAP7 Calibre DRC deck (guard) ──
ASAP7_PDK_DIR="${ASAP7_PDK_DIR:-/proj/workarea/LIB/asap7/asap7_pdk_r1p7}"
CALIBRE_DRC_RULES="${CALIBRE_DRC_RULES:-$ASAP7_PDK_DIR/calibre/ruledirs/drc/drcRules_calibre_asap7.rul}"
if [[ ! -s "$CALIBRE_DRC_RULES" ]]; then
  # The real deck is absent (only the placeholder README ships in the repo). This
  # is the EXPECTED state until the deck is downloaded from asap.asu.edu.
  _emit_skip skipped "deck_missing:$CALIBRE_DRC_RULES (download from https://asap.asu.edu/download/ and drop in)"
  exit 0
fi

# ── 3. restage backend GDS (reuse the shared signoff restager) ──
if [[ ! -f "$CONFIG_MK" ]]; then _emit_skip skipped "no_config_mk:$CONFIG_MK"; exit 0; fi
DESIGN_NAME=$(grep 'DESIGN_NAME' "$CONFIG_MK" | head -1 | sed 's/.*=\s*//' | tr -d ' ')
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_restage_for_signoff.sh"
GDS_FILE="$ORFS_RESULTS_DIR/6_final.gds"
if [[ ! -f "$GDS_FILE" ]]; then
  _emit_skip skipped "no_gds:$GDS_FILE (run_orfs.sh first)"
  exit 0
fi

# ── 4. resolve the top/primary cell for the runset ──
TOP_CELL="${CALIBRE_TOP_CELL:-}"
if [[ -z "$TOP_CELL" && -f "$PROJECT_DIR/metadata.json" ]]; then
  TOP_CELL=$(python3 - "$PROJECT_DIR/metadata.json" <<'PY' 2>/dev/null || true
import json, sys
try: print(json.load(open(sys.argv[1])).get("top_module") or "")
except Exception: print("")
PY
)
fi
[[ -z "$TOP_CELL" ]] && TOP_CELL="$DESIGN_NAME"

# The ASAP7 deck references $pdk_path (see calibre/ruledirs/drc/.tcshenv). Export it.
export pdk_path="$ASAP7_PDK_DIR"

echo "Calibre DRC: design=$DESIGN_NAME top=$TOP_CELL platform=$PLATFORM"
echo "  deck: $CALIBRE_DRC_RULES"
echo "  gds:  $GDS_FILE"

# ── 5. build the batch runset (SVRF) ──
RESULTS_DB="$CAL_DIR/${DESIGN_NAME}.drc.results"
SUMMARY_RPT="$CAL_DIR/${DESIGN_NAME}.drc.summary"
RUNSET="$CAL_DIR/drc.runset.svrf"
: > "$RESULTS_DB"; : > "$SUMMARY_RPT"
{
  echo "// r2g-generated Calibre DRC runset ($DESIGN_NAME)"
  echo "LAYOUT PATH \"$GDS_FILE\""
  echo "LAYOUT PRIMARY \"$TOP_CELL\""
  if [[ -n "${CALIBRE_LAYERMAP:-}" && -f "$CALIBRE_LAYERMAP" ]]; then
    echo "LAYOUT SYSTEM GDSII MAP \"$CALIBRE_LAYERMAP\""
  else
    echo "LAYOUT SYSTEM GDSII"
  fi
  echo "DRC RESULTS DATABASE \"$RESULTS_DB\""
  echo "DRC SUMMARY REPORT \"$SUMMARY_RPT\" HIER"
  echo "DRC MAXIMUM RESULTS ALL"
  echo "INCLUDE \"$CALIBRE_DRC_RULES\""
} > "$RUNSET"

# R2G_CALIBRE_SMOKE=1 -> just verify the deck loads on this Calibre, don't full-run.
CAL_FLAGS=(-drc -hier -turbo)
[[ "${R2G_CALIBRE_SMOKE:-0}" == "1" ]] && CAL_FLAGS=(-drc -hier -check_only)

# ── 6. run Calibre under a killable process group + timeout ──
CALIBRE_DRC_TIMEOUT="${CALIBRE_DRC_TIMEOUT:-7200}"
RUN_LOG="$CAL_DIR/calibre_drc_run.log"
CAL_STATUS=0
set +e
setsid timeout --signal=TERM --kill-after=60 "$CALIBRE_DRC_TIMEOUT" \
  "$CALIBRE_EXE" "${CAL_FLAGS[@]}" "$RUNSET" > "$RUN_LOG" 2>&1
CAL_STATUS=$?
set -e
if [[ $CAL_STATUS -ne 0 ]]; then
  pkill -9 -f "calibre.*${DESIGN_NAME}.*drc" 2>/dev/null || true
fi

# Detect a deck/version load failure explicitly (the #1 integration risk) so the
# operator gets an actionable status instead of a generic non-zero.
if grep -qaiE "encrypt.*(version|incompatible)|not compatible|unable to (open|read) rule|ERROR: .*rule file|license" "$RUN_LOG" 2>/dev/null; then
  _emit_skip incompatible "calibre_deck_load_failed (version/license/rule-parse; see $RUN_LOG) — deck targets 2017.4, this Calibre is $($CALIBRE_EXE -version 2>/dev/null | head -1 | tr -d '/')"
  exit 0
fi
if [[ $CAL_STATUS -eq 124 || $CAL_STATUS -eq 137 ]]; then
  _emit_skip timeout "calibre_drc_timeout_${CALIBRE_DRC_TIMEOUT}s"
  exit 0
fi

# ── 7. extract the verdict from the results DB / summary ──
EXTRACT="${R2G_EXTRACT_CALIBRE_DRC:-$(dirname "${BASH_SOURCE[0]}")/../extract/extract_calibre_drc.py}"
python3 "$EXTRACT" "$PROJECT_DIR" "$RESULT_JSON" 2>/dev/null \
  || _emit_skip unknown "extract_failed (exit=$CAL_STATUS; see $RUN_LOG)"

echo "Results: $RESULT_JSON"
exit 0
