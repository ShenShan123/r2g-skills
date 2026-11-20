export DESIGN_NAME = picorv32_demo
export PLATFORM = asap7
export VERILOG_FILES = /home/yangao/r2g-skills/experiments/asap7_qualification_20261116/multi_picorv32/rtl/picorv32.v /home/yangao/r2g-skills/experiments/asap7_qualification_20261116/multi_picorv32/rtl/picorv32_demo.v
export SDC_FILE = /home/yangao/r2g-skills/experiments/asap7_qualification_20261116/multi_picorv32/constraints/constraint.sdc
export CORE_UTILIZATION = 30
export CORE_ASPECT_RATIO = 1
export CORE_MARGIN = 1.08
export PLACE_DENSITY = 0.35
export PLACE_DENSITY_LB_ADDON = 0.20
export NUM_CORES = 4
export SKIP_LAST_GASP = 1
export PYTHON_EXE = /usr/bin/python3

export PDN_TCL = /home/yangao/r2g-skills/experiments/asap7_qualification_20261116/multi_picorv32/constraints/pdn.tcl

export VIA_IN_PIN_MIN_LAYER = M1
export VIA_IN_PIN_MAX_LAYER = M1
override export TECH_LEF = /home/yangao/r2g-skills/experiments/asap7_qualification_20261116/drc_diagnosis/asap7_tech_asym_v1_m4_only_candidate.lef

export SC_LEF = /home/yangao/r2g-skills/experiments/asap7_qualification_20261116/drc_diagnosis/asap7sc7p5t_R_edge_pin_window_candidate.lef
