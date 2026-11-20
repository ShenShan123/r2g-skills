#!/bin/bash
# V3: rebuild one design from a counterfactual raw root, compare with the baseline rebuild. nice 10.
X=$1; VAR=$2; TRACE=${3:-0}; RAWV=${4:-$2}
W=/data/yangao/exp4_validation_20261107; PY=$W/venv_rebuild/bin/python; V=$W/v3/$VAR; mkdir -p $V/cmp $W/logs
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
PRE=""; [ "$TRACE" = 1 ] && PRE="strace -f -qq -s 4096 -e trace=openat,execve -e signal=none -o $V/strace_$X.txt"
{
  nice -n 10 $PRE $PY -I $W/code/v2_rebuild.py $W/v2/project $W/v2/ext_r2g2 $V/root1 $V/root2 $X --raw $W/v3/raw_$RAWV
  [ -f $V/root2/placement/B/$X.pt ] && nice -n 10 $PY -I $W/code/v2_compare.py $W/v2/main/root2 $V/root2 $X $V/cmp/$X.json
} > $W/logs/v3_${VAR}_$X.log 2>&1
grep -h "REBUILD\|CMP" $W/logs/v3_${VAR}_$X.log
