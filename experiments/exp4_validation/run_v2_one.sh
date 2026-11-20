#!/bin/bash
# V2 rebuild + compare for one design (memlab203, nice 10). Optional $2 = alternative venv python, $3 = tag.
X=$1; PY=${2:-/data/yangao/exp4_validation_20261107/venv_rebuild/bin/python}; TAG=${3:-main}
W=/data/yangao/exp4_validation_20261107; V=$W/v2/$TAG; mkdir -p $V/cmp $W/logs
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
{
  [ -f $V/root2/rebuild_$X.json ] || nice -n 10 $PY -I $W/code/v2_rebuild.py $W/v2/project $W/v2/ext_r2g2 $V/root1 $V/root2 $X
  nice -n 10 $W/venv_rebuild/bin/python -I $W/code/v2_compare.py /data/r2g_n45_pilot500/R2G_dataset_6labels_v1 $V/root2 $X $V/cmp/$X.json
} > $W/logs/v2_${TAG}_$X.log 2>&1
grep -h "REBUILD\|CMP" $W/logs/v2_${TAG}_$X.log
