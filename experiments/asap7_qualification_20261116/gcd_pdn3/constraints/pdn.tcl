add_global_connection -net VDD -inst_pattern {.*} -pin_pattern {^VDD$} -power
add_global_connection -net VSS -inst_pattern {.*} -pin_pattern {^VSS$} -ground
global_connect
set_voltage_domain -name CORE -power VDD -ground VSS
define_pdn_grid -name top -voltage_domains CORE -pins M3
add_pdn_stripe -grid top -layer M1 -width 0.018 -pitch 0.54 -offset 0 -followpins
add_pdn_stripe -grid top -layer M2 -width 0.018 -pitch 0.54 -offset 0 -followpins
add_pdn_stripe -grid top -layer M3 -width 0.018 -spacing 2.682 -pitch 5.4 -offset 0.279
add_pdn_connect -grid top -layers {M1 M2}
add_pdn_connect -grid top -layers {M2 M3}
