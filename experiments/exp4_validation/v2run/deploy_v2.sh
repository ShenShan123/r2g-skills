#!/bin/bash
# Deploy the v2 checker code + platform files to memlab203 (ssh stdin, no scp), sha256-verified.
set -euo pipefail
H=/home/yangao/r2g-skills/experiments/exp4_validation; V=/data/yangao/exp4_validation_20261107/exp4v2
cd $H
LIST="gt_raw.py match.py platforms_v2.py truth_defgraph.py adapter_defgraph.py v2_defgraph_check.py v1_images_dg.py v1_4stage_check.py sta_pins_dg.tcl sample_v2.json v2run/make_dg_project_v2.sh v2run/run_v2_design.sh v2run/run_v2_verifiers.sh defgraph/make_defgraph_project.sh"
tar cf - $LIST | ssh memlab203 "mkdir -p $V/code && tar -C $V/code -xf -"
a=$(sha256sum $LIST | sha256sum | cut -c1-16); b=$(ssh memlab203 "cd $V/code && sha256sum $LIST" | sha256sum | cut -c1-16)
[[ $a == $b ]] && echo "CODE_OK $a" || { echo MISMATCH; exit 1; }
ssh memlab203 "set -e; P=/data/yangao/orfs203/flow/platforms; cd $V && mkdir -p platform/nangate45 platform/sky130hd platform/gf180
for f in lef/NangateOpenCellLibrary.tech.lef lef/NangateOpenCellLibrary.macro.mod.lef lib/NangateOpenCellLibrary_typical.lib; do cp -n \$P/nangate45/\$f platform/nangate45/; done
for f in lib/sky130_fd_sc_hd__tt_025C_1v80.lib lef/sky130_fd_sc_hd.tlef lef/sky130_fd_sc_hd_merged.lef; do cp -n \$P/sky130hd/\$f platform/sky130hd/; done
for f in lib/gf180mcu_fd_sc_mcu9t5v0__ff_n40C_5v50.lib.gz lef/gf180mcu_5LM_1TM_9K_9t_tech.lef lef/gf180mcu_5LM_1TM_9K_9t_sc.lef; do cp -n \$P/gf180/\$f platform/gf180/; done
sha256sum platform/*/*"
