#!/usr/bin/env bash
# Build the R2G2.0 four-stage heterogeneous dataset from one ORFS backend run.
#
#   run_stage_dataset.sh <project_dir> [--run-dir DIR] [--out-dir DIR] [options]
#
# Produces four HeteroData graphs -- floorplan / placement / cts / route -- that
# share one post-synthesis canonical topology and one set of post-route labels,
# and differ only in which physical snapshot was legal to read at that
# prediction cutoff. Contract: scripts/r2g2/upstream_docs/B_VIEW_DATASET_STRUCTURE.md.
#
# Pipeline (each step is also runnable on its own with --config):
#   signoff gate  -> stage_dataset/make_sample_config.py   (ORFS run -> config)
#                 -> r2g2/01_build_base_graph.py           (post-Yosys topology)
#                 -> r2g2/02_extract_features.py           (4 x 8 feature CSVs)
#                 -> stage_dataset/emit_timing_reports.py  (OpenSTA + V3 manifest)
#                 -> r2g2/03_extract_labels.py             (shared post-route Y)
#                 -> r2g2/04_assemble_heterograph.py       (4 x heterograph.pt)
#                 -> r2g2/05_build_stage_snapshots.py      (audit snapshots)
#                 -> r2g2/checks/validate_four_stage.py    (contract + leakage)
#
# This is the sibling of run_graphs.sh, not a replacement: run_graphs.sh builds
# the five b-f views from 6_final.def only. Both read the same signoff gate.
#
# Env: R2G_SIGNOFF_GATE (enforce|strict|warn|off), R2G_GRAPH_PYTHON (torch venv),
#      R2G2_MAX_PATHS (OpenSTA path cap), ORFS_ROOT/OPENROAD_EXE via _env.sh.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# $HERE is <skill>/scripts/flow, so the skill root is two levels up.
SKILL_DIR="$(cd "$HERE/../.." && pwd)"
R2G2_DIR="$SKILL_DIR/scripts/r2g2"
ADAPT_DIR="$SKILL_DIR/scripts/stage_dataset"

PROJECT_DIR=""
RUN_DIR=""
OUT_DIR=""
PLATFORM=""
IRDROP_SP=""
SKIP_TIMING=0
SKIP_IRDROP=1          # no PDNSim SPICE export in stock OpenROAD; see below
FORCE=0
STAGES="all"

usage() { sed -n '2,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-dir)    RUN_DIR="$2"; shift 2 ;;
    --out-dir)    OUT_DIR="$2"; shift 2 ;;
    --platform)   PLATFORM="$2"; shift 2 ;;
    --irdrop-sp)  IRDROP_SP="$2"; SKIP_IRDROP=0; shift 2 ;;
    --skip-timing) SKIP_TIMING=1; shift ;;
    --stages)     STAGES="$2"; shift 2 ;;
    --force)      FORCE=1; shift ;;
    -h|--help)    usage 0 ;;
    -*)           echo "unknown option: $1" >&2; usage 2 ;;
    *)            PROJECT_DIR="$1"; shift ;;
  esac
done

[[ -n "$PROJECT_DIR" ]] || { echo "ERROR: project_dir is required" >&2; usage 2; }
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"

# shellcheck source=/dev/null
source "$HERE/_env.sh" 1>&2
export ORFS_ROOT FLOW_DIR

PY="${R2G_GRAPH_PYTHON:-}"
if [[ -z "$PY" || ! -x "$PY" ]]; then
  echo "ERROR: the four-stage builder needs the torch venv." >&2
  echo "HINT: export R2G_GRAPH_PYTHON=/path/to/venv/bin/python (see references/env.local.sh)" >&2
  exit 3
fi
for module in torch torch_geometric; do
  "$PY" -c "import $module" 2>/dev/null || {
    echo "ERROR: $PY cannot import $module" >&2; exit 3; }
done

# Pick the newest complete four-stage run unless one was named.
if [[ -z "$RUN_DIR" ]]; then
  RUN_DIR="$("$PY" - "$PROJECT_DIR" <<'PYEOF'
import os, sys
project = sys.argv[1]
need = ("2_floorplan.odb", "3_place.odb", "4_cts.odb", "5_route.odb", "6_final.spef")
backend = os.path.join(project, "backend")
best = ""
for entry in sorted(os.scandir(backend), key=lambda e: e.name) if os.path.isdir(backend) else []:
    results = os.path.join(entry.path, "results")
    if all(os.path.exists(os.path.join(results, f)) for f in need):
        best = entry.path
print(best)
PYEOF
)"
  [[ -n "$RUN_DIR" ]] || {
    echo "ERROR: no backend run under $PROJECT_DIR has all four stage .odb + SPEF" >&2
    echo "HINT: the four-stage dataset needs 2_floorplan/3_place/4_cts/5_route .odb" >&2
    exit 4; }
fi
RUN_DIR="$(cd "$RUN_DIR" && pwd)"
OUT_DIR="${OUT_DIR:-$PROJECT_DIR/dataset_4stage}"
mkdir -p "$OUT_DIR"

echo "[4stage] project=$PROJECT_DIR"
echo "[4stage] run=$RUN_DIR"
echo "[4stage] out=$OUT_DIR"

