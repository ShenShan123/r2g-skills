#!/bin/bash
# V1 re-run with the amended checker (PROTOCOL amendments A1-A6). nice 10.
X=$1; W=/data/yangao/exp4_validation_20261107; cd $W
PY=/data/yangao/r2g-api-repair-cost-20261028/bin/graph-python; RAW=/data/r2g_n45_pilot500/nangate45_pilot500
{ [ -s gt_v1a/$X.pkl ] && [ gt_v1a/$X.pkl -nt code/gt_raw.py ] || nice -n 10 $PY -I code/gt_raw.py $RAW/$X /data/r2g_n45_pilot500/nangate45 gt_v1a/$X.pkl
  nice -n 10 $PY -I code/v1_check.py gt_v1a/$X.pkl /data/r2g_n45_pilot500/R2G_dataset_6labels_v1 $X encode_map.csv v1a/$X.json
  nice -n 10 $PY -I code/img_check.py $RAW/$X /data/yangao/orfs203/flow/platforms/nangate45 img_truth/$X img_npz_208/npz/$X.npz img_v1a/$X.json; } > logs/v1a_$X.log 2>&1
grep -h "^V1\|^IMG" logs/v1a_$X.log
