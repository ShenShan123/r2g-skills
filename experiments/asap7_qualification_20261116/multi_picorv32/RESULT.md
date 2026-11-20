# PicoRV32 ASAP7 Extended Qualification

Implementation, functional simulation, STA, and LVS pass. Full official DRC times out in a complex geometry operation; complete DRC/LVS signoff is not established. Timeout alone does not establish a DRC violation.

Uses original ISC-licensed PicoRV32 RTL: default RV32I, 32 registers, 64-bit counters, no multiply/divide, compressed instructions, or IRQ extensions. A new wrapper exposes the native memory bus. No SRAM macros are included; simulation program/data memory resides in the testbench.

| Check / metric | Result |
|---|---|
| Configuration, Icarus syntax/elaboration | PASS |
| RTL instruction program | PASS; 3 wait-state modes, 9 signatures per mode |
| Final-netlist program | PASS; same 3 modes, zero-delay Liberty models |
| Full official DRC on mask-equivalent inputs | Incomplete; 3 input/engine variants each time out at 600 s in the same operation |
| Official transistor LVS | CORRECT, property-resolution maximum 65536 |
| Fixed clock | 1000 ps / 1 GHz; no Fmax search |
| Die / core area | 5145.62 / 4826.21 µm² |
| Standard-cell area / utilization | 1478.65 µm² / 30.64% |
| Final logic instances / types | 11528 / 62 |
| Setup / hold slack | 277.954 / 30.9057 ps |
| Setup / hold TNS | 0 / 0 ps |
| Estimated power | 0.0131487 W |
| ORFS tool-stage elapsed sum | 503.51 s, 19 records |
| Full wrapper wall time | 520.91 s, including startup/archive overhead |
| Implementation peak RSS | 2354.56 MiB |
| DRC attempts / final LVS recheck | 3 × 600 s timeout / 15.12 s |

The program covers summation/Fibonacci loops, branches, arithmetic, shifts, word/byte reads and writes, signed/unsigned extension, JAL/JALR, and EBREAK. Finite program simulation is not complete ISA certification or formal equivalence. Gate simulation has no delays; STA assesses timing separately. An independent final-netlist mutation inverting write-data bit 0 is rejected; the original is unchanged. Power uses default activity, not program-annotated activity.

Implementation reuses the accepted GCD TECH_LEF, conservative SC_LEF pin windows, M1 via-in-pin, and PDN. Shared platform files and official rules are unchanged. Non-text geometry and instance transforms match final GDS; only top-name/pin-label adaptations differ. Flattened and exact FIN-union DRC candidates have zero Region XOR on all 36 layers, but no complete DRC verdict. The same FIN-union GCD control still passes 334 checks with zero results in 9.52 s.

The independent transistor reference is derived from published official PEX, not this design's extraction. Audit preserves D/G/S/B, models, and L/W/nfin for 319443 MOS; 59 combinational types pass exhaustive truth checks, and three sequential types pass strict transistor-topology equivalence.

The three original DRC trials are traditional flat, fully flattened hier/turbo4, and flattened FIN-union hier/turbo4. User CPU times are 597.01/647.90/649.42 s; all time out after encrypted operation 86/1010 without a complete rule count or final result count. All processes have ended.

See RESULT.json for input hashes, commands, paths, and raw results; REPRODUCE.md for commands. Original flow and checker evidence are preserved. Initial LVS at budget 1024 is CORRECT but reaches the property budget; a same-input 65536 recheck remains CORRECT without that warning, retaining isomorphic-device ambiguity notices. Generic ppa.json summary.drc=2 misparses M2 in a historical iteration file and is not final DRC. The final router report is empty; official Calibre outcomes govern signoff.
