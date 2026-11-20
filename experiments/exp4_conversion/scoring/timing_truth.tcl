# Exp 4 (conversion) scorer: per-instance worst setup slack, written from CONTRACT.md 6.2.
# Independent of every converter under test. Env: TT_LEFS (space-separated), TT_LIB, TT_DEF,
# TT_SDC, TT_OUT. Output TSV: "PERIOD\t<min clock period>" then "<inst>\t<min slack or NA>".
foreach lef $::env(TT_LEFS) { read_lef $lef }
read_liberty $::env(TT_LIB)
read_def $::env(TT_DEF)
read_sdc $::env(TT_SDC)
set periods {}
foreach clk [all_clocks] { lappend periods [get_property $clk period] }
set f [open $::env(TT_OUT) w]
if {[llength $periods]} { puts $f "PERIOD\t[tcl::mathfunc::min {*}$periods]" } else { puts $f "PERIOD\tNA" }
foreach inst [get_cells *] {
  set worst ""
  foreach p [get_pins -of_objects $inst] {
    if {[catch {get_property $p slack_max} s]} { continue }
    if {![string is double -strict $s]} { continue }
    if {abs($s) >= 1e29} { continue }
    if {$worst eq "" || $s < $worst} { set worst $s }
  }
  if {$worst eq ""} { set worst NA }
  puts $f "[get_full_name $inst]\t$worst"
}
close $f
