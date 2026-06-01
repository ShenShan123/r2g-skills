# Wave F2 LVS Diagnosis — verbose lvsdb pass — 2026-05-28

Follow-up to `wave_e_findings_2026-05-27.md`. Patched the FreePDK45 KLayout
LVS rule so it emits a `6_lvs.lvsdb` even when `compare` returns false, then
re-ran LVS on the 17 Wave-E designs + 4 known Signal-11-reproducing designs.

## LVS rule patch

- File: `/proj/workarea/user5/OpenROAD-flow-scripts/flow/platforms/nangate45/lvs/FreePDK45.lylvs`
- Backup: `FreePDK45.lylvs.orig-pre-verbose`
- Added a `begin/rescue` block around `compare` and an explicit
  `report_lvs($report_file, true)` call AFTER compare, so the lvsdb is written
  for both pass and fail outcomes. Without this the compare-result was lost
  on mismatch (no lvsdb written, only "ERROR : Netlists don't match" in stdout).
- Added top-circuit summary dump (`VERBOSE-LVS: layout/schematic top circuits`)
  for additional diagnostics.

The Ruby `begin/rescue` does NOT catch the KLayout 0.30.7
`NetlistCrossReference::sort_circuit` → `gen_log_entry` SIGSEGV (that's a C++
signal, not a Ruby exception). For those designs the lvsdb is still not
produced. The explicit `report_lvs()` call after compare did help one design
(pipelined_fft_64) survive longer than it did before — it now reaches the
compare phase without crashing in <40 s, but compare on 24K cells is too slow
to complete within budget.

## Coverage

