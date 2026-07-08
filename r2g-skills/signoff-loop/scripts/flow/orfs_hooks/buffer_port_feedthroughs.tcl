# buffer_port_feedthroughs.tcl — ORFS POST_GLOBAL_PLACE_TCL hook
#
# Splits port-to-port "feedthrough" nets (e.g. Verilog `assign out_port = in_port`)
# by inserting a real buffer cell in front of every aliased output port. Without
# this, one net carries 2+ top-level port names; SPICE cannot express two ports on
# one node, so Magic's GDS extraction keeps only one name and Netgen LVS fails
# "Top level cell failed pin matching" even though all devices and nets match.
#
# Why POST_GLOBAL_PLACE: yosys emits a buffer for these assigns, but ORFS
# global_place.tcl runs `remove_buffers` (under GPL_TIMING_DRIVEN=1) which deletes
# it and merges the two port nets — so any earlier insertion point is undone, and
# OpenROAD's own `buffer_ports` skips nets whose only pins are ports. This hook is
# the first point after the last `remove_buffers` in the flow. Inserted buffers
# are placed at their output port pin and legalized by detailed placement (3_5).
#
# Wire it per design in config.mk:
#   export POST_GLOBAL_PLACE_TCL = /abs/path/to/buffer_port_feedthroughs.tcl
#
# Idempotent: after splitting, no net has 2+ bterms, so a re-source is a no-op.
# See signoff-loop/references/failure-patterns.md, "sky130 LVS" (port-alias cause).

set block [ord::get_db_block]
# Resolve the buffer cell. MIN_BUF_CELL_AND_PORTS is the natural source, but ORFS
# stage scripts call `erase_non_stage_variables`, which scrubs it before POST
# hooks run — so fall back to a per-platform candidate list.
set fdbuf_candidates {}
if { [info exists ::env(MIN_BUF_CELL_AND_PORTS)] } {
  lappend fdbuf_candidates $::env(MIN_BUF_CELL_AND_PORTS)
}
lappend fdbuf_candidates \
  {sky130_fd_sc_hd__buf_4 A X} \
  {sky130_fd_sc_hs__buf_4 A X} \
  {BUF_X4 A Z} \
  {BUFx4_ASAP7_75t_R A Y} \
  {gf180mcu_fd_sc_mcu7t5v0__buf_4 I Z} \
  {sg13g2_buf_4 A X}
set fdbuf_master ""
foreach cand $fdbuf_candidates {
  lassign $cand fdbuf_cell fdbuf_in fdbuf_out
  set m [[ord::get_db] findMaster $fdbuf_cell]
  if { $m != "NULL" && $m != "" } { set fdbuf_master $m; break }
}
if { $fdbuf_master == "" } {
  utl::error FLW 901 "buffer_port_feedthroughs: no buffer master found (tried: $fdbuf_candidates)"
}

# fallback location: core center
set core [$block getCoreArea]
set core_cx [expr { ([$core xMin] + [$core xMax]) / 2 }]
set core_cy [expr { ([$core yMin] + [$core yMax]) / 2 }]

set fdbuf_count 0
foreach net [$block getNets] {
  if { [$net isSpecial] } { continue }
  set bterms [$net getBTerms]
  if { [llength $bterms] < 2 } { continue }
  set ins {}
  set outs {}
  foreach bt $bterms {
    if { [$bt getIoType] == "INPUT" } { lappend ins $bt } else { lappend outs $bt }
  }
  # Split every aliased output port onto its own net behind a buffer. If the net
  # has no input port (instance driver fanning out to 2+ output ports), keep the
  # first output on the original net and split the rest.
  set to_split $outs
  if { [llength $ins] == 0 } { set to_split [lrange $outs 1 end] }
  if { [llength $ins] > 1 } {
    puts "\[buffer_port_feedthroughs\] WARNING: net [$net getName] aliases\
      [llength $ins] input ports — cannot split inputs with a buffer; skipping inputs"
  }
  foreach bt $to_split {
    set pname [$bt getName]
    set newnet_name "${pname}_fdbuf"
    while { [$block findNet $newnet_name] != "NULL" } { append newnet_name "_" }
    set newnet [odb::dbNet_create $block $newnet_name]
    $bt disconnect
    $bt connect $newnet
    set inst [odb::dbInst_create $block $fdbuf_master "fdbuf_${fdbuf_count}"]
    [$inst findITerm $fdbuf_in] connect $net
    [$inst findITerm $fdbuf_out] connect $newnet
    # place at the output port pin (die edge); detailed placement legalizes it
    set bx $core_cx
    set by $core_cy
    foreach bpin [$bt getBPins] {
      set boxes [$bpin getBoxes]
      if { [llength $boxes] > 0 } {
        set box [lindex $boxes 0]
        set bx [$box xMin]
        set by [$box yMin]
        break
      }
    }
    # clamp into the core area so the legalizer has a nearby valid row
    if { $bx < [$core xMin] } { set bx [$core xMin] }
    if { $bx > [$core xMax] } { set bx [$core xMax] }
    if { $by < [$core yMin] } { set by [$core yMin] }
    if { $by > [$core yMax] } { set by [$core yMax] }
    $inst setLocation $bx $by
    $inst setPlacementStatus PLACED
    incr fdbuf_count
  }
}
puts "\[buffer_port_feedthroughs\] inserted $fdbuf_count $fdbuf_cell buffer(s) on port-feedthrough nets"