# ---- signoff gate -----------------------------------------------------------
# Same rule as run_graphs.sh: a 5_route.def alone is not sign-off. Labels are
# post-route supervision, so an unsigned run must not silently become a dataset.
GATE_MODE="${R2G_SIGNOFF_GATE:-enforce}"
mkdir -p "$OUT_DIR/reports"
GATE_REPORT="$PROJECT_DIR/reports/signoff_gate.json"
if [[ "$GATE_MODE" != "off" ]]; then
  # Bind the gate to the SAME run the stage DEFs come from. The DEF passed is
  # that run's own 6_final.def -- the layout the DRC/LVS/route evidence actually
  # covers. The stage DEFs we re-export from .odb are downstream of it, so
  # certifying the run certifies them; certifying a regenerated file would not.
  GATE_FLAGS=()
  [[ -f "$RUN_DIR/results/6_final.def" ]] && GATE_FLAGS+=(--def "$RUN_DIR/results/6_final.def")
  if ! python3 "$HERE/signoff_gate.py" "$PROJECT_DIR" \
        --run-dir "$RUN_DIR" --mode "$GATE_MODE" "${GATE_FLAGS[@]}" >&2; then
    echo "ERROR: signoff gate failed; see $GATE_REPORT" >&2
    echo "HINT: R2G_SIGNOFF_GATE=warn builds anyway with the reason recorded." >&2
    exit 5
  fi
  # Keep the verdict beside the dataset too, so the artifacts carry their own
  # provenance even when read away from the project dir.
  [[ -f "$GATE_REPORT" ]] && cp "$GATE_REPORT" "$OUT_DIR/reports/signoff_gate.json"
else
  echo "[4stage] signoff gate: OFF (R2G_SIGNOFF_GATE=off)" >&2
fi

run_step() {  # run_step <label> <command...>
  local label="$1"; shift
  if [[ "$STAGES" != "all" && ",$STAGES," != *",$label,"* ]]; then
    echo "[4stage] skip $label (--stages $STAGES)"; return 0
  fi
  echo "[4stage] === $label ==="
  "$@"
}

CONFIG=""
config_path() {
  "$PY" - "$OUT_DIR" <<'PYEOF'
import glob, os, sys
matches = [p for p in glob.glob(os.path.join(sys.argv[1], "*.json"))
           if os.path.basename(p) != "manifest.json"]
print(matches[0] if len(matches) == 1 else "")
PYEOF
}

# ---- config + stage DEFs ----------------------------------------------------
MAKE_ARGS=(--run-dir "$RUN_DIR" --out-dir "$OUT_DIR")
[[ -n "$PLATFORM" ]] && MAKE_ARGS+=(--platform "$PLATFORM")
[[ "$FORCE" == "1" ]] && MAKE_ARGS+=(--force)
run_step config "$PY" "$ADAPT_DIR/make_sample_config.py" "${MAKE_ARGS[@]}"

CONFIG="$(config_path)"
[[ -n "$CONFIG" ]] || { echo "ERROR: no sample config in $OUT_DIR" >&2; exit 6; }
echo "[4stage] config=$CONFIG"

# ---- topology + features ----------------------------------------------------
run_step base     "$PY" "$R2G2_DIR/01_build_base_graph.py" --config "$CONFIG"
run_step features "$PY" "$R2G2_DIR/02_extract_features.py" --config "$CONFIG"

# ---- timing sources (optional but gated when present) -----------------------
if [[ "$SKIP_TIMING" == "0" ]]; then
  if ! run_step timing "$PY" "$ADAPT_DIR/emit_timing_reports.py" \
        --config "$CONFIG" --max-paths "${R2G2_MAX_PATHS:-10000}" --update-config; then
    # Fail-soft by column, not by dataset: pin/io_pin slack stays NaN with
    # valid=0 and the manifest says why, instead of aborting the other labels.
    echo "[4stage] WARN: OpenSTA reports unavailable; timing labels stay NaN/valid=0" >&2
  fi
else
  echo "[4stage] timing: skipped (--skip-timing)"
fi

# ---- labels -----------------------------------------------------------------
LABEL_ARGS=(--config "$CONFIG")
if [[ "$SKIP_IRDROP" == "1" ]]; then
  # PDNSim in stock OpenROAD exposes -voltage_file/-error_file but no SPICE
  # export, so VDD_extracted.sp -- the only source R2G2.0 accepts for the
  # ir_drop_mV label -- cannot be produced here. Pass --irdrop-sp when a build
  # that can export one is available; otherwise the column is honestly absent.
  LABEL_ARGS+=(--skip-irdrop)
  # Reported inside run_step so --stages runs that skip labels don't claim a
  # decision they never made.
  IRDROP_NOTE="[4stage] irdrop: skipped (no VDD_extracted.sp; ir_drop_mV -> NaN/valid=0)"
else
  LABEL_ARGS+=(--irdrop-sp "$IRDROP_SP")
  IRDROP_NOTE="[4stage] irdrop: solving from $IRDROP_SP"
fi
run_step labels bash -c 'echo "$1"; shift; exec "$@"' _ "$IRDROP_NOTE" \
  "$PY" "$R2G2_DIR/03_extract_labels.py" "${LABEL_ARGS[@]}"

# ---- assemble + snapshots + validate ----------------------------------------
run_step assemble  "$PY" "$R2G2_DIR/04_assemble_heterograph.py" --config "$CONFIG"
run_step snapshots "$PY" "$R2G2_DIR/05_build_stage_snapshots.py" --config "$CONFIG"
run_step validate  "$PY" "$R2G2_DIR/checks/validate_four_stage.py" --config "$CONFIG"

GEN="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1]))['output_dir'])" "$CONFIG")"
echo "[4stage] DONE: $GEN"
echo "[4stage] graphs: $GEN/stages/{floorplan,placement,cts,route}/heterograph.pt"
echo "[4stage] contract check: $GEN/four_stage.validation.json"
