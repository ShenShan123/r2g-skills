# Reviewed C03490-only POST_GLOBAL_PLACE_TCL candidate.
# Original RTL independently drives io_oeb[15:8] from ~io_out[16].
# Replicate that inverter's INPUT cone; never buffer the resolved io_oeb net.
# Source hashes and the exhaustive four-state cone witness are checked by the
# campaign before this hook is installed. Port directions remain INOUT.
namespace eval r2g_zube_drivers {
  proc require {condition message} {
    if {![uplevel 1 [list expr $condition]]} {error "zube driver replication: $message"}
  }
  set block [ord::get_db_block]
  require {[$block getName] eq "zube_wrapper"} "unexpected design"
  set control [$block findBTerm {io_out[16]}]
  require {$control ne "NULL" && [$control getIoType] eq "INOUT"} "control interface differs"
  set input_net [$control getNet]
  require {$input_net ne "NULL" && ![$input_net isSpecial]} "invalid control net"
  set expected {};set bterms {};set nets {}
  for {set i 8} {$i<16} {incr i} {
    set name [format {io_oeb[%d]} $i];lappend expected $name
    set bt [$block findBTerm $name]
    require {$bt ne "NULL" && [$bt getIoType] eq "INOUT"} "output interface differs"
    set net [$bt getNet]
    require {$net ne "NULL" && ![$net isSpecial] && $net ne $input_net} "invalid output net"
    lappend bterms $bt;lappend nets $net
  }
  set unique [lsort -unique $nets]
  require {[llength $unique] in {1 8}} "partially split or unreviewed topology"
  foreach net $unique {
    set terms [$net getITerms]
    require {[llength $terms]==1} "output has additional internal connections"
    set output [lindex $terms 0];set inst [$output getInst]
    require {[[$output getMTerm] getName] eq "Y" && [[$inst getMaster] getName] eq "sky130_fd_sc_hd__inv_1"} "unexpected driver function"
    require {[[$inst findITerm A] getNet] eq $input_net} "driver reads an output instead of the original control"
  }
  if {[llength $unique]==8} {
    foreach net $unique {require {[llength [$net getBTerms]]==1} "split output still aliases another pin"}
    puts "R2G_ZUBE_DRIVERS already independent; no change"
  } else {
    set original [lindex $unique 0];set names {}
    foreach bt [$original getBTerms] {lappend names [$bt getName]}
    require {[lsort $names] eq [lsort $expected]} "aliased pins differ from the proven cone"
    set driver [[lindex [$original getITerms] 0] getInst];set master [$driver getMaster]
    # Preflight every destination before changing connectivity.
    for {set i 9} {$i<16} {incr i} {
      require {[$block findNet [format {io_oeb[%d]} $i]] eq "NULL"} "destination net already exists"
      require {[$block findInst "r2g_zube_oeb_inv_$i"] eq "NULL"} "destination cell already exists"
    }
    lassign [$driver getLocation] x y
    for {set i 9} {$i<16} {incr i} {
      set bt [lindex $bterms [expr {$i-8}]]
      set net [odb::dbNet_create $block [format {io_oeb[%d]} $i]]
      set clone [odb::dbInst_create $block $master "r2g_zube_oeb_inv_$i"]
      [$clone findITerm A] connect $input_net
      [$clone findITerm Y] connect $net
      foreach term [$driver getITerms] {
        if {[[$term getMTerm] getSigType] in {POWER GROUND} && [$term getNet] ne "NULL"} {
          [$clone findITerm [[$term getMTerm] getName]] connect [$term getNet]
        }
      }
      $bt disconnect;$bt connect $net
      $clone setLocation $x $y;$clone setPlacementStatus PLACED
      $clone setDoNotTouch true;$net setDoNotTouch true
    }
    $driver setDoNotTouch true;$original setDoNotTouch true
    puts "R2G_ZUBE_DRIVERS replicated 7 original inverters; 8 independent INOUT pins; input/control unchanged"
  }
}
