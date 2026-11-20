# Independent per-instance-pin setup slack for the def-graph timing label definition (no parasitics,
# SDC read as given). env: DESIGN_DIR PLATFORM_DIR OUT
set D $::env(DESIGN_DIR); set P $::env(PLATFORM_DIR)
read_lef $P/NangateOpenCellLibrary.tech.lef
read_lef $P/NangateOpenCellLibrary.macro.mod.lef
read_liberty $P/NangateOpenCellLibrary_typical.lib
read_def $D/5_route.def
read_sdc $D/constraint.sdc
set per {}
foreach c [all_clocks] { lappend per [get_property $c period] }
set f [open $::env(OUT) w]
if {[llength $per]} { puts $f "PERIOD\t[tcl::mathfunc::min {*}$per]" }
foreach inst [get_cells *] {
  set iname [get_full_name $inst]
  foreach p [get_pins -of_objects $inst] {
    if {[catch {get_property $p slack_max} s]} { set s nan }
    puts $f "$iname\t[get_property $p lib_pin_name]\t$s"
  }
}
close $f
