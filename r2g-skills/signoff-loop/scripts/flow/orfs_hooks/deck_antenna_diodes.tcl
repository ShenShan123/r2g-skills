# Insert an antenna diode on each net the SIGNOFF DECK flagged.
#
# Fires from PRE_GLOBAL_ROUTE_TCL, which ORFS sources inside global_route_helper
# AFTER `load_design 4_cts.odb`, so the design is already open and ORFS writes
# the stage ODB itself: this must NOT read_db or write_db. The routing that
# follows connects the diode pins, exactly as it would for any other cell.
#
# WHY this exists rather than another knob on repair_antennas. On nangate45 the
# two tools do not compute the same quantity. For PADDR[2] on metal4 the
# FreePDK45 deck computes 306.47 (metal area over ONE minimum gate,
# agate 0.02625 um^2) while `check_antennas` computes 1.49 (metal area over the
# whole BUF_X1 the net drives, split per layer) -- a factor of ~200. On that
# layout `check_antennas -verbose` reports 0 net and 0 pin violations while the
# deck reports 16, so OpenROAD's violating set is EMPTY: it is not mis-ranking
# the nets, it cannot see them. Measured consequences:
#   * ANTENNAAREARATIO 300 -> 200 cleared 1 of 7 designs, and 3 of the failures
#     gained a diode while keeping every violation -- the threshold makes it act
#     but on nets of its own choosing.
#   * repair_antennas has no -net option (only -iterations, -ratio_margin,
#     -jumper_only, -diode_only, -allow_congestion), so the choice cannot be
#     handed to it.
# Hence the net list comes from the deck's own verdict, mapped back from the
# lyrdb violation polygons, and the insertion happens here.
#
# The net list is read from `antenna_nets.txt` beside this file rather than from
# an environment variable, because ORFS erases non-stage variables before the
# hook runs and a name that survives today is not a contract.
set _r2g_here [file dirname [file normalize [info script]]]
set _r2g_netfile [file join $_r2g_here antenna_nets.txt]
if { ![file exists $_r2g_netfile] } {
    puts "R2G_DIODE: ERROR no net list at $_r2g_netfile"
    return
}
set _r2g_nets {}
set _fh [open $_r2g_netfile r]
foreach _ln [split [read $_fh] "\n"] {
    set _ln [string trim $_ln]
    if { $_ln ne "" && ![string match "#*" $_ln] } { lappend _r2g_nets $_ln }
}
close $_fh

set _db [ord::get_db]
set _blk [[$_db getChip] getBlock]
set _cell [expr {[info exists ::env(R2G_DIODE_CELL)] ? $::env(R2G_DIODE_CELL) \
                                                     : "ANTENNA_X1"}]
set _m [$_db findMaster $_cell]
if { $_m eq "NULL" } {
    puts "R2G_DIODE: ERROR diode master $_cell not found"
    return
}
set _sig ""
foreach _t [$_m getMTerms] {
    if { [$_t getSigType] eq "SIGNAL" } { set _sig [$_t getName]; break }
}
if { $_sig eq "" } {
    puts "R2G_DIODE: ERROR $_cell has no SIGNAL pin"
    return
}

set _added 0
set _skipped 0
foreach _net $_r2g_nets {
    set _n [$_blk findNet $_net]
    if { $_n eq "NULL" } {
        puts "R2G_DIODE: skip (no such net) $_net"; incr _skipped; continue
    }
    # Seed the diode at the victim pin's own instance so detailed_placement
    # legalizes it to a nearby site. A diode legalized across the die lengthens
    # the very metal it is meant to drain.
    set _x 0; set _y 0; set _ok 0
    foreach _it [$_n getITerms] {
        set _inst [$_it getInst]
        if { [$_inst isPlaced] } {
            lassign [$_inst getLocation] _x _y; set _ok 1; break
        }
    }
    if { !$_ok } {
        foreach _bt [$_n getBTerms] {
            foreach _bp [$_bt getBPins] {
                foreach _box [$_bp getBoxes] {
                    set _x [$_box xMin]; set _y [$_box yMin]; set _ok 1; break
                }
                if { $_ok } break
            }
            if { $_ok } break
        }
    }
    if { !$_ok } {
        puts "R2G_DIODE: skip (no placed anchor) $_net"; incr _skipped; continue
    }
    set _name "r2g_deck_diode_$_added"
    if { [catch {
        set _inst [odb::dbInst_create $_blk $_m $_name]
        $_inst setPlacementStatus PLACED
        $_inst setLocation $_x $_y
        odb::dbITerm_connect [$_inst findITerm $_sig] $_n
    } _msg] } {
        puts "R2G_DIODE: ERROR $_net: $_msg"; incr _skipped; continue
    }
    puts "R2G_DIODE: + $_name on $_net at $_x $_y"
    incr _added
}
puts "R2G_DIODE: added $_added skipped $_skipped insts=[llength [$_blk getInsts]]"
if { $_added > 0 } {
    if { [catch {detailed_placement} _msg] } {
        puts "R2G_DIODE: detailed_placement: $_msg"
    }
    if { [catch {check_placement -verbose} _msg] } {
        puts "R2G_DIODE: check_placement: $_msg"
    }
}
