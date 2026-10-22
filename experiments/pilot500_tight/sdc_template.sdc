# r2g pilot500 tight-clock re-run (2026-10-22). Same clock-port search as the
# pilot; period PER_NS set per design; uncertainty and I/O delays as in the
# frozen baseline spec (setup 5 % of period, hold 20 ps, I/O 15 % of period),
# so port-to-port and port-to-register paths are constrained too.
set cand [get_ports -quiet {clk clock i_clk clk_i core_clk CLK CLOCK HCLK clock_i sys_clk clk_in clkin clk_sys aclk ACLK wb_clk_i clock_in pclk PCLK clock0 clk0 clkA clk_a ref_clk refclk mclk MCLK fclk sclk}]
if {[llength $cand] == 0} {
  foreach p [all_inputs] {
    if {[regexp -nocase {(^|_)(clk|clock)([0-9_]*$|_)} [get_name $p]]} {
      lappend cand $p
    }
  }
}
set _per PER_NS
if {[llength $cand] > 0} {
  create_clock -name core_clk -period $_per [lindex $cand 0]
} else {
  create_clock -name virtual_clk -period $_per
}
set_clock_uncertainty -setup [expr {$_per * 0.05}] [get_clocks *]
set_clock_uncertainty -hold 0.020 [get_clocks *]
set _io [expr {$_per * 0.15}]
set _clks [get_clocks *]
set _in [all_inputs]
foreach _c $cand {
  set _in [lsearch -all -inline -not -exact $_in $_c]
}
if {[llength $_in] > 0} {
  set_input_delay  $_io -clock [lindex $_clks 0] $_in
}
if {[llength [all_outputs]] > 0} {
  set_output_delay $_io -clock [lindex $_clks 0] [all_outputs]
}
