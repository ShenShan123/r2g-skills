# Exact C03259 PRE_PDN hook. Restore the standard SKY130 PG connections that
# global_connect skips on protected instances; keep all signal connections.
source [file join [file dirname [info script]] check_i2c_tristate_topology.tcl]
set b [ord::get_db_block]
set signals {}
foreach name {m1/_351_ s1/_318_} {
  set i [$b findInst $name]
  if {$i eq "NULL" || ![$i isDoNotTouch]} {error "Expected protected original I2C driver"}
  foreach pin {A TE_B Z} {dict set signals $name $pin [[$i findITerm $pin] getNet]}
  foreach {pin rail} {VPWR VDD VPB VDD VGND VSS VNB VSS} {
    set t [$i findITerm $pin]
    if {$t eq "NULL"} {error "Missing SKY130 power/body pin"}
    set old [$t getNet]
    if {$old ne "NULL" && [$old getName] ne $rail} {error "Unexpected existing I2C supply connection"}
  }
}
# Match the platform's standard global connection rules, restricted to the two
# original instances. No logic optimization runs while protection is lifted.
foreach name {m1/_351_ s1/_318_} {unset_dont_touch [get_cells $name]}
add_global_connection -net VDD -inst_pattern {^(m1/_351_|s1/_318_)$} -pin_pattern {^(VPWR|VPB)$} -power
add_global_connection -net VSS -inst_pattern {^(m1/_351_|s1/_318_)$} -pin_pattern {^(VGND|VNB)$} -ground
global_connect
foreach name {m1/_351_ s1/_318_} {
  set_dont_touch [get_cells $name]
  set i [$b findInst $name]
  foreach pin {A TE_B Z} {
    if {[[$i findITerm $pin] getNet] ne [dict get $signals $name $pin]} {error "I2C power hookup changed signal connectivity"}
  }
  foreach {pin rail} {VPWR VDD VPB VDD VGND VSS VNB VSS} {
    set n [[$i findITerm $pin] getNet]
    if {$n eq "NULL" || [$n getName] ne $rail} {error "I2C original power/body connection missing"}
  }
}
puts "R2G_I2C_DRIVER_POWER_PASS connections=8 protection=retained"
