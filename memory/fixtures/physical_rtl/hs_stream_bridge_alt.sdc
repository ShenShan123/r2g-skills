current_design hs_stream_bridge_alt
set clk_port_name clk
set clk_period 10.0
create_clock -name core_clock -period $clk_period [get_ports $clk_port_name]
set non_clock_inputs [all_inputs -no_clocks]
set_input_delay [expr $clk_period * 0.2] -clock core_clock $non_clock_inputs
set_output_delay [expr $clk_period * 0.2] -clock core_clock [all_outputs]
