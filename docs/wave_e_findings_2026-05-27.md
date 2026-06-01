# Wave E LVS Real-Failure Investigation — 2026-05-27

Initial diagnostic pass on the 17 designs marked `status=fail` (real "Netlists don't match")
in the 2026-05-27 signoff snapshot.

## What we have

All 17 designs:
- Have GDS, DEF, and `6_final.cdl` produced normally by ORFS.
- Hit `ERROR : Netlists don't match` from KLayout LVS after ~1-30 minutes (size-dependent).
- Have NO `6_lvs.lvsdb` file written (LVS errors out before lvsdb produced).
- Have NO `mismatch_count` populated in lvs.json (lvsdb-based extraction can't run).

The `6_lvs.log` contains only the polygon-layer-input phase, then the schematic-side
flatten messages for unused stdcells, then `ERROR : Netlists don't match`. No detail
about *which* device/net failed to match.

## Smallest case: `iscas85_c1355` (1269 cells)

- 75 top-level pins in CDL (matches DEF: `PINS 75`).
- `6_final.cdl` has ONE `.SUBCKT c1355` definition (no stdcell defs, as expected — stdcells
  are loaded by KLayout from the platform LVS library).
- `c1355_extracted.cir` has the same 75 pins by name BUT in different positional order
  (alphabetic in CDL, geometric in extracted).
- Standard cells used: BUF_X1, INV_X1, NAND2_X1, NAND3_X1, XNOR2_X1 — all in the LVS
  rule's `connect_implicit` cell list.
- `LVS finished at : 5:44:12 - LVS duration = 0 hrs. 0 min. 1 sec.` → fails in 1 second,
  confirming early structural mismatch.

## Working hypothesis

KLayout deep-LVS for nangate45 succeeds on 582 designs. The 17 failures share a property
not yet identified — possibly:

1. **Top-level pin order on the layout side** (geometric) doesn't reconcile with the CDL
   pin order during structural compare for these specific cases.
2. **Stdcell pin assignment mismatch** for one specific cell variant — needs to inspect
   `c1355_extracted.cir` cell instances vs CDL cell instances per-cell.
3. **Tie-cell or LOGIC0_X1/LOGIC1_X1 missing in layout** — if the synth net uses tied
   constants but the layout doesn't include the tie cell GDS.

Without `report_netlist_mismatch()` output (which goes into lvsdb), we cannot pinpoint
which scenario applies. Possible next steps:

- Add `report_netlist_mismatch_each_pair` to the LVS rule prologue so output is verbose.
- Or, manually run klayout LVS interactively and call `LVS.compare_each` to walk
  the diff.

## Next-action recommendation

This bucket needs an interactive KLayout LVS session per design (or a tooling patch to
emit the per-net mismatch list). It is **not blocking the 85% LVS-clean headline** —
defer until the parallel re-run waves (A, B, C, D) complete and reveal whether any of
the current `unknown` designs flip to this same `fail` pattern.

## Signal-11 designs share the same root cause

Update: KLayout 0.30.7 has a SIGSEGV bug in `NetlistCrossReference::sort_circuit()` →
`gen_log_entry()` — the crash fires *after* the comparator detects a mismatch, while
generating the mismatch log. So any design with a real structural mismatch may either:

- Exit cleanly with `ERROR : Netlists don't match` (the 17 Wave-E designs above), OR
- Crash with `Signal number: 11` (5 designs observed in re-run: pipelined_fft_64,
  core_usb_host_top, opdb_dynamic_node_xbar_network_xbar, verilog_axi_axi_fifo_wr,
  wb2axip_aximwr2wbsp).

Both are the same underlying issue: a real netlist mismatch we can't introspect without
either a klayout upgrade past 0.30.7 or a rule-prologue patch that suppresses the log
generation step.

## Diagnostic data captured

- `/tmp/wave_e_results.jsonl` — per-design summary (status, errors, components, etc.)
- `/tmp/wave_e_progress.log` — run trace
- 17 designs listed in `/tmp/wave_e.txt`:
  blake2s_core, cordic, iccad2017_unit5_F, iccad2017_unit5_G, iscas85_c1355, iscas85_c499,
  secworks_aes_src_rtl_aes_core, secworks_sha256_src_interfaces_axi4_rtl_sha256_axi4_slave,
  ultraembedded_usb_device, verilog_axi_axil_crossbar_wr,
  verilog_ethernet_axis_baser_rx_64, verilog_ethernet_axis_baser_tx_64, vlsi_axi_slave,
  vtr_..._verilog_common_1r2w, vtr_..._verilog_common_bram, wb2axip_axi2axilite,
  wb2axip_axilsingle.
