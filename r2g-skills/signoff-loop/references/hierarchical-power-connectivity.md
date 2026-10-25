# Hierarchical powered-Verilog connectivity (Sky130)

On `sky130hs:riscv32i`, Netgen reported a top-pin/topology mismatch despite equal
device counts: `5878 vs 5878` devices but `6128 vs 6132` nets. The tempting theory
that the top-level `VDD,VSS` declaration order was wrong was disproved by a
same-input replay. `6_final.v` retains two RTL-generated ALU child modules; the
power-pin fallback adds standard-cell `.VGND(VSS)`, `.VNB(VSS)`, `.VPB(VDD)`, and
`.VPWR(VDD)` connections inside those children, but the children have no VDD/VSS
ports. In Verilog that creates implicit *local* nets, which Netgen exposed as a
hierarchical `...unused_C` net carrying many power pins.

`scripts/flow/power_verilog_from_spice.py` now computes the powered-module
closure, adds non-ANSI `inout VDD/VSS` ports to each powered child/ancestor, and
connects every named child instance with `.VDD(VDD), .VSS(VSS)`. The standalone
`normalize_power_connectivity.py` applies the same derived-schematic pass to an
OpenROAD-produced powered netlist and writes a digest-bound receipt. Positional
child instances are reported and fail strict normalization rather than being
silently rewritten. A replay with the exact extracted SPICE changed the report to
`5878 vs 5878` devices, `6128 vs 6128` nets, and `Circuits match uniquely`.

This is a schematic-connectivity repair, not a promotion result. The packaged
OpenROAD 26Q3/RCX replay now makes both `riscv32i_before_u50` and
`riscv32i_after_u40` DRC/LVS-clean with complete RCX receipts, but strict signoff
still fails on the aggregate timing gate (setup WNS `-0.498750 ns` and
`-0.249082 ns`). The default host OpenROAD/RCX pair still cannot read the
packaged ODB (`database schema revision 0.139 > 0.98`), and the logical
`6_final.v` fallback lacks five route-inserted antenna diodes present in the
layout. Do not flatten the layout or rename nets to make a mismatch disappear;
preserve the derived receipt and strict reports as evidence, and keep this
replay diagnostic-only until timing and the remaining support gates pass.
