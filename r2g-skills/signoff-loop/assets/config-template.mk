export DESIGN_NAME = {{DESIGN_NAME}}
export PLATFORM    = {{PLATFORM}}

export VERILOG_FILES = {{VERILOG_FILES}}
export SDC_FILE      = {{SDC_FILE}}

# --- Floorplan ---
# Use CORE_UTILIZATION for auto-sizing, OR DIE_AREA/CORE_AREA for manual sizing.
# For designs < 10 cells, use explicit DIE_AREA to avoid PDN grid errors.
export CORE_UTILIZATION = {{CORE_UTILIZATION}}
# export DIE_AREA  = 0 0 50 50
# export CORE_AREA = 2 2 48 48

# --- Placement ---
# Minimum safe value: 0.10. Use 0.20-0.45 for macro-heavy designs.
export PLACE_DENSITY_LB_ADDON = {{PLACE_DENSITY_LB_ADDON}}

# --- Synthesis ---
export ABC_AREA = 1
# export SYNTH_HIERARCHICAL = 1

# --- Safety flags (enable for large designs >50K instances) ---
# export SKIP_CTS_REPAIR_TIMING = 1
# export SKIP_LAST_GASP = 1
# export SKIP_GATE_CLONING = 1

# --- Routing (uncomment if global routing fails with congestion) ---
# export ROUTING_LAYER_ADJUSTMENT = 0.10

# --- Timing closure (uncomment to tune repair aggressiveness) ---
# export SETUP_SLACK_MARGIN = 0.0
# export HOLD_SLACK_MARGIN = 0.0
# export TNS_END_PERCENT = 100
