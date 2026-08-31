# Source-frozen FIFO timing contract for the strict-clean action32 cohort.
current_design fifo

set clk_name write_clock
set clk_port_name wclk
set clk_period 2.5
set clk_io_pct 0.2

create_clock -name $clk_name -period $clk_period [get_ports $clk_port_name]
create_clock -name read_clock -period $clk_period [get_ports rclk]

set non_clock_inputs [all_inputs -no_clocks]
set_input_delay [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_input_delay [expr $clk_period * $clk_io_pct] -clock read_clock $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]
set_output_delay [expr $clk_period * $clk_io_pct] -clock read_clock [all_outputs]
