# Wave Campaign Progress — 2026-05-28

Started 2026-05-27 22:38 UTC, snapshot at 2026-05-28 09:27 UTC (~11h elapsed,
campaign still in flight). Goal: continue DRC + LVS sweep on the 682-design corpus,
re-running designs in unknown/missing buckets from the 2026-05-27 signoff snapshot.

## Net new clean results this session

**17 designs flipped to clean (1 DRC + 16 LVS)**:

| Wave | Design | Type | Notes |
|------|--------|------|-------|
| A4 | Riscy_SoC_rtl_cpu_csrs | DRC | 0 violations, 1962s |
| B  | opdb_dynamic_node_xbar_network_xbar | LVS | recovered from Signal-11 on retry |
| B  | verilog_axi_axi_fifo_rd | LVS | recovered from Signal-11 on retry |
| Cm1 | iccad2015_unit21_in2 | LVS | 94K cells |
| Cm1 | iccad2015_unit20_in2 | LVS | 100K cells |
| Cm1 | iccad2015_unit19_in1 | LVS | 118K cells |
| Cm1 | vtr_...FIR_ex3PM16_fir_61 | LVS | 104K cells |
| Cm2 | iccad2015_unit22_in1 | LVS | 94K cells |
| Cm2 | iccad2015_unit01_in2 | LVS | 114K cells |
| Cm2 | iccad2015_unit19_in2 | LVS | 102K cells |
| Cm2 | poly1305_wrapper | LVS | 98K cells |
| Cm2 | cf_fir_24_16_16 | LVS | 130K cells |
| Cm3 | iccad2015_unit18_in1 | LVS | 103K cells |
| Cm3 | iccad2015_unit18_in2 | LVS | 117K cells |
| Cm3 | iccad2015_unit21_in1 | LVS | 100K cells |
| Cm3 | iccad2015_unit09_in2 | LVS | 132K cells |
| Cm3 | poly1305_core | LVS | 97K cells |

Most LVS recoveries are the ICCAD2015 contest designs, which previously didn't have
LVS rule support; the new FreePDK45.lylvs rule now processes them cleanly.

## Documented platform-level blockers (no per-design fix possible)

| Issue | Designs affected | Doc |
|-------|------------------:|-----|
| KLayout antenna rules stricter than OpenROAD's | 30 (Wave D) | docs/wave_d_findings_2026-05-27.md |
| KLayout 0.30.7 SIGSEGV in NetlistCrossReference::sort_circuit | 5 (Wave B + some C) | docs/wave_e_findings_2026-05-27.md |
| LVS "Netlists don't match" with no lvsdb output | 17 (Wave E) | docs/wave_e_findings_2026-05-27.md |
| KLayout DRC stuck on `or`/polygon-op | 231+ (existing pattern) | r2g-rtl2gds/references/failure-patterns.md |
| CDL pin-count parse error | 1 (spi_master_single_cs) | new — line 367 of 6_final_concat.cdl has 8 pins where SUBCKT expects 7 |

## In-flight at snapshot time

- **Wave A2**: 9/10 DRC re-runs done (1 stuck design remaining, ~40 min)
- **Wave A3**: 5/10 DRC re-runs done (5 remaining, ~3.5h)
- **Wave A4**: 15 records (10 designs + duplicates from agent overlaps), 1 clean confirmed,
  ~3 stuck verified, remainder in continuation chain
- **Wave Cm1**: 6/15 medium LVS (4 clean)
- **Wave Cm2**: 6/15 medium LVS (5 clean)
- **Wave Cm3**: 6/15 medium LVS (5 clean)
- **Wave Cl1**: 2/11 large LVS (0 clean so far — both hit 4h LVS_TIMEOUT)
- **Wave Cl2**: 1/10 large LVS

**Estimated time to completion**: ~15-20h wall from snapshot (the large-LVS designs at
300K-1M cells are the long pole; each takes 30 min - 4h LVS).

## Key skill-level improvements observed but not yet implemented

1. **Add global LVS lockfile** to `run_lvs.sh` to serialize KLayout LVS across worker
   agents. Multiple concurrent klayout LVS jobs raise the rate of Signal-11 crashes
   (observed in Wave B). Each klayout LVS uses 3-15GB RAM; concurrent runs also stretch
   wall time 2-3×.
2. **Extend `run_signoff.sh` to treat `drc.json status="fail"` as cacheable**
   (currently it only caches `clean`/`violations`/`stuck`). Without this, a sister
   worker can trigger a redundant 45-min DRC re-run for a design that already has a
   terminal "fail" status.
3. **Dedupe wave partitioning across waves**: Designs that need both DRC re-run AND
   LVS re-run currently appear in both `wave_a*.txt` (DRC) and `wave_c*.txt` (LVS).
   They get processed in parallel with shared ORFS workspace. The continuation script
   sees the same FLOW_VARIANT and would collide if both touched the route stage.

## Files

- /tmp/wave_*_results.jsonl — per-wave JSONL with terminal-status records.
- /tmp/wave_*_progress.log — per-wave execution traces.
- docs/wave_d_findings_2026-05-27.md — antenna DRC platform-rule investigation.
- docs/wave_e_findings_2026-05-27.md — LVS mismatch + Signal-11 investigation.
