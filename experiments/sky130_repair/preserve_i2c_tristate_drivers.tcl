# Exact C03259 pre-synthesis-ODB hook. Keep both real shared-bus drivers
# alive through eliminate_dead_logic; retain its ordinary processing elsewhere.
set block [ord::get_db_block]
if {$block == "NULL" || [$block getName] ne "I2C_module"} {
  error "I2C preservation requires the reviewed top"
}
set port [$block findBTerm sda]
if {$port == "NULL" || [$port getIoType] ne "INOUT"} {
  error "I2C preservation requires original INOUT SDA"
}
set net [$port getNet]
set drivers {}
foreach pin [$net getITerms] {
  set masterpin [$pin getMTerm]
  if {[$masterpin getIoType] eq "INPUT"} {continue}
  set instance [$pin getInst]
  if {[$masterpin getIoType] ne "OUTPUT" || [$masterpin getName] ne "Z" ||
      [[$instance getMaster] getName] ne "sky130_fd_sc_hd__ebufn_1"} {
    error "Unexpected SDA driver before preservation"
  }
  lappend drivers [$instance getName]
}
if {[llength [lsort -unique $drivers]] != 2} {
  error "Both original real tri-state drivers must exist before preservation"
}
foreach name $drivers {
  set cells [get_cells -quiet $name]
  if {[llength $cells] != 1} {error "Ambiguous I2C driver instance"}
  set_dont_touch $cells
}
puts "R2G_I2C_PRESERVE_TWO_DRIVERS $drivers"
