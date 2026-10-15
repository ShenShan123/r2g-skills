# Insert an antenna diode on each net the SIGNOFF DECK flags, bypassing
# OpenROAD's own antenna criterion.
#
# Why this cannot go through repair_antennas: on the apb_gpio layout
# `check_antennas -verbose` reports 0 net and 0 pin violations while the
# FreePDK45 deck reports 16, and the two numbers are not close. For PADDR[2] on
# metal4 the deck computes 306.47 (metal area over ONE minimum gate,
# agate 0.02625 um^2) and OpenROAD computes 1.49 (metal area over the whole
# BUF_X1 the net drives, and split per layer) -- a factor of ~200. No
# ANTENNAAREARATIO setting or -ratio_margin spans that, and repair_antennas has
# no -net option, so the net choice has to come from the deck's verdict and the
# insertion has to happen at the ODB level.
#
# Env: R2G_DIODE_ODB_IN, R2G_DIODE_ODB_OUT, R2G_DIODE_NETS (space separated),
#      R2G_DIODE_CELL (default ANTENNA_X1)
# HOOK form: fires from PRE_GLOBAL_ROUTE_TCL, which ORFS sources inside
# global_route_helper AFTER `load_design 4_cts.odb`. The design is already open
# and ORFS writes the ODB itself, so this must NOT read_db or write_db -- doing
# either here would either reload over the loaded design or write a stage
# artifact out of band, which is exactly what the stage-digest guard exists to
# catch. Nets arrive via R2G_DIODE_NETS because the deck's verdict comes from a
# PRIOR run of the same design: this strategy inherently costs two passes, one
# to learn which nets the deck flags and one to drain them.
set db  [ord::get_db]
set blk [[$db getChip] getBlock]
set cell [expr {[info exists ::env(R2G_DIODE_CELL)] ? $::env(R2G_DIODE_CELL) : "ANTENNA_X1"}]
set m [$db findMaster $cell]
if {$m eq "NULL"} { puts "R2G_DIODE: ERROR master $cell not found"; exit 1 }

# the diode's SIGNAL input is the pin to tie to the victim net
set sig_pin ""
foreach t [$m getMTerms] { if {[$t getSigType] eq "SIGNAL"} { set sig_pin [$t getName]; break } }
if {$sig_pin eq ""} { puts "R2G_DIODE: ERROR $cell has no SIGNAL pin"; exit 1 }

set added 0
set skipped 0
foreach net $::env(R2G_DIODE_NETS) {
    set n [$blk findNet $net]
    if {$n eq "NULL"} { puts "R2G_DIODE: skip (no such net) $net"; incr skipped; continue }
    # Seed the diode at the victim pin's own instance, so detailed_placement
    # legalizes it to a nearby site rather than dragging it across the die --
    # a diode placed far away lengthens the very metal it is meant to drain.
    set x 0; set y 0; set seeded 0
    foreach it [$n getITerms] {
        set inst [$it getInst]
        if {[$inst isPlaced]} { lassign [$inst getLocation] x y; set seeded 1; break }
    }
    if {!$seeded} {
        foreach bt [$n getBTerms] {
            foreach bp [$bt getBPins] {
                foreach box [$bp getBoxes] { set x [$box xMin]; set y [$box yMin]; set seeded 1; break }
                if {$seeded} break
            }
            if {$seeded} break
        }
    }
    if {!$seeded} { puts "R2G_DIODE: skip (no placed anchor) $net"; incr skipped; continue }
    set name "r2g_deck_diode_$added"
    if {[catch {
        set inst [odb::dbInst_create $blk $m $name]
        $inst setPlacementStatus PLACED
        $inst setLocation $x $y
        odb::dbITerm_connect [$inst findITerm $sig_pin] $n
    } msg]} { puts "R2G_DIODE: ERROR $net: $msg"; incr skipped; continue }
    puts "R2G_DIODE: + $name on $net at $x $y"
    incr added
}
puts "R2G_DIODE: added $added skipped $skipped insts=[llength [$blk getInsts]]"
if {$added > 0} {
    if {[catch {detailed_placement} msg]} { puts "R2G_DIODE: detailed_placement: $msg" }
    if {[catch {check_placement -verbose} msg]} { puts "R2G_DIODE: check_placement: $msg" }
}
