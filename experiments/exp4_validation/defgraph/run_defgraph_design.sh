#!/usr/bin/env bash
# Run every def-graph product on one pilot500 design (memlab203, nice 10) and time each stage.
#   run_defgraph_design.sh <design> <tag>     -> $W/defgraph/projects/<tag>/<design>
set -uo pipefail
X=$1; TAG=${2:-base}; W=/data/yangao/exp4_validation_20261107; S=$W/defgraph/repo/r2g-skills/def-graph
RAW=/data/r2g_n45_pilot500/nangate45_pilot500/$X; J=$W/defgraph/projects/$TAG/$X
export R2G_ENV_FILE=/data/yangao/r2g-api-repair-cost-20261028/r2g_vs_bare/tools203_data.env
export R2G_GRAPH_PYTHON=/data/yangao/r2g-api-repair-cost-20261028/bin/graph-python R2G_SIGNOFF_GATE=warn R2G_GRAPH_KIND=both
export OPENROAD_EXE=/home/yangao/r2g_toolchain/bin/openroad OMP_NUM_THREADS=1 NUM_CORES=1
rm -rf "$J"; mkdir -p "$(dirname $J)"
T=$J.timing.tsv; : > $T
step() { local name=$1; shift; local t0=$(date +%s.%N); nice -n 10 "$@" > "$J.$name.log" 2>&1; local rc=$?; echo -e "$name\t$rc\t$(echo "$(date +%s.%N) - $t0" | bc)" >> $T; }
step shim bash $W/code/defgraph/make_defgraph_project.sh "$RAW" /data/yangao/orfs203/flow/platforms/nangate45/lef "$J"
step labels bash $S/scripts/flow/run_labels.sh "$J" nangate45
step features bash $S/scripts/flow/run_features.sh "$J" nangate45
step graphs bash $S/scripts/flow/run_graphs.sh "$J" nangate45
step stage4 bash $S/scripts/flow/run_stage_dataset.sh "$J" --platform nangate45
step images bash $S/scripts/flow/run_images.sh "$J" nangate45
echo "$X $(tr '\n' ' ' < $T)"
