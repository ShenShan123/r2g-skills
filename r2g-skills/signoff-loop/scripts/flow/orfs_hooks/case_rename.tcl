# Fires from SYNTH_CANONICALIZE_TCL (synth_canonicalize.tcl, after hierarchy -check).
# Validated dual-clean on the nangate45 repair arm (knob_ab/case_rename, C03945
# timer: `input COUNT` beside `reg count`). Applied by the agent only when the
# synthesized top really has such a pair (agent/r2g_agent/frontend.py
# case_collisions); failure-patterns.md "Frontend recipes".
#
# CDL/SPICE is case-insensitive (KLayout upper-cases names on read), so an
# internal net whose name differs from a PORT only by case is merged with that
# port in the reference netlist and LVS reports "Net COUNT[0] is not matching
# any net". Rename ONLY such non-port wires (append _r2g). Ports are never
# touched: renaming one would change the design's interface. Connectivity and
# logic are unchanged.
puts "R2G_CASE: start"
yosys proc
# Per-process scratch names: concurrent designs share /tmp.
set _r2g_ports "/tmp/r2g_case_[pid]_ports.txt"
set _r2g_wires "/tmp/r2g_case_[pid]_wires.txt"
yosys tee -q -o $_r2g_ports select -list x:*
yosys tee -q -o $_r2g_wires select -list w:*
proc r2g_read {f} {
  set fh [open $f r]; set d [read $fh]; close $fh
  set out {}
  foreach l [split $d "\n"] { set l [string trim $l]; if {$l ne "" && [string first "/" $l] > 0} { lappend out $l } }
  return $out
}
set ports {}
foreach p [r2g_read $_r2g_ports] {
  set m [lindex [split $p "/"] 0]; set n [join [lrange [split $p "/"] 1 end] "/"]
  dict lappend ports $m [string tolower $n]
  dict set exact $m,$n 1
}
set n_ren 0
foreach w [r2g_read $_r2g_wires] {
  set m [lindex [split $w "/"] 0]; set n [join [lrange [split $w "/"] 1 end] "/"]
  if {[info exists exact] && [dict exists $exact $m,$n]} { continue }
  if {[dict exists $ports $m] && [lsearch -exact [dict get $ports $m] [string tolower $n]] >= 0} {
    yosys cd $m
    yosys rename $n ${n}_r2g
    yosys cd ..
    puts "R2G_CASE: renamed $m/$n -> ${n}_r2g"
    incr n_ren
  }
}
file delete -force $_r2g_ports $_r2g_wires
puts "R2G_CASE: done, renamed $n_ren"
