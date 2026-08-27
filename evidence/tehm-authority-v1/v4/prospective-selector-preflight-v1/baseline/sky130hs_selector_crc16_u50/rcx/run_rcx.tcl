# OpenRCX parasitic extraction script
read_db "/tmp/tehm-authority-v1/selector-preflight-v1/baseline/sky130hs_selector_crc16_u50/.orfs-work/results/sky130hs/selector_crc16/sky130hs_selector_crc16_u50/6_final.odb"

# Define process corner for extraction
define_process_corner -ext_model_index 0 X

# Run parasitic extraction
extract_parasitics -ext_model_file "/opt/EDA4AI/OpenROAD-flow-scripts/flow/platforms/sky130hs/rcx_patterns.rules"

# Write SPEF output
write_spef "/tmp/tehm-authority-v1/selector-preflight-v1/baseline/sky130hs_selector_crc16_u50/rcx/6_final.spef"

puts "RCX extraction complete: /tmp/tehm-authority-v1/selector-preflight-v1/baseline/sky130hs_selector_crc16_u50/rcx/6_final.spef"
