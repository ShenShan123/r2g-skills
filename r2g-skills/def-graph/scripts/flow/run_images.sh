#!/usr/bin/env bash
# Image (feature-map) dataset for one signed-off design -- the GCell-raster
# companion of run_graphs.sh. See scripts/extract/images/build_images.py.
#
#   run_images.sh <project-dir> [platform] [flow_variant]
#
# Same guards as run_graphs.sh, in the same order and with the same shared code:
#   * the newest backend run with a 6_final.def is selected (_select_run.sh) and
#     its platform taken from the run's own build record (_provenance.sh);
#   * the sign-off gate (signoff_gate.py, default enforce) must certify THAT
#     run's DEF -- an unsigned layout never becomes a dataset;
#   * labels/ are re-made unless their completion marker records THIS DEF's
#     digest (_stage_provenance.py) -- the image labels are the label stage's
#     own congestion grid and IR drop, never a second extraction.
# Features come from ONE pre-route stage of the same run:
#   R2G_IMAGE_FEATURE_STAGE = place (default, 3_place.odb) | cts | floorplan
# exported to images/<stage>.def with OpenROAD's write_def. The builder refuses
# a DEF with routed wires; the independent verifier (verify_images.py) must pass
# or the dataset is reported invalid.
#
# Writes <project>/images/{features.npz,labels.npz,image_meta.json,png/} and
# <project>/reports/image_dataset.json  {status: ok|invalid|skipped, ...}.
# Exit: 0 ok | 2 skipped (prerequisite missing) | 4 graph python unusable |
#       5 not signed off | 6 verifier failed
set -euo pipefail
PROJECT_DIR="${1:-}"; PLATFORM="${2:-}"; FLOW_VARIANT_ARG="${3:-}"
[[ -n "$PROJECT_DIR" ]] || { echo "usage: run_images.sh <project-dir> [platform] [flow_variant]" >&2; exit 1; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$HERE/../.." && pwd)"
IMG_SRC="$SKILL_DIR/scripts/extract/images"
CALLER_GRAPH_PYTHON="${R2G_GRAPH_PYTHON-}"
source "$HERE/_env.sh"
[[ -n "$CALLER_GRAPH_PYTHON" ]] && R2G_GRAPH_PYTHON="$CALLER_GRAPH_PYTHON"
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
REPORTS_DIR="$PROJECT_DIR/reports"; LABELS_DIR="$PROJECT_DIR/labels"; OUT="$PROJECT_DIR/images"
mkdir -p "$REPORTS_DIR"
REPORT="$REPORTS_DIR/image_dataset.json"
STAGE="${R2G_IMAGE_FEATURE_STAGE:-place}"
case "$STAGE" in place) ODB_NAME=3_place.odb;; cts) ODB_NAME=4_cts.odb;; floorplan) ODB_NAME=2_floorplan.odb;;
  *) echo "ERROR: R2G_IMAGE_FEATURE_STAGE must be place|cts|floorplan (got $STAGE)" >&2; exit 1;; esac

report() {  # status reason
  python3 - "$REPORT" "$1" "$2" "$STAGE" <<'PY'
import json, sys
p, st, why, stage = sys.argv[1:5]
json.dump({"status": st, "reason": why, "feature_stage": stage}, open(p, "w"), indent=1)
PY
}

CONFIG_MK="$PROJECT_DIR/constraints/config.mk"
if [[ -z "$PLATFORM" && -f "$CONFIG_MK" ]]; then
  PLATFORM=$(grep -E '^\s*(export\s+)?PLATFORM\b' "$CONFIG_MK" | head -1 | sed 's/.*=\s*//' | tr -d ' ' || true)
fi
PLATFORM="${PLATFORM:-sky130hd}"

DEF=""; RUN_DIR=""
for run in $(bash "$HERE/_select_run.sh" "$PROJECT_DIR/backend" "${FLOW_VARIANT_ARG:-}" 2>/dev/null || true); do
  for sub in final results; do
    [[ -z "$DEF" && -f "$run/$sub/6_final.def" ]] && { DEF="$run/$sub/6_final.def"; RUN_DIR="$run"; }
  done
  [[ -n "$DEF" ]] && break
done
[[ -n "$DEF" ]] || { report skipped "no 6_final.def -- backend not completed"; echo "SKIP: no 6_final.def" >&2; exit 2; }
[[ "${R2G_PLATFORM_FORCE:-0}" == "1" ]] || PLATFORM=$(bash "$HERE/_provenance.sh" "$RUN_DIR" "$PLATFORM")

