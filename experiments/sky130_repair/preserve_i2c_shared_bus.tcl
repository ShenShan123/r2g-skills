# C03259 shared bidirectional bus: do not insert single-driver buffers on SDA.
# Routing, parasitic extraction, timing reports and final signoff remain enabled.
source [file join [file dirname [info script]] check_i2c_tristate_topology.tcl]
set nets [get_nets -quiet sda]
if {[llength $nets] != 1} {error "I2C preservation requires exactly one original SDA net"}
set_dont_touch $nets
puts "R2G_I2C_PRESERVE_SHARED_BUS sda"
