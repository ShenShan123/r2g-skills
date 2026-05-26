current_design {{DESIGN_NAME}}

# Clock definition — clk_port_name MUST match the RTL port name exactly.
# Run validate_config.py to verify the port name before synthesis.
set clk_name  core_clock
set clk_port_name {{CLOCK_PORT}}
set clk_period {{CLOCK_PERIOD}}
set clk_io_pct 0.2

set clk_port [get_ports $clk_port_name]

create_clock -name $clk_name -period $clk_period $clk_port

# Clock uncertainty (accounts for jitter + skew margin)
set_clock_uncertainty 0.1 [get_clocks $clk_name]

# I/O delays (20% of clock period by default)
set non_clock_inputs [all_inputs -no_clocks]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]
