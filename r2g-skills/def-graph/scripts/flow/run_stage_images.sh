#!/usr/bin/env bash
# Four-stage IMAGE dataset for one signed-off design -- the GCell-raster twin of run_stage_dataset.sh.
#
#   run_stage_images.sh <project-dir> [platform] [flow_variant]
#
# One image set per PREDICTION stage, named and cut exactly like the four-stage graph dataset: a stage is named
# for what is being predicted, and its features may only read the layout that existed before it.
#
#   stage       features read                      what the channels can contain
#   floorplan   nothing (no layout)                 all-zero channels: the no-spatial-information baseline
#   placement   2_floorplan.odb -> DEF             macros + IO pins only (std cells are unplaced, so skipped)
#   cts         3_place.odb     -> DEF             all five channels
#   route       4_cts.odb       -> DEF             all five channels, clock-tree buffers included
#
# All four share ONE grid (the congestion label's own sidecar) and ONE post-route label set; the wrapper proves it
# by comparing every stage's labels.npz arrays and grid digest. Each stage is built and independently verified by
# run_images.sh (same run selection, platform provenance, sign-off gate, label freshness and verifier), so the
# per-stage guarantees are exactly the single-stage dataset's.
#
# Writes <project>/stage_images/<stage>/{features.npz,labels.npz,image_meta.json,png/,verify.json},
# <project>/stage_images/reports/<stage>.json (each stage's run_images.sh report)
# and <project>/stage_images/stage_images.json (manifest) + reports/stage_image_dataset.json.
# Exit: 0 ok | 2 skipped (prerequisite missing) | 4 graph python unusable | 5 not signed off |
#       6 a stage's verifier failed or the stages disagree on grid/labels
set -euo pipefail
PROJECT_DIR="${1:-}"; PLATFORM="${2:-}"; FLOW_VARIANT_ARG="${3:-}"
[[ -n "$PROJECT_DIR" ]] || { echo "usage: run_stage_images.sh <project-dir> [platform] [flow_variant]" >&2; exit 1; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CALLER_GRAPH_PYTHON="${R2G_GRAPH_PYTHON-}"
source "$HERE/_env.sh"                       # same python resolution as run_images.sh / run_graphs.sh
[[ -n "$CALLER_GRAPH_PYTHON" ]] && R2G_GRAPH_PYTHON="$CALLER_GRAPH_PYTHON"
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
ROOT="$PROJECT_DIR/stage_images"
REPORT="$PROJECT_DIR/reports/stage_image_dataset.json"
mkdir -p "$PROJECT_DIR/reports"
rm -rf "$ROOT"; mkdir -p "$ROOT/reports"

# prediction stage -> run_images.sh feature stage (the DEF it may read)
STAGES=(floorplan:none placement:floorplan cts:place route:cts)
rc_all=0
for spec in "${STAGES[@]}"; do
  pred="${spec%%:*}"; feat="${spec##*:}"
  echo "=== stage $pred (features: $feat) ==="
  set +e
  R2G_IMAGE_FEATURE_STAGE="$feat" R2G_IMAGE_OUT_DIR="$ROOT/$pred" R2G_IMAGE_REPORT="$ROOT/reports/$pred.json" \
    bash "$HERE/run_images.sh" "$PROJECT_DIR" "$PLATFORM" "$FLOW_VARIANT_ARG"
  rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    # skipped / not signed off / python unusable apply to every stage alike: stop at the first, keep its code
    [[ $rc -eq 6 ]] && rc_all=6 || { rc_all=$rc; break; }
  fi
done

PY="${R2G_GRAPH_PYTHON:-python3}"
set +e
"$PY" - "$ROOT" "$REPORT" "$rc_all" <<'PY'
import hashlib, json, os, sys
import numpy as np
root, report, rc_all = sys.argv[1], sys.argv[2], int(sys.argv[3])
order = [("floorplan", "none"), ("placement", "2_floorplan"), ("cts", "3_place"), ("route", "4_cts")]
stages, problems, ref = {}, [], None
for pred, src in order:
    d = os.path.join(root, pred)
    rep = os.path.join(root, "reports", pred + ".json")
    r = json.load(open(rep)) if os.path.isfile(rep) else {"status": "missing"}
    entry = {"feature_source": src, "status": r.get("status"), "verify": r.get("verify")}
    meta_p = os.path.join(d, "image_meta.json")
    if os.path.isfile(meta_p):
        m = json.load(open(meta_p))
        fd = m["sources"].get("feature_def")
        entry.update(shape=m["shape"], grid_sha256=m["sources"]["grid"]["sha256"],
                     feature_def_sha256=fd["sha256"] if fd else None, feature_stage=m["feature_stage"],
                     feature_sums={k: v["sum"] for k, v in m["features"].items()})
        L = dict(np.load(os.path.join(d, "labels.npz")))
        if ref is None:
            ref = (pred, m["shape"], m["sources"]["grid"]["sha256"], L)
        else:
            if m["shape"] != ref[1] or m["sources"]["grid"]["sha256"] != ref[2]:
                problems.append("%s grid differs from %s" % (pred, ref[0]))
            if set(L) != set(ref[3]) or any(not np.array_equal(L[k], ref[3][k]) for k in L):
                problems.append("%s labels differ from %s" % (pred, ref[0]))
    if entry["status"] != "ok":
        problems.append("%s status %s" % (pred, entry["status"]))
    stages[pred] = entry
status = "ok" if not problems and rc_all == 0 else ("skipped" if rc_all in (2, 4) else "invalid")
out = {"status": status, "stages": stages, "problems": problems,
       "shared": {"grid": "labels/congestion_grid.json (one grid for all stages)",
                  "labels": "post-route congestion / congestion_raw / ir_drop, identical in every stage"},
       "leakage_rule": "a stage reads only the layout that existed before it (floorplan: none)"}
json.dump(out, open(os.path.join(root, "stage_images.json"), "w"), indent=1)
json.dump(out, open(report, "w"), indent=1)
print("[stage_images] %s  %s" % (status, "; ".join(problems) or "4 stages, shared grid + labels verified"))
sys.exit(0 if status == "ok" else (rc_all if rc_all else 6))
PY
rc=$?
set -e
exit $rc
