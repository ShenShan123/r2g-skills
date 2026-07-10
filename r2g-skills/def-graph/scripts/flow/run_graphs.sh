#!/usr/bin/env bash
set -euo pipefail

# usage: run_graphs.sh <project-dir> [platform] [flow_variant]
# Builds PyG graph DATASETS (training-ready .pt files, variants b..f + the
# synthesis-netlist graph) from a completed backend run. Sits on top of the
# feature (X) and label (Y) stages — it runs them first when their CSVs are
# missing or older than the DEF — then assembles graphs joining both by name.
# Fail-soft like run_features.sh/run_labels.sh: missing prerequisites (incl.
# torch/torch_geometric) SKIP with a HINT instead of failing the flow.
# Results: <project-dir>/dataset/{b..f}_graph.pt, netlist_graph.pt,
#          graph_manifest.json (+ a copy at reports/graph_dataset.json)
# See references/graph-dataset.md.

PROJECT_DIR="${1:-}"
PLATFORM="${2:-}"
FLOW_VARIANT_ARG="${3:-}"

if [[ -z "$PROJECT_DIR" ]]; then
  echo "usage: run_graphs.sh <project-dir> [platform] [flow_variant]" >&2
  exit 1
fi

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GRAPH_SRC="$SKILL_DIR/scripts/extract/graph"
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_env.sh"

PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
CONFIG_MK="$PROJECT_DIR/constraints/config.mk"
DATASET_DIR="$PROJECT_DIR/dataset"
REPORTS_DIR="$PROJECT_DIR/reports"
mkdir -p "$REPORTS_DIR"

DESIGN_NAME="$(basename "$PROJECT_DIR")"
if [[ -f "$CONFIG_MK" ]]; then
  _dn=$(grep -E '^\s*(export\s+)?DESIGN_NAME' "$CONFIG_MK" | head -1 | sed 's/.*=\s*//' | tr -d ' ' || true)
  [[ -n "$_dn" ]] && DESIGN_NAME="$_dn"
  if [[ -z "$PLATFORM" ]]; then
    _pl=$(grep -E '^\s*(export\s+)?PLATFORM\b' "$CONFIG_MK" | head -1 | sed 's/.*=\s*//' | tr -d ' ' || true)
    PLATFORM="${_pl:-asap7}"
  fi
fi
PLATFORM="${PLATFORM:-asap7}"

skip() {  # reason
  echo "SKIP: $1" >&2
  printf '{"design":"%s","platform":"%s","variants":{},"status":"skipped","reason":"%s"}\n' \
    "$DESIGN_NAME" "$PLATFORM" "$1" > "$REPORTS_DIR/graph_dataset.json"
  exit 0
}

# --- Python with torch + torch_geometric (heavier than the base toolchain) --
# Override with R2G_GRAPH_PYTHON; probed fail-soft so machines without a torch
# env skip cleanly. Install recipe (large — keep OFF $HOME, use /proj):
#   python3 -m venv /proj/<you>/pyenvs/r2g-graph
#   .../pip install torch --index-url https://download.pytorch.org/whl/cpu
#   .../pip install torch_geometric pandas
GRAPH_PYTHON="${R2G_GRAPH_PYTHON:-python3}"
if ! "$GRAPH_PYTHON" -c "import torch, torch_geometric, pandas" >/dev/null 2>&1; then
  skip "no torch+torch_geometric in $GRAPH_PYTHON (set R2G_GRAPH_PYTHON; see install recipe in run_graphs.sh)"
fi

# --- Locate the DEF the same way run_features.sh does (freshness anchor) ----
DEF="${R2G_DEF:-}"
RUN_DIR=""
BACKEND_DIR="$PROJECT_DIR/backend"
if [[ -z "$DEF" && -d "$BACKEND_DIR" ]]; then
  for run in $(ls -d "$BACKEND_DIR"/RUN_* 2>/dev/null | sort -r); do
    for sub in final results; do
      [[ -z "$DEF" && -f "$run/$sub/6_final.def" ]] && { DEF="$run/$sub/6_final.def"; RUN_DIR="$run"; }
    done
    [[ -n "$DEF" ]] && break
  done
fi

# Provenance guard (failure-patterns.md #30): the discovered artifacts are keyed
# to the platform they were BUILT on (backend run-meta.json); config.mk is
# mutable round state a campaign re-point rewrites. An explicit platform arg
# always wins (guard skipped). Shared logic: _provenance.sh (one copy).
if [[ -z "${2:-}" && -n "$RUN_DIR" ]]; then
  PLATFORM=$(bash "$(dirname "${BASH_SOURCE[0]}")/_provenance.sh" "$RUN_DIR" "$PLATFORM")
fi
[[ -z "$DEF" || ! -f "$DEF" ]] && skip "no 6_final.def found — backend not completed/collected"

