# Read-only final database guard for reviewed C03259. Do not alter connectivity.
set block [ord::get_db_block]
if {$block == "NULL" || [$block getName] ne "I2C_module"} {
  error "I2C guard requires the reviewed top"
}
set port [$block findBTerm sda]
if {$port == "NULL" || [$port getIoType] ne "INOUT"} {
  error "I2C SDA must remain an inout port"
}
set net [$port getNet]
if {$net == "NULL" || [llength [$net getBTerms]] != 1} {
  error "I2C SDA missing or aliased to another top port"
}
set drivers {}
foreach pin [$net getITerms] {
  set mt [$pin getMTerm]
  if {[$mt getIoType] eq "INPUT"} {continue}
  set inst [$pin getInst]
  if {[$mt getIoType] ne "OUTPUT" || [$mt getName] ne "Z" ||
      [[$inst getMaster] getName] ne "sky130_fd_sc_hd__ebufn_1"} {
    error "I2C SDA has an unreviewed additional/replacement driver"
  }
  foreach input {A TE_B} {
    set terminal [$inst findITerm $input]
    if {$terminal == "NULL" || [$terminal getNet] == "NULL"} {
      error "I2C tri-state input disconnected"
    }
    set inputnet [$terminal getNet]
    if {$inputnet == $net || [$inputnet getSigType] in {POWER GROUND}} {
      error "I2C tri-state input tied or replaced by SDA feedback"
    }
    foreach source [$inputnet getITerms] {
      if {[[$source getMTerm] getIoType] eq "OUTPUT" &&
          [string match "sky130_fd_sc_hd__conb_*" [[[$source getInst] getMaster] getName]]} {
        error "I2C tri-state input was constantized"
      }
    }
  }
  lappend drivers [$inst getName]
}
if {[llength [lsort -unique $drivers]] != 2} {
  error "I2C SDA must retain both original independently controlled tri-state drivers"
}
puts "R2G_I2C_FINAL_TOPOLOGY_PASS drivers=$drivers inout=sda"
