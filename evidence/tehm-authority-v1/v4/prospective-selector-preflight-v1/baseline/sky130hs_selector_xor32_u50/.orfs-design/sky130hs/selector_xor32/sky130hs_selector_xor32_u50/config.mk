export DESIGN_NAME = selector_xor32
export PLATFORM    = sky130hs

export VERILOG_FILES = /data1/zhangdy/Typed-Executable-Hardware-Memory/memory/evaluation/rtl_selector_preflight/selector_xor32.v
export SDC_FILE      = /tmp/tehm-authority-v1/selector-preflight-v1/baseline/sky130hs_selector_xor32_u50/constraints/constraint.sdc
export ABC_AREA      = 1

# Adders degrade GCD
export ADDER_MAP_FILE :=

export CORE_UTILIZATION = 50
export PLACE_DENSITY_LB_ADDON = 0.25
export TNS_END_PERCENT        = 100
export EQUIVALENCE_CHECK     ?=   0
export REMOVE_CELLS_FOR_EQY   = sky130_fd_sc_hs__tapvpwrvgnd*
