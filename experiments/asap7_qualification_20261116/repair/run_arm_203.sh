#!/usr/bin/env bash
# run_arm_203.sh NAME -- one ASAP7 repair arm on memlab203 (native ORFS), identical to the baseline except for the
# arm's extra make lines. Inputs: $B/repairs/NAME/select (design names), $B/repairs/NAME/extra.mk (appended to
# R2G_EXTRA_MK after the apparatus lines). Output: $B/repairs/NAME/runs (pulled to 216 and signed off there).
set -euo pipefail
NAME=$1; B=/data/yangao/asap7_baseline; A=$B/repairs/$NAME
[ -f $A/select ] && [ -f $A/extra.mk ] || { echo "need $A/select and $A/extra.mk"; exit 1; }
# RESUME=1 (resume_arms.sh): relaunch over an existing runs/ -- the driver skips every design that already has a
# result.json and reruns interrupted ones from scratch; logs are appended.
[ -e $A/runs ] && [ "${RESUME:-0}" != 1 ] && { echo "$A/runs exists, refusing (RESUME=1 to resume)"; exit 1; }
printf "PERIOD_TAG=%s\nWORKERS=%s\nSIGN_JOBS=%s\nSPEC=%s\n" "${PERIOD_TAG:-1000}" "${WORKERS:-}" "${SIGN_JOBS:-}" "${SPEC:-}" > $A/launch.env
cd $B
SPEC=${SPEC:-$B/spec/spec_asap7.json}
sha256sum orfs_spec.py $SPEC apparatus/openroad_pinaccess $A/select $A/extra.mk | tee -a $A/launch_digests
export R2G_SPEC_TABLE=$SPEC
export R2G_STAGE_FILES="asap7_tech_asym.lef=$B/apparatus/asap7_tech_asym.lef;asap7_pin_window.lef=$B/apparatus/asap7sc7p5t_R_edge_pin_window.lef;pdn_m1m2m3.tcl=$B/apparatus/pdn_m1m2m3.tcl"
# optional $A/stage: extra name=/abs/src lines staged into /work (e.g. an arm's router tech LEF)
[ -f $A/stage ] && R2G_STAGE_FILES="$R2G_STAGE_FILES;$(grep -v "^\s*$" $A/stage | paste -sd';')" && sha256sum $(cut -d= -f2 $A/stage) | tee -a $A/launch_digests
export R2G_EXTRA_MK="override export TECH_LEF = /work/asap7_tech_asym.lef
export SC_LEF = /work/asap7_pin_window.lef
export PDN_TCL = /work/pdn_m1m2m3.tcl
export VIA_IN_PIN_MIN_LAYER = M1
export VIA_IN_PIN_MAX_LAYER = M1
$(cat $A/extra.mk)"
setsid nohup python3 orfs_spec.py --package $B/rtl-certified-handoff-0002 --select $A/select --out $A --scratch $A/scratch \
  --platforms asap7 --periods asap7=${PERIOD_TAG:-1000} --utilization 50 --timeout 21600 --pdn-core-floor-um 10 \
  --openroad-exe $B/apparatus/openroad_pinaccess --keep-all --workers ${WORKERS:-16} --cores-per-design 8 \
  --max-design-gb 60 --pause-gb 120 --stop-gb 80 --mem-pause-gb 180 --dispatch-gap-s 5 \
  --native-orfs /data/yangao/orfs203 --native-ld-library-path /home/yangao/r2g_toolchain/pylib311 \
  >> $A/driver.log 2>&1 < /dev/null &
echo "arm $NAME started $!"
