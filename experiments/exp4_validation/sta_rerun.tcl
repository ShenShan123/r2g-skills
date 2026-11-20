# Independent OpenSTA rerun (V1 L4b): routed DEF + final SPEF + original SDC, propagated clocks.
# env: DESIGN_DIR TECH_DIR
set D $::env(DESIGN_DIR); set T $::env(TECH_DIR)
read_lef $T/lef/NangateOpenCellLibrary.tech.lef
read_lef $T/lef/NangateOpenCellLibrary.macro.mod.lef
read_liberty $T/lib/NangateOpenCellLibrary_typical.lib
read_def $D/5_route.def
read_sdc $D/constraint.sdc
set_propagated_clock [all_clocks]
read_spef $D/6_final.spef
puts "R2G_WS [sta::format_time [sta::worst_slack_cmd max] 6]"
puts "R2G_TNS [sta::format_time [sta::total_negative_slack_cmd max] 6]"
report_worst_slack -max -digits 6
report_tns -digits 6
