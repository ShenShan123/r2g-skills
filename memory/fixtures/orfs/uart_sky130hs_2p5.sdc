# Source-frozen UART timing contract for the strict-clean training cohort.
# The 2.5 ns period is deliberately explicit: the 1.4 ns gcd template
# produces a reproducible minor setup violation for this source-bound design.
current_design uart

set clk_name core_clock
set clk_port_name clk
set clk_period 2.5
set clk_io_pct 0.2

set clk_port [get_ports $clk_port_name]

create_clock -name $clk_name -period $clk_period $clk_port

set non_clock_inputs [all_inputs -no_clocks]

set_input_delay [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]
