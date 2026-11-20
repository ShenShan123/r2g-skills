#!/usr/bin/env bash
# Exp 4 (conversion) -- R2G arm, one design: shim project + the def-graph skill's documented
# stages. 0 model calls.
#
#   run_r2g_design.sh <skill_root> <design_dir> <platform_dir> <project_dir> <out_dir>
#
# skill_root  = the def-graph skill directory (r2g-skills/def-graph of the frozen commit)
# out_dir     = receives b_graph.pt, c_graph.pt (+ the manifest) copied from <project>/dataset
#
# Knobs set here (all documented in def-graph/SKILL.md / graph-dataset.md):
#   R2G_DEF / R2G_ODB / R2G_SPEF  pin the layout the shim prepared (one DEF for X and Y)
#   R2G_GRAPH_KIND=homo           the homogeneous Data format (the contract target, see PROTOCOL.md)
#   R2G_GRAPH_VARIANTS=bc         only the two views in scope
# run_graphs.sh runs the label and feature stages itself (they are missing on a fresh project).
set -euo pipefail
S=$(cd "$1" && pwd); D="$2"; P="$3"; J="$4"; O="$5"
mkdir -p "$O"
HERE=$(cd "$(dirname "$0")" && pwd)
t0=$(date +%s.%N)
bash "$HERE/make_project.sh" "$D" "$P" "$J"
t1=$(date +%s.%N)
export R2G_DEF="$J/inputs/6_final.def" R2G_ODB="$J/inputs/6_final.odb" R2G_SPEF="$J/inputs/6_final.spef"
export R2G_GRAPH_KIND=homo R2G_GRAPH_VARIANTS=bc
rc=0
bash "$S/scripts/flow/run_graphs.sh" "$J" nangate45 > "$J/run_graphs.log" 2>&1 || rc=$?
t2=$(date +%s.%N)
for f in b_graph.pt c_graph.pt graph_manifest.json; do
  [[ -f "$J/dataset/$f" ]] && cp "$J/dataset/$f" "$O/$f"
done
python3 - "$O/r2g_timing.json" "$t0" "$t1" "$t2" "$rc" <<'EOF'
import json, sys
p, t0, t1, t2, rc = sys.argv[1], *map(float, sys.argv[2:5]), int(sys.argv[5])
json.dump({"shim_seconds": round(t1 - t0, 2), "skill_seconds": round(t2 - t1, 2),
           "run_graphs_rc": rc}, open(p, "w"), indent=1)
EOF
exit $rc
