#!/usr/bin/env bash
# launch_pilot.sh SERVER   -- ASAP7 pilot: loose period 1000 ps, 20% utilization, 3 h stage timeout (the other
# platforms' pilot setting), stock OpenROAD, qualification apparatus staged into /work.
set -euo pipefail
S=$1
if [ "$S" = 216 ]; then
  B=/proj/workarea/yangao/asap7_baseline; PKG=/proj/workarea/yangao/rtl-certified-handoff-0002
  EXTRA=(--workers 12 --cores-per-design 8)
else
  B=/data/yangao/asap7_baseline; PKG=$B/rtl-certified-handoff-0002
  EXTRA=(--workers 18 --cores-per-design 8 --pause-gb 120 --stop-gb 80
         --native-orfs /data/yangao/orfs203 --native-ld-library-path /home/yangao/r2g_toolchain/pylib311)
fi
cd $B
[ -e pilot ] && { echo "pilot exists, refusing"; exit 1; }
export R2G_STAGE_FILES="asap7_tech_asym.lef=$B/apparatus/asap7_tech_asym.lef;asap7_pin_window.lef=$B/apparatus/asap7sc7p5t_R_edge_pin_window.lef;pdn_m1m2m3.tcl=$B/apparatus/pdn_m1m2m3.tcl"
export R2G_EXTRA_MK="override export TECH_LEF = /work/asap7_tech_asym.lef
export SC_LEF = /work/asap7_pin_window.lef
export PDN_TCL = /work/pdn_m1m2m3.tcl
export VIA_IN_PIN_MIN_LAYER = M1
export VIA_IN_PIN_MAX_LAYER = M1"
setsid nohup python3 orfs_baseline.py --package $PKG --select $B/pilot.select --out $B/pilot --scratch $B/pilot_scratch \
  --platforms asap7 --periods asap7=1000 --utilization 20 --timeout 10800 --pdn-core-floor-um 10 "${EXTRA[@]}" \
  > $B/pilot.driver.log 2>&1 < /dev/null &
echo "started pid $!"
