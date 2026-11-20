#!/usr/bin/env bash
# mm_run.sh LIST.tsv [P] -- export placement DEFs (container) then build each sample (mm_venv), P in parallel
set -uo pipefail
M=${MM_ROOT:-/proj/workarea/yangao/mm_dataset}; L=$1; P=${2:-8}
PLAT=${PLAT:-sky130hd}; RUNS=${RUNS:-/proj/workarea/yangao/rerun_sky130hd_20261022/runs}
TLEF=${TLEF:-sky130_fd_sc_hd.tlef}; SLEF=${SLEF:-sky130_fd_sc_hd_merged.lef}
PD=/proj/workarea/yangao/r2g_toolchain/OpenROAD-flow-scripts/flow/platforms/$PLAT
mkdir -p $M/samples
podman run --rm -v $RUNS:/runs:ro -v $M/samples:/out -v /proj/workarea/yangao/mm_dataset/code:/code:ro \
  -i r2g-orfs:26Q3 bash /code/export.sh < $L
prep() {
  d=$1; run=$2
  [ -f $M/samples/$d/sample.json ] && { echo "SKIP $d"; return; }
  [ -s $M/samples/$d/3_place.def ] || { echo "NO_DEF $d"; return; }
  /proj/workarea/yangao/mm_venv/bin/python /proj/workarea/yangao/mm_dataset/code/mm_prep.py $run $M/samples/$d $d --platform-dir $PD --code /proj/workarea/yangao/mm_dataset/code \
    --place-def $M/samples/$d/3_place.def --platform $PLAT --tech-lef $TLEF --sc-lef $SLEF > $M/samples/$d/prep.log 2>&1
  echo "$(tail -1 $M/samples/$d/prep.log)"
}
export -f prep; export M PD PLAT TLEF SLEF
cut -f1,2 $L | xargs -P $P -L 1 bash -c 'prep $0 $1'