GATE_MODE="${R2G_SIGNOFF_GATE:-enforce}"
if ! python3 "$HERE/signoff_gate.py" "$PROJECT_DIR" --run-dir "$RUN_DIR" --def "$DEF" --mode "$GATE_MODE"; then
  report invalid "signoff gate: not signed off (reports/signoff_gate.json)"
  echo "ERROR: not signed off -- no image dataset (R2G_SIGNOFF_GATE=warn builds anyway)" >&2
  exit 5
fi

FEAT_ODB=""
for sub in results final; do [[ -z "$FEAT_ODB" && -f "$RUN_DIR/$sub/$ODB_NAME" ]] && FEAT_ODB="$RUN_DIR/$sub/$ODB_NAME"; done
[[ -n "$FEAT_ODB" ]] || { report skipped "no $ODB_NAME in $RUN_DIR"; echo "SKIP: no $ODB_NAME" >&2; exit 2; }

PY="${R2G_GRAPH_PYTHON:-}"
if [[ -z "$PY" ]] || ! "$PY" -c "import numpy" >/dev/null 2>&1; then
  report skipped "R2G_GRAPH_PYTHON unusable (needs numpy)"; echo "ERROR: R2G_GRAPH_PYTHON='$PY' cannot import numpy" >&2; exit 4
fi

# fresh labels for THIS DEF (content digest), else re-run the label stage
if [[ ! -f "$LABELS_DIR/congestion_grid.csv" || ! -f "$REPORTS_DIR/labels_stats.json" ]] || \
   ! python3 "$HERE/_stage_provenance.py" check --stats "$REPORTS_DIR/labels_stats.json" --def "$DEF" --stage labels >/dev/null 2>&1; then
  echo "--- labels stale/missing: running run_labels.sh ---"
  bash "$HERE/run_labels.sh" "$PROJECT_DIR" "$PLATFORM" "$FLOW_VARIANT_ARG"
fi
[[ -f "$LABELS_DIR/congestion_grid.json" ]] || { report skipped "label stage produced no congestion grid"; exit 2; }

RESOLVED="$(bash "$HERE/resolve_platform_paths.sh" "$CONFIG_MK" "$PLATFORM" 2>/dev/null || true)"
export SC_LEF="$(echo "$RESOLVED" | sed -n 's/^SC_LEF=//p')"
export ADDITIONAL_LEFS="$(echo "$RESOLVED" | sed -n 's/^ADDITIONAL_LEFS=//p')"
[[ -n "$SC_LEF" ]] || { report skipped "SC_LEF unresolved for $PLATFORM"; exit 2; }

rm -rf "$OUT"; mkdir -p "$OUT"
FEAT_DEF="$OUT/${ODB_NAME%.odb}.def"
printf 'read_db %s\nwrite_def %s\n' "$FEAT_ODB" "$FEAT_DEF" > "$OUT/export_def.tcl"
"${OPENROAD_EXE:-openroad}" -no_init -exit "$OUT/export_def.tcl" > "$OUT/export_def.log" 2>&1 || true
[[ -s "$FEAT_DEF" ]] || { report invalid "write_def of $ODB_NAME failed (images/export_def.log)"; exit 6; }

echo "[images] run=$RUN_DIR stage=$STAGE"
"$PY" "$IMG_SRC/build_images.py" --feature-def "$FEAT_DEF" --feature-stage "$STAGE" \
      --labels-dir "$LABELS_DIR" --out-dir "$OUT" --design "$(basename "$PROJECT_DIR")"
set +e
"$PY" "$IMG_SRC/verify_images.py" "$OUT" --labels-dir "$LABELS_DIR" --final-def "$DEF" --json "$OUT/verify.json"
vrc=$?
set -e
python3 - "$REPORT" "$OUT" "$STAGE" "$RUN_DIR" <<'PY'
import json, sys
rep, out, stage, run = sys.argv[1:5]
v = json.load(open(out + "/verify.json"))
m = json.load(open(out + "/image_meta.json"))
json.dump({"status": v["status"], "feature_stage": stage, "run_dir": run, "shape": m["shape"],
           "features": list(m["features"]), "labels": list(m["labels"]),
           "verify": {"passed": v["passed"], "total": v["total"],
                      "failed_checks": [r["check"] for r in v["results"] if not r["ok"]]}},
          open(rep, "w"), indent=1)
PY
[[ $vrc -eq 0 ]] || { echo "ERROR: image verifier failed (images/verify.json)" >&2; exit 6; }
echo "[images] ok -> $OUT"
