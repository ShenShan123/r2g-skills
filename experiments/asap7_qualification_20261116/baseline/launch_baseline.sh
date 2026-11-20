#!/usr/bin/env bash
# launch_baseline.sh 216|203 -- ASAP7 frozen-spec dev baseline, one driver per server.
# Frozen spec (spec/spec_asap7.json), pin-access repair OpenROAD (apparatus/openroad_pinaccess), qualification apparatus
# staged into /work, every dataset intermediate kept (--keep-all), 6 h stage timeout, PDN-0185 retry at a 10 um core.
set -euo pipefail
S=$1
if [ "$S" = 216 ]; then
  B=/proj/workarea/yangao/asap7_baseline; PKG=/proj/workarea/yangao/rtl-certified-handoff-0002
  EXTRA=(--workers 8 --cores-per-design 8 --max-design-gb 200)
else
  B=/data/yangao/asap7_baseline; PKG=$B/rtl-certified-handoff-0002
  EXTRA=(--workers 18 --cores-per-design 8 --max-design-gb 60 --pause-gb 120 --stop-gb 80 --mem-pause-gb 180
         --dispatch-gap-s 5 --native-orfs /data/yangao/orfs203 --native-ld-library-path /home/yangao/r2g_toolchain/pylib311)
fi
cd $B
[ -e baseline ] && { echo "baseline exists, refusing"; exit 1; }
sha256sum orfs_spec.py spec/spec_asap7.json baseline.select apparatus/openroad_pinaccess | tee baseline.launch_digests
export R2G_SPEC_TABLE=$B/spec/spec_asap7.json
export R2G_STAGE_FILES="asap7_tech_asym.lef=$B/apparatus/asap7_tech_asym.lef;asap7_pin_window.lef=$B/apparatus/asap7sc7p5t_R_edge_pin_window.lef;pdn_m1m2m3.tcl=$B/apparatus/pdn_m1m2m3.tcl"
export R2G_EXTRA_MK="override export TECH_LEF = /work/asap7_tech_asym.lef
export SC_LEF = /work/asap7_pin_window.lef
export PDN_TCL = /work/pdn_m1m2m3.tcl
export VIA_IN_PIN_MIN_LAYER = M1
export VIA_IN_PIN_MAX_LAYER = M1"
setsid nohup python3 orfs_spec.py --package $PKG --select $B/baseline.select --out $B/baseline --scratch $B/baseline_scratch \
  --platforms asap7 --periods asap7=1000 --utilization 50 --timeout 21600 --pdn-core-floor-um 10 \
  --openroad-exe $B/apparatus/openroad_pinaccess --keep-all "${EXTRA[@]}" > $B/baseline.driver.log 2>&1 < /dev/null &
echo "started driver $!"
