# PRE_FINAL_REPORT_TCL: retain extraction and the original read_spef/checkers.
# Only normalize the escaped spelling after the real extractor writes SPEF.
if {[llength [info commands ::r2g_original_write_spef]]} {
  error "SPEF encoding hook was loaded twice"
}
rename ::write_spef ::r2g_original_write_spef
proc ::write_spef {args} {
  if {[llength $args] != 1} {
    error "SPEF encoding hook expects the standard single-output-file call"
  }
  set result [uplevel 1 [list ::r2g_original_write_spef {*}$args]]
  puts [exec $::env(PYTHON_EXE) /work/normalize_spef_names.py [lindex $args 0]]
  return $result
}
