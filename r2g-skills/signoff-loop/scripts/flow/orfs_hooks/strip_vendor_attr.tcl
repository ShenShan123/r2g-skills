# Fires from SYNTH_CANONICALIZE_TCL (synth_canonicalize.tcl, after hierarchy -check).
# Validated dual-clean on the nangate45 repair arm, 2026-10-21 (aldff: C01169,
# C03509; strip_vendor_attr: C02098). Applied by the agent only on its log
# signature (agent/r2g_agent/frontend.py); failure-patterns.md "Frontend recipes".
#
# A vendor FPGA attribute (Intel `altera_attribute`, carrying an embedded SDC
# statement with escaped quotes and braces) is copied verbatim into the netlist,
# and OpenSTA's Verilog reader rejects it (STA-0171, "syntax error" on that
# line). It has no meaning for an ASIC flow, so drop it before synthesis. Only
# this attribute is touched; logic and every other attribute are unchanged.
puts "R2G_STRIP_ATTR: start"
yosys setattr -mod -unset altera_attribute
yosys setattr -unset altera_attribute
puts "R2G_STRIP_ATTR: done"
