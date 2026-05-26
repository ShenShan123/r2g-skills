export DESIGN_NAME = arbiter
export PLATFORM    = nangate45

export VERILOG_FILES = $(PROJECT_DIR)/rtl/design.v
export SDC_FILE      = $(PROJECT_DIR)/constraints/constraint.sdc

export CORE_UTILIZATION = 30
export PLACE_DENSITY_LB_ADDON = 0.20