# --- Ensure fresh features/ + labels/ (run their stages when missing/stale) -
# Freshness is judged by the stage-completion marker (the stats JSON, written
# LAST by run_features.sh/run_labels.sh), not just an early CSV: a stage killed
# mid-way leaves fresh-looking CSVs but no marker, and building graphs on such
# a half-finished dir silently loses labels (2026-07-05 irdrop incident).
FEATURES_DIR="$PROJECT_DIR/features"
LABELS_DIR="$PROJECT_DIR/labels"
needs_stage() {  # dir probe_csv completion_marker_json
  [[ ! -f "$1/$2" ]] && return 0
  [[ "$1/$2" -ot "$DEF" ]] && return 0
  [[ ! -f "$3" ]] && return 0
  [[ "$3" -ot "$DEF" ]] && return 0
  return 1
}
if needs_stage "$FEATURES_DIR" "nodes_gate.csv" "$REPORTS_DIR/features_stats.json"; then
  echo "--- features stale/missing/incomplete: running run_features.sh ---"
  bash "$(dirname "${BASH_SOURCE[0]}")/run_features.sh" "$PROJECT_DIR" "$PLATFORM" "$FLOW_VARIANT_ARG"
fi
if needs_stage "$LABELS_DIR" "wirelength.csv" "$REPORTS_DIR/labels_stats.json"; then
  echo "--- labels stale/missing/incomplete: running run_labels.sh ---"
  bash "$(dirname "${BASH_SOURCE[0]}")/run_labels.sh" "$PROJECT_DIR" "$PLATFORM" "$FLOW_VARIANT_ARG"
fi
[[ -f "$FEATURES_DIR/nodes_gate.csv" ]] || skip "features stage produced no nodes_gate.csv"
[[ -f "$LABELS_DIR/wirelength.csv" ]] || skip "labels stage produced no wirelength.csv"

# --- Platform lib resolution for the netlist graph's cell-type vocabulary ---
RESOLVED="$(bash "$(dirname "${BASH_SOURCE[0]}")/resolve_platform_paths.sh" "$CONFIG_MK" "$PLATFORM" 2>/dev/null || true)"
LIB_FILES=$(echo "$RESOLVED" | sed -n 's/^LIB_FILES=//p')
ADDITIONAL_LIBS=$(echo "$RESOLVED" | sed -n 's/^ADDITIONAL_LIBS=//p')
export R2G_PLATFORM="$PLATFORM"
# The netlist-graph cell-type map must be built the SAME way as the feature stage
# (nodes_gate.py): lib_db from the FULL liberty (std + per-design macro libs), but the
# id space keyed on the STD-CELL-ONLY subset. So export BOTH: R2G_LIB_FILES (full) and
# R2G_SC_LIB_FILES (std-cell-only, subtracting the macro libs ORFS already folded into
# LIB_FILES — mirrors run_features.sh). Exporting only R2G_SC_LIB_FILES=$LIB_FILES made
# netlist_graph interleave each macro into the sorted std vocabulary, drifting std-cell
# ids off the feature graphs on every macro design (failure-patterns.md
# "Dataset-Extraction Silent-Value Defects" #12/#19).
export R2G_LIB_FILES="$LIB_FILES $ADDITIONAL_LIBS"
SC_LIB_FILES=""
for _lf in $LIB_FILES; do
  _is_macro_lib=0
  for _al in $ADDITIONAL_LIBS; do
    [[ "$_lf" == "$_al" ]] && _is_macro_lib=1 && break
  done
  [[ "$_is_macro_lib" == 0 ]] && SC_LIB_FILES="${SC_LIB_FILES:+$SC_LIB_FILES }$_lf"
done
export R2G_SC_LIB_FILES="$SC_LIB_FILES"

mkdir -p "$DATASET_DIR"
GRAPH_TIMEOUT="${GRAPH_TIMEOUT:-2400}"
VARIANTS="${R2G_GRAPH_VARIANTS:-bcdef}"

echo "Design: $DESIGN_NAME  Platform: $PLATFORM  Variants: $VARIANTS"
echo "DEF: $DEF"

timeout --signal=TERM --kill-after=30 "$GRAPH_TIMEOUT" \
  "$GRAPH_PYTHON" "$GRAPH_SRC/build_graphs.py" \
    --features "$FEATURES_DIR" --labels "$LABELS_DIR" \
    --design "$DESIGN_NAME" --out-dir "$DATASET_DIR" --variants "$VARIANTS" \
    --platform "$PLATFORM"

# --- Synthesis-netlist graph (optional — needs the yosys netlist) ----------
YOSYS_V=""
if [[ -n "$RUN_DIR" ]]; then
  for cand in 1_2_yosys.v 1_1_yosys.v; do
    for sub in results final; do
      [[ -z "$YOSYS_V" && -f "$RUN_DIR/$sub/$cand" ]] && YOSYS_V="$RUN_DIR/$sub/$cand"
    done
  done
fi
if [[ -n "$YOSYS_V" ]]; then
  timeout --signal=TERM --kill-after=30 "$GRAPH_TIMEOUT" \
    "$GRAPH_PYTHON" "$GRAPH_SRC/netlist_graph.py" "$YOSYS_V" "$DATASET_DIR/netlist_graph.pt" "$DESIGN_NAME" \
    || echo "  netlist_graph: FAILED (non-fatal)" >&2
else
  echo "NOTE: no yosys netlist found in $RUN_DIR — skipping netlist_graph.pt" >&2
fi

cp "$DATASET_DIR/graph_manifest.json" "$REPORTS_DIR/graph_dataset.json"
echo "Dataset:  $DATASET_DIR"
echo "Manifest: $REPORTS_DIR/graph_dataset.json"
