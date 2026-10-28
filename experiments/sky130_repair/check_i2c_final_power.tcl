# Read-only final acceptance guard; require original topology and all eight PG pins.
source [file join [file dirname [info script]] check_i2c_tristate_topology.tcl]
set b [ord::get_db_block]
foreach name {m1/_351_ s1/_318_} {
  set i [$b findInst $name]
  if {$i eq "NULL"} {error "Original I2C driver missing"}
  foreach {pin rail type} {VPWR VDD POWER VPB VDD POWER VGND VSS GROUND VNB VSS GROUND} {
    set t [$i findITerm $pin]
    if {$t eq "NULL"} {error "Original I2C power/body pin missing"}
    set n [$t getNet]
    if {$n eq "NULL" || [$n getName] ne $rail || [$n getSigType] ne $type} {
      error "Original I2C power/body connection missing or incorrect: $name/$pin"
    }
  }
}
puts "R2G_I2C_FINAL_POWER_PASS connections=8"
