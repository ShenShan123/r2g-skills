#!/bin/bash
# Full-corpus V1 (amended checker) for one design on memlab203, nice 10, single-threaded tools.
X=$1; W=/data/yangao/exp4_validation_20261107; cd $W
PY=/data/yangao/r2g-api-repair-cost-20261028/bin/graph-python; RAW=/data/r2g_n45_pilot500/nangate45_pilot500
TECH=/data/r2g_n45_pilot500/nangate45; P=/data/yangao/orfs203/flow/platforms/nangate45; DS=/data/r2g_n45_pilot500/R2G_dataset_6labels_v1
OR=/home/yangao/r2g_toolchain/bin/openroad
mkdir -p gt_v1a v1a sta img_truth/$X img_v1a lineage logs
{
  [ -s gt_v1a/$X.pkl ] || nice -n 10 $PY -I code/gt_raw.py $RAW/$X $TECH gt_v1a/$X.pkl
  nice -n 10 $PY -I code/v1_check.py gt_v1a/$X.pkl $DS $X encode_map.csv v1a/$X.json
  [ -s sta/$X.txt ] || DESIGN_DIR=$RAW/$X TECH_DIR=$TECH nice -n 10 timeout 3600 $OR -no_init -no_splash -exit -threads 1 code/sta_rerun.tcl > sta/$X.txt 2>&1
  [ -s img_truth/$X/power_truth.tsv ] || DESIGN_DIR=$RAW/$X OUT_DIR=img_truth/$X TECH_DIR=$P PYTHONHOME=/home/yangao/r2g_toolchain/oss-cad-suite \
    nice -n 10 timeout 3600 $OR -no_init -no_splash -exit -threads 1 -python code/img_gt_dump.py
  nice -n 10 $PY -I code/img_check.py $RAW/$X $P img_truth/$X img_npz_208/npz/$X.npz img_v1a/$X.json
  nice -n 10 $PY -I code/lineage_check.py $RAW/$X gt_v1a/$X.pkl $DS lineage/$X.json
} > logs/all500_$X.log 2>&1
echo "$X $(grep -h '^V1\|^IMG\|^LIN' logs/all500_$X.log | cut -c1-60 | tr '\n' '|')"
