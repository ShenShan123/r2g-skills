read_lef /home/yangao/r2g_toolchain/OpenROAD-flow-scripts/flow/platforms/asap7/lef/asap7_tech_1x_201209.lef
set tech [ord::get_db_tech]
puts "DBU [$tech getDbUnitsPerMicron]"
set m4 [$tech findLayer M4]
foreach rule [$m4 getTechLayerEolKeepOutRules] {
 puts "M4 EOL: side=[$rule getSideExt] backward=[$rule getBackwardExt] forward=[$rule getForwardExt]"
}
exit
