current_design picorv32_demo
create_clock -name core_clock -period 1000 [get_ports clk]
set_input_delay 100 -clock core_clock [all_inputs -no_clocks]
set_output_delay 100 -clock core_clock [all_outputs]
