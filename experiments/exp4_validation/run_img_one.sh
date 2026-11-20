#!/bin/bash
# Image V1 for one design on memlab203 (independent dump + check). nice 10.
X=$1; W=/data/yangao/exp4_validation_20261107; cd $W
P=/data/yangao/orfs203/flow/platforms/nangate45; RAW=/data/r2g_n45_pilot500/nangate45_pilot500
mkdir -p img_truth/$X img_v1 logs
{
  [ -s img_truth/$X/power_truth.tsv ] || DESIGN_DIR=$RAW/$X OUT_DIR=img_truth/$X TECH_DIR=$P PYTHONHOME=/home/yangao/r2g_toolchain/oss-cad-suite \
    nice -n 10 timeout 3600 /home/yangao/r2g_toolchain/bin/openroad -no_init -no_splash -exit -threads 1 -python code/img_gt_dump.py
  nice -n 10 /data/yangao/r2g-api-repair-cost-20261028/bin/graph-python -I code/img_check.py $RAW/$X $P img_truth/$X img_npz_208/npz/$X.npz img_v1/$X.json
} > logs/img_$X.log 2>&1
tail -1 logs/img_$X.log
