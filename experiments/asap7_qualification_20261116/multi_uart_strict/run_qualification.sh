#!/usr/bin/env bash
set -euo pipefail
repo=/home/yangao/r2g-skills
qual="$repo/experiments/asap7_qualification_20261116"
project="$qual/multi_uart_strict"
remote=/proj/workarea/yangao/asap7_qualification_20261116
export PATH=/usr/bin:/bin:/home/yangao/.local/bin:$PATH
export R2G_ALLOW_UNSUPPORTED_PLATFORM=1 ORFS_MAX_CPUS=4 ORFS_TIMEOUT=1800
export OPENROAD_EXE="$qual/reference_diagnosis/patched_openroad"
cd "$repo"
mkdir -p "$project"/{lint,sim,reports,signoff}
"$OPENROAD_EXE" -version > "$project/reports/openroad_version.txt"
sha256sum "$OPENROAD_EXE" > "$project/reports/openroad_sha256.txt"
/usr/bin/python3 r2g-skills/signoff-loop/scripts/project/validate_config.py "$project" "$project/reports/config_validation.json"
iverilog -g2012 -s uart -tnull "$project"/rtl/*.v > "$project/lint/iverilog.log" 2>&1
iverilog -g2012 -s uart_tb -o "$project/sim/uart_tb.out" "$project"/rtl/*.v "$project/tb/uart_tb.v" > "$project/sim/rtl_compile.log" 2>&1
vvp "$project/sim/uart_tb.out" > "$project/sim/simulation.log"
bash r2g-skills/signoff-loop/scripts/flow/run_orfs.sh "$project" asap7 qual_multi_uart_strict_20261116 > "$project/backend_driver.log" 2>&1
QUAL_PROJECT=multi_uart_strict QUAL_TOP=uart /usr/bin/klayout -b -r "$qual/prepare_design_gds.py"
QUAL_PROJECT=multi_uart_strict /usr/bin/klayout -b -r "$qual/verify_geometry.py" > "$project/reports/gds_geometry.log"
/usr/bin/python3 "$qual/prepare_variant_cdl.py" multi_uart_strict
sftp -q memlab216 <<SFTP
put -r $qual/multi_uart_strict_input $remote/
SFTP
ssh memlab216 "module load calibre/2025.1_16.10; python3 $remote/run_check.py drc multi_uart_strict --gds $remote/multi_uart_strict_input/drc.gds --flat" > "$project/signoff/drc_driver.json" &
drc_job=$!
ssh memlab216 "module load calibre/2025.1_16.10; python3 $remote/run_check.py lvs multi_uart_strict --gds $remote/multi_uart_strict_input/lvs.gds --cdl $remote/multi_uart_strict_input/source.cdl --top uart --property-limit 1024" > "$project/signoff/lvs_driver.json" &
lvs_job=$!
wait "$drc_job"
wait "$lvs_job"
sftp -q memlab216 <<SFTP
get -r $remote/calibre_runs/multi_uart_strict/drc $project/signoff/
get -r $remote/calibre_runs/multi_uart_strict/lvs $project/signoff/
SFTP
/usr/bin/python3 r2g-skills/signoff-loop/scripts/extract/extract_ppa.py "$project" "$project/reports/ppa.json"
/usr/bin/python3 r2g-skills/signoff-loop/scripts/reports/check_timing.py "$project"
final_netlist=("$project"/backend/RUN_*/results/6_final.v)
test "${#final_netlist[@]}" = 1
iverilog -g2012 -s uart_tb -o "$project/sim/gate_tb.out" "${final_netlist[0]}" "$project/tb/cells_from_liberty.v" "$project/tb/uart_tb.v" > "$project/sim/gate_compile.log" 2>&1
vvp "$project/sim/gate_tb.out" > "$project/sim/gate_simulation.log"
/usr/bin/python3 "$qual/lvs_diagnosis/run_uart_formal.py" multi_uart_strict
