#!/usr/bin/env bash
# v2 checker proof, one design on memlab203 (nice 10, 1 thread): def-graph products + ground truth + checks.
#   run_v2_design.sh <platform> <design_dir>
set -uo pipefail
PL=$1; D=$2; X=$(basename "$D"); W=/data/yangao/exp4_validation_20261107; V=$W/exp4v2; C=$V/code
S=$W/defgraph/repo/r2g-skills/def-graph; PD=$V/platform/$PL; J=$V/projects/$X
export R2G_ENV_FILE=/data/yangao/r2g-api-repair-cost-20261028/r2g_vs_bare/tools203_data.env
export R2G_GRAPH_PYTHON=/data/yangao/r2g-api-repair-cost-20261028/bin/graph-python R2G_SIGNOFF_GATE=warn R2G_GRAPH_KIND=both
export OPENROAD_EXE=/home/yangao/r2g_toolchain/bin/openroad OMP_NUM_THREADS=1 NUM_CORES=1
PY=$R2G_GRAPH_PYTHON; OR=$OPENROAD_EXE
mkdir -p $V/projects $V/truth $V/results $V/logs; rm -rf "$J"
L=$V/logs/$X; T=$V/results/$X.timing.tsv; : > $T
step() { local name=$1; shift; local t0=$(date +%s.%N); nice -n 10 "$@" > "$L.$name.log" 2>&1; local rc=$?; echo -e "$name\t$rc\t$(echo "$(date +%s.%N) - $t0" | bc)" >> $T; }
step shim bash $C/v2run/make_dg_project_v2.sh "$D" $PL "$PD" "$J"
step labels bash $S/scripts/flow/run_labels.sh "$J" $PL
step features bash $S/scripts/flow/run_features.sh "$J" $PL
step graphs bash $S/scripts/flow/run_graphs.sh "$J" $PL
[[ $PL == nangate45 ]] && step stage4 bash $S/scripts/flow/run_stage_dataset.sh "$J" --platform $PL
step images bash $S/scripts/flow/run_images.sh "$J" $PL
# ---- ground truth (independent) + checks
read -r TL SL LB RD SDC <<<"$(python3 -c "import sys; sys.path.insert(0,'$C'); from platforms_v2 import PLATFORMS as P; c=P['$PL']; print(c['tech_lef'], ','.join(c['sc_lef']), ','.join(c['libs']), c['route_def'], c['sdc'])")"
SCL=$(echo $SL | tr , '\n' | sed "s#^#$PD/#" | tr '\n' ' '); LBS=$(echo $LB | tr , '\n' | sed "s#^#$PD/#" | tr '\n' ' ')
step sta env TECH_LEF=$PD/$TL SC_LEFS="$SCL" LIBS="$LBS" DEF=$D/$RD SDC=$D/$SDC OUT=$V/truth/$X.sta.tsv $OR -no_init -no_splash -exit -threads 1 $C/sta_pins_dg.tcl
step truth $PY -I $C/truth_defgraph.py $D $PL $PD $V/truth/$X.sta.tsv $V/truth/$X.pkl
step check_bf $PY -I $C/v2_defgraph_check.py $V/truth/$X.pkl $J/dataset $V/results/$X.bf.json
PLACE=$D/3_place.def
if [[ $PL != nangate45 ]]; then
  PLACE=$V/truth/$X.3_place.def
  printf 'read_db %s/3_place.odb\nwrite_def %s\n' "$D" "$PLACE" > $V/truth/$X.place.tcl
  step place_def $OR -no_init -no_splash -exit -threads 1 $V/truth/$X.place.tcl
fi
step check_img $PY -I $C/v1_images_dg.py $D $PD $V/truth/$X.pkl $J/images $V/results/$X.img.json $PL $PLACE
if [[ $PL == nangate45 ]]; then
  step gt_raw $PY -I $C/gt_raw.py $D /data/r2g_n45_pilot500/nangate45 $V/truth/$X.gt.pkl
  step check_4stage $PY -I $C/v1_4stage_check.py $D $V/truth/$X.gt.pkl $J/dataset_4stage/generated $V/results/$X.4stage.json
fi
echo "$X $(tr '\n' ' ' < $T)"
grep -h '^DGV2\|^DGIMG\|^4STG' $L.check_*.log | cut -c1-400
