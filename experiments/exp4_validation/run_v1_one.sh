#!/bin/bash
# V1 for one design on memlab203: ground truth -> dataset check -> independent STA. nice 10.
X=$1; W=/data/yangao/exp4_validation_20261107; cd $W
PY=/data/yangao/r2g-api-repair-cost-20261028/bin/graph-python
RAW=/data/r2g_n45_pilot500/nangate45_pilot500; TECH=/data/r2g_n45_pilot500/nangate45
DS=/data/r2g_n45_pilot500/R2G_dataset_6labels_v1
mkdir -p gt v1 sta logs
{
  [ -s gt/$X.pkl ] || nice -n 10 $PY -I code/gt_raw.py $RAW/$X $TECH gt/$X.pkl
  nice -n 10 $PY -I code/v1_check.py gt/$X.pkl $DS $X encode_map.csv v1/$X.json
  [ -s sta/$X.txt ] || DESIGN_DIR=$RAW/$X TECH_DIR=$TECH nice -n 10 timeout 3600 /home/yangao/r2g_toolchain/bin/openroad -no_init -no_splash -exit -threads 1 code/sta_rerun.tcl > sta/$X.txt 2>&1
} > logs/v1_$X.log 2>&1
tail -1 logs/v1_$X.log | head -c 300; echo
