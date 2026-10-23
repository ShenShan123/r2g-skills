# Fires from SYNTH_CANONICALIZE_TCL (synth_canonicalize.tcl, after hierarchy -check).
# Validated dual-clean on the nangate45 repair arm, 2026-10-21 (aldff: C01169,
# C03509; strip_vendor_attr: C02098). Applied by the agent only on its log
# signature (agent/r2g_agent/frontend.py); failure-patterns.md "Frontend recipes".
#
# Async-load flip-flops ($aldff: an asynchronous signal loads a DATA value, an
# FPGA idiom) have no cell in nangate45, so dfflibmap leaves $_ALDFFE_* unmapped
# and `check -assert` aborts synthesis. Convert ONLY those cells to set/reset
# flip-flops plus gating logic -- yosys's own functionally-equivalent emulation
# -- which dfflibmap then maps onto DFFRS. Every other flip-flop is untouched.
# (Tcl: every `$` that is a yosys cell-type sigil must be escaped.)
puts "R2G_ALDFF: start"
yosys proc
yosys select -count t:\$aldff t:\$aldffe
yosys simplemap t:\$aldff t:\$aldffe
yosys dfflegalize -cell \$_DFFSR_PPP_ 01 -cell \$_DFFSRE_PPPP_ 01 t:\$_ALDFF* t:\$_ALDFFE*
puts "R2G_ALDFF: converted"
