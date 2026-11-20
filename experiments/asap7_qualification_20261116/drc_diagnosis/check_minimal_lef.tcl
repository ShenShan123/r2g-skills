read_lef /home/yangao/r2g-skills/experiments/asap7_qualification_20261116/drc_diagnosis/asap7_tech_asym_v1_m4_only_candidate.lef
set tech [ord::get_db_tech]
puts "DBU [$tech getDbUnitsPerMicron]"
set m4 [$tech findLayer M4]
foreach rule [$m4 getTechLayerEolKeepOutRules] {
 puts "M4 EOL: side=[$rule getSideExt] backward=[$rule getBackwardExt] forward=[$rule getForwardExt]"
}
exit
