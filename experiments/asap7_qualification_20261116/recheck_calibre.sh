#!/usr/bin/env bash
# Run on memlab216 after uploading the isolated inputs and private rules.
set -eu
root=${1:-/proj/workarea/yangao/asap7_qualification_20261116}
run=${2:-gcd_recheck}
module load calibre/2025.1_16.10
export MGLS_LICENSE_FILE=27020@sv01
export VAR_M4_YOFFSET=0 VAR_M5_XOFFSET=0 VAR_M6_YOFFSET=0 VAR_M7_XOFFSET=0
export pdk_path=/proj/workarea/LIB/asap7/asap7_pdk_r1p7
mkdir -p "$root/calibre_runs/$run/drc" "$root/calibre_runs/$run/lvs"
cd "$root/calibre_runs/$run/drc"
ln -sf "$root/gcd_calibre_input/gcd_topcell.gds" test.gds
cat > run.svrf <<EOF
DRC MAXIMUM RESULTS ALL
INCLUDE "$root/private_rules/calibredecks_r1p7/calibre/ruledirs/drc/drcRules_calibre_asap7.rul"
EOF
/usr/bin/time -v timeout 600 calibre -drc -hier -turbo 4 run.svrf > run.log 2>&1
cd ../lvs
cat > run.svrf <<EOF
LAYOUT PATH "$root/gcd_calibre_input/gcd_pinmapped.gds"
LAYOUT PRIMARY gcd
LAYOUT SYSTEM GDSII
SOURCE PATH "$root/gcd_calibre_input/gcd_device_complete.cdl"
SOURCE PRIMARY gcd
SOURCE SYSTEM SPICE
INCLUDE "$root/private_rules/calibredecks_r1p7/calibre/ruledirs/lvs/lvsRules_calibre_asap7.rul"
LVS POWER NAME "VDD" "VDD!" "vdd!"
LVS GROUND NAME "VSS" "VSS!" "vss!"
EOF
/usr/bin/time -v timeout 600 calibre -lvs -hier -turbo 4 run.svrf > run.log 2>&1
grep 'TOTAL DRC RuleChecks Executed:\|TOTAL DRC Results Generated:' ../drc/drc_report
grep 'LVS completed\.' run.log
# Calibre exit 0 alone is not a clean verdict; inspect both reports above.
