#!/bin/bash
# V4 for one design: inject (py39 frozen-equivalent torch), then V1 + dataset-own checks per copy. nice 10.
X=$1; W=/data/yangao/exp4_validation_20261107; cd $W
P39=$W/py39env/bin/python; GP=/data/yangao/r2g-api-repair-cost-20261028/bin/graph-python; DS=/data/r2g_n45_pilot500/R2G_dataset_6labels_v1
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
mkdir -p v4/results logs
{
  nice -n 10 $P39 -I code/v4_inject.py $DS gt_v1a/$X.pkl $X v4/copies
  for D in $(ls v4/copies); do
    [ -f v4/copies/$D/$X/paths.json ] || continue
    nice -n 10 $GP -I code/v1_check.py gt_v1a/$X.pkl $DS $X encode_map.csv v4/results/${D}__$X.v1.json --paths v4/copies/$D/$X/paths.json
    nice -n 10 $P39 -I code/v4_own_checks.py $W/v2/project $DS $X v4/copies/$D/$X/paths.json v4/results/${D}__$X.own.json
  done
} > logs/v4_$X.log 2>&1
grep -c "^V1\|^OWN" logs/v4_$X.log
