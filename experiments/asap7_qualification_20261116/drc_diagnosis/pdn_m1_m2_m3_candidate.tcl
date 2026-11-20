# Experimental GCD-only qualification candidate; not yet flow-tested.
# Remove the M2-to-M5 stack that creates off-grid M4 landing metal and
# 24 nm V4 cuts inside 120 nm M5 stripes. Keep a physically connected grid.
add_global_connection -net VDD -inst_pattern {.*} -pin_pattern {^VDD$} -power
add_global_connection -net VSS -inst_pattern {.*} -pin_pattern {^VSS$} -ground
global_connect
set_voltage_domain -name CORE -power VDD -ground VSS
define_pdn_grid -name top -voltage_domains CORE -pins M3
add_pdn_stripe -grid top -layer M1 -width 0.018 -pitch 0.54 -offset 0 -followpins
add_pdn_stripe -grid top -layer M2 -width 0.018 -pitch 0.54 -offset 0 -followpins
# Core x origin = 1.08 um. Snap centers to M3 routing grid:
# offset 0.297 gives 1.377 = 0.009 + 38 * 0.036 um.
add_pdn_stripe -grid top -layer M3 -width 0.018 -spacing 0.522 -pitch 1.08 -offset 0.297
add_pdn_connect -grid top -layers {M1 M2}
add_pdn_connect -grid top -layers {M2 M3}
