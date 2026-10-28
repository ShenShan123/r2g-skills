# Keep the real extractor and read_spef. Change only reversible escaping of
# negative-index identifiers, retaining the original SPEF and a token audit.
set ::r2g_spef_encoder [file normalize [file join [file dirname [info script]] .. normalize_spef_names.py]]
if {[llength [info commands ::r2g_original_write_spef]]} {
  error "SPEF encoding hook was loaded twice"
}
rename ::write_spef ::r2g_original_write_spef
proc ::write_spef {args} {
  if {[llength $args] != 1} {
    error "SPEF encoding hook expects the standard single-output-file call"
  }
  set result [uplevel 1 [list ::r2g_original_write_spef {*}$args]]
  puts [exec $::env(PYTHON_EXE) $::r2g_spef_encoder [lindex $args 0]]
  return $result
}
