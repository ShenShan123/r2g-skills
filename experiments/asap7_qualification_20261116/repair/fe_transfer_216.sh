#!/usr/bin/env bash
# fe_transfer_216.sh synth|full -- transfer nangate45 FRONTEND repairs to ASAP7 (on 216, container).
#   synth: each design synthesised (make synth) with its donor's frontend transplanted, platform default, to size
#          it (these designs never synthesised in the pilot, so the frozen spec has no entry for them);
#   full:  the whole flow under spec_fe/spec_asap7.json (the frozen spec + entries sized from the synth pass).
# Donors: fe_donors.json {design: [recipe, donor_work_dir]}. One orfs_spec invocation per design (the transplant
# dir is per design), at most $P at a time. Everything else is the baseline's: patched OpenROAD, apparatus, --keep-all.
set -euo pipefail
PH=$1; P=${P:-6}; B=/proj/workarea/yangao/asap7_baseline; cd $B
A=$B/repairs/fe_transfer_$PH${ARM_SUFFIX:-}; mkdir -p $A
SPEC=$B/spec_fe/spec_asap7.json; [ "$PH" = full ] && [ ! -f $SPEC ] && { echo "no $SPEC"; exit 1; }
python3 -c "import json;[print(k+'\t'+v[1]) for k,v in json.load(open('${DONORS:-fe_donors.json}')).items()]" > $A/jobs.tsv
sha256sum orfs_spec.py apparatus/openroad_pinaccess ${DONORS:-fe_donors.json} $( [ $PH = full ] && echo $SPEC ) | tee $A/launch_digests
one() {
  n=$1; donor=$2; d=$A/$n; mkdir -p $d; echo $n > $d/select
  export R2G_TRANSPLANT_DIR=$donor
  export R2G_STAGE_FILES="asap7_tech_asym.lef=$B/apparatus/asap7_tech_asym.lef;asap7_pin_window.lef=$B/apparatus/asap7sc7p5t_R_edge_pin_window.lef;pdn_m1m2m3.tcl=$B/apparatus/pdn_m1m2m3.tcl"
  export R2G_EXTRA_MK="override export TECH_LEF = /work/asap7_tech_asym.lef
export SC_LEF = /work/asap7_pin_window.lef
export PDN_TCL = /work/pdn_m1m2m3.tcl
export VIA_IN_PIN_MIN_LAYER = M1
export VIA_IN_PIN_MAX_LAYER = M1
${EXTRA_MK:-}"
  [ -n "${STAGE_EXTRA:-}" ] && R2G_STAGE_FILES="$R2G_STAGE_FILES;$STAGE_EXTRA"
  if [ "$PH" = full ]; then export R2G_SPEC_TABLE=$SPEC; T=""; else unset R2G_SPEC_TABLE; T="--make-target synth"; fi
  python3 orfs_spec.py --package /proj/workarea/yangao/rtl-certified-handoff-0002 --select $d/select --out $d \
    --scratch $d/scratch --platforms asap7 --periods asap7=${PERIOD_TAG:-1000} --utilization 50 --timeout 21600 --pdn-core-floor-um 10 \
    --openroad-exe $B/apparatus/openroad_pinaccess --keep-all --workers 1 --cores-per-design 8 --max-design-gb 100 $T \
    > $d/driver.log 2>&1 || true
  tail -2 $d/driver.log | head -1
}
export -f one; export A B PH SPEC EXTRA_MK STAGE_EXTRA PERIOD_TAG
setsid nohup bash -c "cat $A/jobs.tsv | xargs -P $P -L 1 bash -c 'one \$0 \$1' > $A/xargs.log 2>&1" < /dev/null > /dev/null 2>&1 &
echo "fe_transfer $PH started"
