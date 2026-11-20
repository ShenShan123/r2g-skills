# PicoRV32 ASAP7 qualification

`rtl/picorv32.v` is an unchanged copy of the existing PicoRV32 source, retaining its ISC copyright/permission notice. `rtl/picorv32_demo.v` exposes the native external memory bus and ties disabled PCPI/IRQ inputs low. All CPU parameters retain their defaults: RV32I, 32 registers, dual-port register file, 64-bit counters, no compressed ISA/multiply/divide/interrupt extension. There is no SRAM macro in the physical design; instruction/data memory exists only in the testbench.

From the repository root:

```bash
python3 r2g-skills/signoff-loop/scripts/project/validate_config.py experiments/asap7_qualification_20261116/multi_picorv32 experiments/asap7_qualification_20261116/multi_picorv32/input/validation.json
iverilog -g2012 -s picorv32_demo -t null experiments/asap7_qualification_20261116/multi_picorv32/rtl/*.v
python3 experiments/asap7_qualification_20261116/multi_picorv32/run_functional.py rtl
PATH=/usr/bin:/bin:/home/yangao/.local/bin:$PATH R2G_ALLOW_UNSUPPORTED_PLATFORM=1 ORFS_MAX_CPUS=4 ORFS_TIMEOUT=1800 bash r2g-skills/signoff-loop/scripts/flow/run_orfs.sh experiments/asap7_qualification_20261116/multi_picorv32 asap7 multi_picorv32_20261116
python3 experiments/asap7_qualification_20261116/multi_picorv32/run_functional.py gate
QUAL_PROJECT=multi_picorv32 QUAL_TOP=picorv32_demo /usr/bin/klayout -b -r experiments/asap7_qualification_20261116/prepare_design_gds.py
python3 experiments/asap7_qualification_20261116/prepare_variant_cdl.py multi_picorv32
QUAL_PROJECT=multi_picorv32 /usr/bin/klayout -b -r experiments/asap7_qualification_20261116/verify_geometry.py
python3 r2g-skills/signoff-loop/scripts/extract/extract_ppa.py experiments/asap7_qualification_20261116/multi_picorv32 experiments/asap7_qualification_20261116/multi_picorv32/reports/ppa.json
```

Preserve existing run/evidence directories before replay. The testbench executes a 40-instruction RV32I program under three memory response-delay patterns, checking nine data signatures per pattern. Its coverage includes loops, taken/not-taken branches, arithmetic, shifts, signed/unsigned byte loads, word load/store, JAL/JALR, and EBREAK. This is bounded functional simulation, not exhaustive ISA verification or formal equivalence. Final-netlist simulation uses functional models generated from the actual ASAP7 FF Liberty, with zero propagation delay; static timing is reported separately.

After transferring `multi_picorv32_input` via SFTP to the existing 216 qualification workspace, run full official DRC/LVS there with new evidence names:

```bash
module load calibre/2025.1_16.10
cd /proj/workarea/yangao/asap7_qualification_20261116
python3 run_check.py drc multi_picorv32 --gds "$PWD/multi_picorv32_input/drc.gds" --flat --top picorv32_demo
python3 run_check.py lvs multi_picorv32 --gds "$PWD/multi_picorv32_input/lvs.gds" --cdl "$PWD/multi_picorv32_input/source.cdl" --top picorv32_demo --property-limit 65536
```

No rule exclusions, logic black boxes, or physical geometry edits are permitted. DRC uses the full unmodified official encrypted deck with `DRC MAXIMUM RESULTS ALL`; LVS uses independently published, deparasitized official PEX cell references.

## Recorded qualification boundary

The implemented RTL and final netlist pass the bounded CPU program, STA meets the fixed 1000 ps target, and full transistor LVS is CORRECT with property-resolution budget 65536. Full DRC has **not completed**: the conventional flat, hierarchy-flattened turbo, and FIN-union normalized turbo attempts each reached the 600 s runtime limit at the same encrypted operation (86/1010). This is not a completed DRC failure result and is not DRC clean. All attempts and exact input hashes are preserved in `RESULT.json`; no PicoRV32 job remains active.

The final LVS evidence is `../calibre_evidence/multi_picorv32_property65536/lvs/`. DRC attempts are `../calibre_evidence/multi_picorv32/drc/`, `../calibre_evidence/multi_picorv32_flattened_hier/drc/`, and `../calibre_evidence/multi_picorv32_fin_merged_hier/drc/`. The last attempt checks `../multi_picorv32_fin_merged_input/drc.gds`; it must not be confused with the original `../multi_picorv32_input/drc.gds`. `geometry_verification.json` beside each normalized input proves all 36 drawing layers XOR equal to the original. The FIN-union GCD control completed all 334 rulechecks with zero results.

Resource accounting separates 19 ORFS tool elapsed records (503.51 s total, peak RSS 2354.56 MiB) from outer wrapper wall time (520.91 s), and separately records the three DRC attempts, LVS, and input normalization. The main `ppa.json` DRC field is an unrelated generic-parser artifact; use the official Calibre receipts.
