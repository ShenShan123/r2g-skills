# Independent per-instance-pin setup slack for the def-graph timing label definition (no parasitics,
# SDC read whole as given). v2: platform-general.
# env: TECH_LEF, SC_LEFS (space-separated), LIBS (space-separated), DEF, SDC, OUT
read_lef $::env(TECH_LEF)
foreach l $::env(SC_LEFS) { read_lef $l }
foreach l $::env(LIBS) { read_liberty $l }
read_def $::env(DEF)
read_sdc $::env(SDC)
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
