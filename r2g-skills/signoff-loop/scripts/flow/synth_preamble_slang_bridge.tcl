# Project-local bridge for ORFS revisions whose configs advertise
# SYNTH_HDL_FRONTEND=slang but whose synth_preamble.tcl still unconditionally
# calls Yosys read_verilog.  The bridge elaborates the frozen SystemVerilog
# source set with yosys-slang, serializes that design as RTLIL, and then hands
# the RTLIL to the unmodified ORFS synthesis preamble.
#
# The caller must load the slang plugin (YOSYS_FLAGS="... -m slang") and bind
# R2G_ORFS_CANONICAL_SCRIPTS_DIR to the immutable ORFS scripts directory.

yosys -import

if {![info exists ::env(R2G_ORFS_CANONICAL_SCRIPTS_DIR)]} {
  error "R2G_ORFS_CANONICAL_SCRIPTS_DIR is required by the Slang bridge"
}
if {![info exists ::env(OBJECTS_DIR)] || ![info exists ::env(VERILOG_FILES)]} {
  error "OBJECTS_DIR and VERILOG_FILES are required by the Slang bridge"
}

set needs_slang 0
foreach source_file $::env(VERILOG_FILES) {
  if {[file extension $source_file] ni {".rtlil" ".json"}} {
    set needs_slang 1
  }
}

if {!$needs_slang} {
  # ORFS invokes the preamble twice: canonicalization first, then synthesis
  # with 1_synth.rtlil as VERILOG_FILES.  The latter must bypass Slang and use
  # the canonical RTLIL reader directly.
  source "$::env(R2G_ORFS_CANONICAL_SCRIPTS_DIR)/synth_preamble.tcl"
} else {
  set slang_args [list --single-unit -D SYNTHESIS]
  if {[info exists ::env(VERILOG_INCLUDE_DIRS)] && $::env(VERILOG_INCLUDE_DIRS) ne ""} {
    foreach dir $::env(VERILOG_INCLUDE_DIRS) {
      lappend slang_args -I $dir
    }
  }
  if {[info exists ::env(VERILOG_DEFINES)] && $::env(VERILOG_DEFINES) ne ""} {
    foreach define $::env(VERILOG_DEFINES) {
      lappend slang_args -D $define
    }
  }

  puts "R2G Slang bridge: elaborating frozen SystemVerilog source set"
  read_slang {*}$slang_args {*}$::env(VERILOG_FILES)

  file mkdir $::env(OBJECTS_DIR)
  set bridge_rtlil "$::env(OBJECTS_DIR)/r2g_slang_frontend.rtlil"
  write_rtlil $bridge_rtlil
  design -reset

  # The canonical ORFS preamble recognizes RTLIL natively and continues with
  # its normal hierarchy, stdcell, technology-map, and synthesis pipeline. This
  # Tcl environment mutation is process-local and does not alter config or RTL.
  set ::env(VERILOG_FILES) $bridge_rtlil
  source "$::env(R2G_ORFS_CANONICAL_SCRIPTS_DIR)/synth_preamble.tcl"
}
