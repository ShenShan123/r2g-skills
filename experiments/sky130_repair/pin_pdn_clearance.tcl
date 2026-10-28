# PRE_IO_PLACEMENT_TCL for the diagnosed Sky130HD m3 edge-pin/PDN conflict.
# Add placement exclusions only. Do not alter PDN, connectivity, RTL or checks.
if {$::env(PLATFORM) ne "sky130hd" || $::env(IO_PLACER_H) ne "met3"} {
  error "pin_pdn_clearance requires the diagnosed sky130hd/met3 IO configuration"
}
namespace eval r2g_pdn_pins {
  set block [ord::get_db_block]
  set tech [ord::get_db_tech]
  set layer [$tech findLayer met3]
  set units [$block getDbUnitsPerMicron]
  set width [$layer getWidth]
  set spacing [$layer getSpacing]
  set grid [$tech getManufacturingGrid]
  # Freeze the exact rule regime that was diagnosed; no guessed technology
  # fallback or widening/narrowing of a DRC acceptance threshold.
  if {$width != 300 || $spacing != 300 || $units != 1000} {
    error "unexpected met3 rules/units; requires a new geometry diagnosis"
  }
  set length [expr {int(ceil(double([$layer getArea])/$width/$grid))*$grid}]
  if {$length != 800} {error "unexpected horizontal pin length"}
  set_pin_length -hor_length [expr {double($length)/$units}]
  set die [$block getDieArea]
  set xmin [$die xMin];set xmax [$die xMax]
  set ymin [$die yMin];set ymax [$die yMax]
  set halo [expr {$spacing+$width/2+$grid}]
  set intervals [dict create left {} right {}]
  proc add_rect {x0 y0 x1 y1} {
    variable intervals;variable xmin;variable xmax;variable ymin;variable ymax
    variable length;variable spacing;variable halo
    set lo [expr {max($ymin,$y0-$halo)}]
    set hi [expr {min($ymax,$y1+$halo)}]
    if {$hi<=$lo} {return}
    if {$x0 <= $xmin+$length+$spacing} {dict lappend intervals left [list $lo $hi]}
    if {$x1 >= $xmax-$length-$spacing} {dict lappend intervals right [list $lo $hi]}
  }
  foreach net [$block getNets] {
    if {[$net getSigType] ni {POWER GROUND}} {continue}
    foreach wire [$net getSWires] {
      foreach box [$wire getWires] {
        if {![$box isVia]} {
          if {[[$box getTechLayer] getName] eq "met3"} {
            add_rect [$box xMin] [$box yMin] [$box xMax] [$box yMax]
          }
        } else {
          set via [$box getTechVia]
          if {$via eq "NULL"} {set via [$box getBlockVia]}
          if {$via eq "NULL"} {error "cannot inspect PDN via geometry"}
          lassign [$box getViaXY] vx vy
          foreach shape [$via getBoxes] {
            if {[[$shape getTechLayer] getName] eq "met3"} {
              add_rect [expr {$vx+[$shape xMin]}] [expr {$vy+[$shape yMin]}] \
                [expr {$vx+[$shape xMax]}] [expr {$vy+[$shape yMax]}]
            }
          }
        }
      }
    }
  }
  set count 0
  dict for {side ranges} $intervals {
    set merged {}
    foreach range [lsort -integer -index 0 $ranges] {
      lassign $range lo hi
      if {[llength $merged] && $lo <= [lindex $merged end 1]} {
        set prior [lindex $merged end]
        lset merged end [list [lindex $prior 0] [expr {max($hi,[lindex $prior 1])}]]
      } else {lappend merged $range}
    }
    foreach range $merged {
      lassign $range lo hi
      set region [format "%s:%.3f-%.3f" $side [expr {double($lo)/$units}] [expr {double($hi)/$units}]]
      exclude_io_pin_region -region $region
      puts "R2G_PDN_PIN_EXCLUDE $region"
      incr count
    }
  }
  if {$count==0} {error "no diagnosed edge PDN obstacles found"}
  puts "R2G_PDN_PIN_CLEARANCE applied $count intervals; original PDN and netlist retained"
}
