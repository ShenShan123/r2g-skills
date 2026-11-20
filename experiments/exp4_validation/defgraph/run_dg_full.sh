#!/bin/bash
# def-graph products + our checks for one design (memlab203).  run_dg_full.sh <design> <tag>
X=$1; TAG=${2:-base}; W=/data/yangao/exp4_validation_20261107; cd $W
PY=/data/yangao/r2g-api-repair-cost-20261028/bin/graph-python; RAW=/data/r2g_n45_pilot500/nangate45_pilot500
J=$W/defgraph/projects/$TAG/$X; mkdir -p dgtruth dgv1/$TAG gt_v1a
[ "${SKIP_BUILD:-0}" = 1 ] || bash code/defgraph/run_defgraph_design.sh $X $TAG > /dev/null
{
[ -s gt_v1a/$X.pkl ] || nice -n 10 $PY -I code/gt_raw.py $RAW/$X /data/r2g_n45_pilot500/nangate45 gt_v1a/$X.pkl
[ -s dgtruth/$X.sta.tsv ] || DESIGN_DIR=$RAW/$X PLATFORM_DIR=dgtruth/plat OUT=dgtruth/$X.sta.tsv nice -n 10 /home/yangao/r2g_toolchain/bin/openroad -no_init -no_splash -exit -threads 1 code/sta_pins_dg.tcl > dgtruth/$X.sta.log 2>&1
[ -s dgtruth/$X.pkl ] || nice -n 10 $PY -I code/truth_defgraph.py $RAW/$X dgtruth/plat dgtruth/$X.sta.tsv dgtruth/$X.pkl
nice -n 10 $PY -I code/v1_defgraph_check.py dgtruth/$X.pkl $J/dataset dgv1/$TAG/$X.bf.json
nice -n 10 $PY -I code/v1_images_dg.py $RAW/$X dgtruth/plat dgtruth/$X.pkl $J/images dgv1/$TAG/$X.img.json
nice -n 10 $PY -I code/v1_4stage_check.py $RAW/$X gt_v1a/$X.pkl $J/dataset_4stage/generated dgv1/$TAG/$X.4stage.json
} > logs/dgfull_${TAG}_$X.log 2>&1
echo "$X | $(tr '\n' ' ' < $J.timing.tsv) | $(grep -h '^DGV1\|^DGIMG\|^4STG' logs/dgfull_${TAG}_$X.log | cut -c1-300 | tr '\n' ' ')"
