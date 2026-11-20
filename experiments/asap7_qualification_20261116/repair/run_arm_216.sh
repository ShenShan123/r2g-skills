#!/usr/bin/env bash
# run_arm_216.sh NAME -- one ASAP7 repair arm on memlab216 (container), same contract as run_arm_203.sh: the baseline
# frozen spec + apparatus + pin-access OpenROAD + --keep-all, plus $A/extra.mk lines and optional $A/stage files.
# SPEC=<json> overrides the spec, WORKERS=<n> the parallelism. Output $A/runs is signed off in place (serve: signoff only).
set -euo pipefail
NAME=$1; B=/proj/workarea/yangao/asap7_baseline; A=$B/repairs/$NAME
[ -f $A/select ] && [ -f $A/extra.mk ] || { echo "need $A/select and $A/extra.mk"; exit 1; }
# RESUME=1 (resume_arms.sh): relaunch over an existing runs/ -- the driver skips every design that already has a
# result.json and reruns interrupted ones from scratch; logs are appended.
[ -e $A/runs ] && [ "${RESUME:-0}" != 1 ] && { echo "$A/runs exists, refusing (RESUME=1 to resume)"; exit 1; }
printf "PERIOD_TAG=%s\nWORKERS=%s\nSIGN_JOBS=%s\nSPEC=%s\n" "${PERIOD_TAG:-1000}" "${WORKERS:-}" "${SIGN_JOBS:-}" "${SPEC:-}" > $A/launch.env
# PERIOD_TAG only names the containers/run dirs (every design takes its real period from the spec): two arms over the
# SAME designs must use different tags, or each driver's "clear stale same-named container" kills the other's work.
TAG=${PERIOD_TAG:-1000}
for n in $(cat $A/select); do podman ps --format "{{.Names}}" | grep -q "^${n}__asap7__p${TAG}$" && { echo "container collision: $n p$TAG"; exit 1; }; done
cd $B
SPEC=${SPEC:-$B/spec_pplfix/spec_asap7.json}
sha256sum orfs_spec.py $SPEC apparatus/openroad_pinaccess $A/select $A/extra.mk | tee -a $A/launch_digests
export R2G_SPEC_TABLE=$SPEC
export R2G_STAGE_FILES="asap7_tech_asym.lef=$B/apparatus/asap7_tech_asym.lef;asap7_pin_window.lef=$B/apparatus/asap7sc7p5t_R_edge_pin_window.lef;pdn_m1m2m3.tcl=$B/apparatus/pdn_m1m2m3.tcl"
[ -f $A/stage ] && R2G_STAGE_FILES="$R2G_STAGE_FILES;$(grep -v '^\s*$' $A/stage | paste -sd';')" && sha256sum $(cut -d= -f2 $A/stage) | tee -a $A/launch_digests
export R2G_EXTRA_MK="override export TECH_LEF = /work/asap7_tech_asym.lef
export SC_LEF = /work/asap7_pin_window.lef
export PDN_TCL = /work/pdn_m1m2m3.tcl
export VIA_IN_PIN_MIN_LAYER = M1
export VIA_IN_PIN_MAX_LAYER = M1
$(cat $A/extra.mk)"
setsid nohup python3 orfs_spec.py --package /proj/workarea/yangao/rtl-certified-handoff-0002 --select $A/select --out $A \
  --scratch $A/scratch --platforms asap7 --periods asap7=$TAG --utilization 50 --timeout 21600 --pdn-core-floor-um 10 \
  --openroad-exe $B/apparatus/openroad_pinaccess --keep-all --workers ${WORKERS:-6} --cores-per-design 8 \
  --max-design-gb 100 >> $A/driver.log 2>&1 < /dev/null &
echo "arm $NAME started $!"
module load calibre/2025.1_16.10 >/dev/null 2>&1
mkdir -p $A/runs $A/signoff
setsid nohup python3 signoff_batch.py --runs $A/runs --out $A/signoff --jobs ${SIGN_JOBS:-3} --drc-budget 21600 --watch \
  >> $A/signoff.out 2>&1 < /dev/null &
echo "signer started"
