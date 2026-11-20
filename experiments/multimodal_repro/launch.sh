#!/usr/bin/env bash
# launch.sh JOBS.txt GPU PARALLEL -- each line of JOBS.txt: "<method> <seed> <train_frac>"; one result dir per line
# under $RUNS/<method>_f<frac>_s<seed>/ (skipped when metrics.json exists). Logs next to each result.
set -uo pipefail
J=$1; GPU=$2; P=${3:-3}
HERE=$(cd "$(dirname "$0")" && pwd)
export HERE GPU
export RUNS=${RUNS:-/home/yangao/mm_runs/n45_repro} DATA=${DATA:-/home/yangao/mm_data_n45}
export CACHE=${CACHE:-/home/yangao/mm_repro_cache} LAYNET_SRC=${LAYNET_SRC:-/home/yangao/mm_repro_upstream/congPred}
export PYG=/home/yangao/.conda/envs/gnn_blackwell/bin/python PYD=/home/yangao/r2g_toolchain/pyenvs/laynet/bin/python
one() {
  m=$1; s=$2; f=$3; o=$RUNS/${m}_f${f}_s${s}
  [ -f $o/metrics.json ] && { echo "SKIP $o"; return; }
  mkdir -p $o
  case $m in
    gpdl)    cmd="$PYG $HERE/run_circuitnet.py" ;;
    verihgn) cmd="$PYG $HERE/run_verihgn.py --cache $CACHE/verihgn" ;;
    lhnn)    cmd="$PYG $HERE/run_lhnn.py" ;;
    laynet)  cmd="$PYD $HERE/run_laynet.py --src $LAYNET_SRC --cache $CACHE/laynet" ;;
    laynet_nonetlist) cmd="$PYD $HERE/run_laynet.py --src $LAYNET_SRC --cache $CACHE/laynet --no-netlist" ;;
  esac
  PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True CUDA_VISIBLE_DEVICES=$GPU OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 $cmd --data $DATA --seed $s --train-frac $f --out $o > $o/log.txt 2>&1
  rc=$?
  echo "$(date +%H:%M) DONE rc=$rc $o $(tail -1 $o/log.txt | cut -c1-160)"
}
export -f one
xargs -P $P -L 1 bash -c 'one $0 $1 $2' < $J