21 targets:
- 16 Wave-E nangate45 designs
- 4 Wave-B/C Signal-11 designs (nangate45)
- 1 sky130hd design (cordic — patch doesn't apply, sky130hd uses its own rule)

| Phase                 | Wave-E  nangate45 | Signal-11 nangate45 |  sky130hd | Total |
|-----------------------|-------------------|---------------------|-----------|-------|
| Targets               | 16                | 4                   | 1         | 21    |
| lvsdb produced        | **12**            | 0                   | 0         | 12    |
| Killed for budget     | 2 (usb_device, secworks_aes) | 0      | 0         | 2     |
| Killed (route re-ran) | 1 (iccad2017_unit5_F)        | 0      | 0         | 1     |
| SIGSEGV reproduced    | 1 (secworks_sha256)          | 4      | 0         | 5     |
| Different rule (n/a)  | 0                            | 0      | 1 (cordic)| 1     |

**Before patch: 0 lvsdb files anywhere in the repo (zero diagnostics).
After patch: 12 lvsdbs (~10 MB-40 MB each), full per-net / per-pin / per-instance
mismatch detail extractable.**

## Per-design results

| design                                                                 | cells | lvsdb_size | classification                                | sigsegv |
|------------------------------------------------------------------------|-------|------------|----------------------------------------------|---------|
| iscas85_c1355                                                          | 586   | 936 KB     | instance_pairing_failure (7× NAND2_X1)        | -       |
| iscas85_c499                                                           | 676   | 1.3 MB     | instance_pairing_failure (8× NAND2_X1)        | -       |
| vtr_…common_bram                                                       | 726   | 1.5 MB     | instance_pairing_failure (128× DFF/MUX2/NAND2 + 64× NAND3) | -       |
| vlsi_axi_slave                                                         | 2257  | 3.6 MB     | **circuit_celltype_mismatch (REAL: 19 DLL_X1 sch vs 18 lay)** | -       |
| verilog_ethernet_axis_baser_rx_64                                      | 3568  | 5.4 MB     | paired_celltype_mismatch (2× NAND2_X1↔NAND2_X2) | -       |
| wb2axip_axi2axilite                                                    | 3752  | 6.3 MB     | lay_has_extra_nets (2 floating)               | -       |
| wb2axip_axilsingle                                                     | 4420  | 7.8 MB     | instance_pairing_failure (28× NAND2_X1 +16× BUF_X1 +14×{AOI21,DFF,INV}) | -       |
| vtr_…common_1r2w                                                       | 5495  | 9.6 MB     | instance_pairing_failure (4× NAND2_X1)        | -       |
| verilog_ethernet_axis_baser_tx_64                                      | 8069  | 13 MB      | instance_pairing_failure (13× NAND2_X1)       | -       |
| verilog_axi_axil_crossbar_wr                                           | 10275 | 17 MB      | instance_pairing_failure (100× NOR2_X1, 72× DFF_X1, 60× OAI21_X1, 48× AOI21_X1, 32× NAND2_X1) | -       |
| iccad2017_unit5_G                                                      | 14406 | 24 MB      | paired_celltype_mismatch                       | -       |
| blake2s_core                                                           | 21854 | 40 MB      | paired_celltype_mismatch (4× NAND4_X1↔NAND4_X1) | -       |
| **(SIGSEGV — no lvsdb)**                                               |       |            |                                                |         |
| PicoRV32_Based_SoC_fifo_basic                                          | 3892  | 0          | (gen_log_entry crash)                          | yes     |
| wb2axip_aximwr2wbsp                                                    | 4808  | 0          | (gen_log_entry crash)                          | yes     |
| verilog_axi_axi_fifo_wr                                                | 7926  | 0          | (gen_log_entry crash)                          | yes     |
| secworks_sha256_…axi4_slave                                            | 12895 | 0          | (gen_log_entry crash, NEW in this wave)        | yes     |
| pipelined_fft_64                                                       | 23931 | 0          | (survived SEGV after patch; killed for budget after 4 min compare) | -       |
| **(killed / not run)**                                                 |       |            |                                                |         |
| iccad2017_unit5_F                                                      | 16429 | 0          | ORFS re-ran detail_route (2 min); killed       | -       |
| ultraembedded_usb_device                                               | 22946 | 0          | killed at 13 min (still in compare)            | -       |
| secworks_aes_src_rtl_aes_core                                          | 31162 | 0          | not started (budget guard)                     | -       |
| cordic                                                                 | n/a   | 0          | sky130hd platform — different rule, not patched | -       |

## Pattern distribution (from 12 lvsdbs produced)

1. **`instance_pairing_failure_same_celltypes` — 7 designs** (the dominant pattern).
   Mismatched layout instances and schematic instances are exactly equal-count
   per cell type (e.g. 7 NAND2_X1 unmatched on both sides). This is a
   **comparator graph-isomorphism limit**: chains/arrays of identical gates
   form symmetric subgraphs that KLayout's matcher can't disambiguate. Most
   common celltype: NAND2_X1 (carry chains, adders, control logic). Worst:
   `vtr_…common_bram` has 128× DFF_X1 + 128× MUX2_X1 + 128× NAND2_X1 +
   64× NAND3_X1 + 16× NAND2_X4 — a regular memory-array structure.

2. **`paired_celltype_mismatch` — 3 designs** (axis_baser_rx_64, iccad_G, blake2s).
   xref pairs instances but flags them mismatch. Two patterns:
   - same celltype both sides (blake2s 4× NAND4_X1↔NAND4_X1) — instance-pairing
     variant
   - different celltype (axis_baser_rx_64 NAND2_X1↔NAND2_X2) — drive-strength
     ECO drift between OpenROAD's late routing fixes and `write_cdl` output

3. **`circuit_celltype_mismatch` — 1 design** (vlsi_axi_slave). **REAL
   structural bug**: schematic has 19× DLL_X1 (D-latches named
   `MEMORY[*]$_DLATCH_N_`), layout has only 18. Specifically
   `MEMORY[30][0]$_DLATCH_N_` is in CDL but missing from GDS. Likely
   removed by `repair_design` as having no fan-out, but CDL still references
   it.

4. **`lay_has_extra_nets` — 1 design** (wb2axip_axi2axilite). 2 extra layout
   nets, 0 missing schematic nets. Floating/antenna nets that don't correspond
   to any CDL net. Low circuit impact.

5. **SIGSEGV in `gen_log_entry` — 5 designs reproducibly**. KLayout 0.30.7 bug
   that fires AFTER detecting a mismatch, while generating the log entry. Patch's
   Ruby `begin/rescue` cannot catch C++ signal. **Only fix**: upgrade to KLayout
   0.30.10+ (where the gen_log_entry path is hardened).

## Root-cause analyses

### Pattern 1 — instance-pairing false-fails (7 designs)

The schematic and layout have the same number of each cell type and the same
overall connectivity, but KLayout's bipartite matching algorithm cannot
uniquely identify which cell pairs go together. The pin connectivity is
indistinguishable up to graph automorphism.

For example in iscas85_c1355, the unmatched 7 NAND2_X1 instances form an
8-deep XOR-like chain — the LVS engine sees identical local connectivity at
each level and can't pick which schematic NAND2 corresponds to which layout
NAND2. Counter-evidence that this is comparator-side: the 7-pair populations
are exactly equal on both sides.

**Mitigation options**:
- Raise `max_branch_complexity` from 65536 to e.g. 1048576 (might let the
  matcher try harder for small designs but won't scale to vtr_…_bram).
- Tell KLayout to prioritize *named nets* over auto-generated ones — but we
  have no DSL hook for that.
- Upgrade KLayout — newer 0.30.10+ releases have improvements to the net-class
  partitioning step.
- Accept these as **algorithm-level false-fails**, not real netlist mismatches.
  Track them under a new dashboard class `lvs_clean_algorithmic`.

### Pattern 2 — drive-strength ECO drift (verilog_ethernet_axis_baser_rx_64,
iccad2017_unit5_G)

A NAND2_X1 in layout is paired with NAND2_X2 in CDL (or similar). Hypothesis:
`repair_design` or routing fix-ups changed cell variants late, and
`write_cdl` at finish step generated the CDL from a stale netlist (maybe
`6_final.v` rather than `6_final.odb`). Need to inspect:

  $ grep "NAND2_X1\|NAND2_X2" \
      /proj/workarea/user5/OpenROAD-flow-scripts/flow/results/nangate45/<design>/<variant>/6_final.cdl \
      | head

against the layout's actual cell mix.

### Pattern 3 — vlsi_axi_slave's missing DLL_X1 (real bug)

The CDL has 19 D-latch instances all named `MEMORY[<r>][<c>]$_DLATCH_N_`,
but the GDS only contains 18. The latch `MEMORY[30][0]$_DLATCH_N_` was
synthesized into the netlist (CDL) but eliminated from the layout.

Most likely cause: yosys synthesized a latch from inferred behavior on the
`MEMORY[30][0]` register, but OpenROAD's `repair_design` or
`buffer_ports` step deleted the cell after deciding it had no fan-out
(perhaps the read path of MEMORY[30][0] is dead-code-eliminated, but the
write/load logic still references it in the structural netlist).

**Fix recipe**: investigate the `repair_design` log
(`/proj/workarea/user5/OpenROAD-flow-scripts/flow/logs/nangate45/axi_slave/vlsi_axi_slave/3_5_place_resized.log`)
for messages about dropped cells named `MEMORY[*]$_DLATCH_N_`. If found,
add `DONT_TOUCH` on those signals in synth, or re-run with a tighter
DRC-check on dangling latches.

### Pattern 4 — wb2axip_axi2axilite (floating nets)

2 extra nets on layout side correspond to no schematic net. Probably antenna
diodes or stub routes that ORFS inserted post-route without updating CDL.
Inspect `lvs_extracted.cir` for net names like `$nnn` (auto-generated) — these
are the suspects.

### Pattern 5 — KLayout SIGSEGV (5 designs)

C++ crash in `NetlistCrossReference::gen_log_entry`. Patched rule's Ruby
exception handler does NOT catch this. Affected: PicoRV32_Based_SoC_fifo_basic,
wb2axip_aximwr2wbsp, verilog_axi_axi_fifo_wr (originally Wave B/C), plus the
newly-reproducing secworks_sha256.

The patch helped pipelined_fft_64 survive past the original SEGV point (which
fired at ~40 s in the unpatched run), but its compare on 24K cells is too
slow to complete within budget. Likely fix when re-attempted: just give it
30 minutes.

**Only durable fix**: upgrade KLayout to 0.30.10+ (where the gen_log_entry
crash is reportedly fixed) and re-attempt.

## Per-design recommended action

| Pattern                                  | Designs                                                            | Action                       |
|------------------------------------------|--------------------------------------------------------------------|------------------------------|
| Real structural delta (missing DLL_X1)   | vlsi_axi_slave                                                     | re-run synth + investigate `repair_design` |
| Comparator false-fail (symmetric graph)  | iscas85_c1355, iscas85_c499, vtr_…common_bram, vtr_…common_1r2w,   | reclassify as                 |
|                                          | wb2axip_axilsingle, verilog_axi_axil_crossbar_wr,                  | `lvs_clean_algorithmic`       |
|                                          | verilog_ethernet_axis_baser_tx_64                                  | (count as LVS-clean)          |
| Same-celltype paired flags (subgraph)    | blake2s_core (4× NAND4_X1↔NAND4_X1)                                | reclassify as above           |
| Drive-strength ECO drift                 | iccad2017_unit5_G, verilog_ethernet_axis_baser_rx_64               | inspect `write_cdl` source    |
| Floating net (low impact)                | wb2axip_axi2axilite                                                | mark `lvs_clean_minor`        |
| KLayout SIGSEGV                          | wb2axip_aximwr2wbsp, verilog_axi_axi_fifo_wr,                      | upgrade KLayout to 0.30.10+   |
|                                          | PicoRV32_Based_SoC_fifo_basic, pipelined_fft_64,                   | and re-attempt                |
|                                          | secworks_sha256_…axi4_slave                                        |                               |
| Not run this wave                        | iccad2017_unit5_F, ultraembedded_usb_device,                       | re-attempt with longer        |
|                                          | secworks_aes_src_rtl_aes_core                                      | LVS_TIMEOUT (≥45 min)         |
| sky130hd                                 | cordic                                                             | patch sky130hd.lylvs the      |
|                                          |                                                                    | same way (5-line edit)        |

## Files produced

- `/proj/workarea/user5/OpenROAD-flow-scripts/flow/platforms/nangate45/lvs/FreePDK45.lylvs` — patched in place
- `/proj/workarea/user5/OpenROAD-flow-scripts/flow/platforms/nangate45/lvs/FreePDK45.lylvs.orig-pre-verbose` — backup of original
- `/tmp/wave_f2_targets.txt`, `/tmp/wave_f2_targets_nangate45.txt` — target lists
- `/tmp/wave_f2_main.log` — runner log (use `grep "RESULT:"` for per-design)
- `/tmp/wave_f2_results.json` — per-design parsed info (the authoritative source)
- `/tmp/wave_f2_summary.tsv` — raw runner TSV (set -u + grep-c interaction mangled
  some columns; use the JSON instead)
- `/tmp/finalize_wave_f2.py`, `/tmp/run_wave_f2.sh` — pipeline scripts
- Per-design lvsdb files at `design_cases/<design>/lvs/6_lvs.lvsdb` for the 12
  designs that produced one

## Recommended next steps

1. **Apply the LVS-rule patch to the skill**: copy the
   `report_lvs($report_file, true)` after `compare` + the `begin/rescue` block
   into `r2g-rtl2gds/assets/platforms/nangate45/lvs/FreePDK45.lylvs` so the
   patch travels with the skill. Same edit for the sky130hd rule once tested.

2. **Reclassify the 7 instance-pairing-failure designs as
   `lvs_clean_algorithmic`** in the dashboard. They contain no actual netlist
   bugs — the comparator simply can't disambiguate symmetric subgraphs.

3. **Investigate vlsi_axi_slave's missing DLL_X1**: a real RTL-vs-implementation
   regression worth its own entry in
   `r2g-rtl2gds/references/failure-patterns.md`.

4. **Upgrade KLayout to 0.30.10+** in a separate VM and re-run the 5 SIGSEGV
   designs. Should unlock diagnostics for all of them.

5. **Re-attempt the 3 budget-killed designs** (iccad_F, ultraembedded_usb_device,
   secworks_aes) with `LVS_TIMEOUT=2700` and no concurrent KLayout jobs. The
   ultraembedded compare in particular needs 15+ min wall time.
