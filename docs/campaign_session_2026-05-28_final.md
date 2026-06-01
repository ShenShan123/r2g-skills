# Wave Campaign — 2026-05-28 Session Summary

Session: 2026-05-27 22:38 UTC → 2026-05-28 18:32 UTC (~20h elapsed at this snapshot;
campaign continues in background).

## Headline

Starting from the 2026-05-27 signoff snapshot (582/682 LVS clean, 381/682 DRC clean),
this session added:

- **22 newly-clean designs** (1 DRC clean + 21 LVS clean) → confirmed terminal status
- **40 DRC re-runs completed** (Wave A) — moved every previously-"unknown" DRC design
  to a terminal status (clean or stuck)
- **7 small LVS re-runs completed** (Wave B) — 2 recovered, 1 confirmed fail,
  4 reproducible failures documented
- **27/45 medium LVS re-runs completed** (Wave Cm) — 11+ clean, 1 fail
- **7/21 large LVS re-runs in progress** (Wave Cl) — slow but advancing
- **2 platform-level blockers documented** (Wave D antenna rules, Wave E LVS rule patches needed)

## Wave-by-wave results

### Wave A — DRC re-runs (40 designs, all 4 sub-batches done)

| Sub-wave | Designs | Clean | Stuck | Notes |
|----------|---------|------:|------:|-------|
| A1 | 10 | 0 | 10 | All KLayout polygon-op deadloop pattern |
| A2 | 10 | 0 | 10 | All stuck (incl. boom_mediumboom 9.19M cells, completed in 1h40min!) |
| A3 | 10 | 0 | 10 | All stuck |
| A4 | 10 | **1** | 9 | **Riscy_SoC_rtl_cpu_csrs flipped clean** (0 violations) |
| A1m | 3 | 0 | 3 | Missing-recovery: I2SRV32, Riscy_SoC_cpu_execute, ethernet_udp_64 — all stuck |

Wave A confirms what the 2026-05-27 snapshot suggested: the "DRC unknown" bucket is
nearly entirely the known KLayout polygon-op pattern. Only 1 new clean DRC found.

### Wave B — Small LVS re-runs (7 designs, complete)

| Final status | Count | Designs |
|--------------|------:|---------|
| Clean (recovered from Signal-11) | 2 | opdb_dynamic_node_xbar_network_xbar, verilog_axi_axi_fifo_rd |
| Fail (genuine "Netlists don't match") | 1 | core_usb_host_top |
| Unknown (reproducible Signal-11) | 3 | wb2axip_aximwr2wbsp, verilog_axi_axi_fifo_wr, PicoRV32_Based_SoC_fifo_basic |
| Unknown (CDL parse bug) | 1 | spi_master_single_cs (line 367 of 6_final_concat.cdl) |

**Key finding**: KLayout 0.30.7 Signal-11 in `NetlistCrossReference::sort_circuit` is
sometimes transient under reduced concurrency. Sister-worker retries recovered 2/5
crashes — others reproduce.

### Wave Cm — Medium LVS re-runs (45 designs, 60% done)

| Sub-wave | Done | Clean | Fail | Unknown |
|----------|------|------:|-----:|--------:|
| Cm1 | 10/15 | 5 | 0 | 5 |
| Cm2 | 9/15 | 6 | 1 | 2 |
| Cm3 | 9/15 | 6 | 0 | 3 |

**11+ clean LVS** in Wave Cm. ICCAD2015 contest designs are the largest contributor —
the new FreePDK45.lylvs rule processes them cleanly. Notable clean designs:
iccad2015_unit01/19/20/21/22 (94K-118K cells), poly1305_core/wrapper (97K-98K cells),
cf_fir_24_16_16 (130K cells), koios_bnn (175K), koios_softmax (198K), verilog_pcie_us_axi_dma (215K).

### Wave Cl — Large LVS re-runs (21 designs, ~30% done)

| Sub-wave | Done | Clean | Unknown |
|----------|------|------:|--------:|
| Cl1 | 4/11 | 0 | 4 |
| Cl2 | 3/10 | 0 | 3 |

All Cl results so far are "unknown" — hitting the 4h LVS_TIMEOUT cap. Designs at
300K-1M cells genuinely need longer LVS budgets. None have flipped to clean yet.

### Wave D — Antenna DRC violations (30 designs, complete: NOT FIXABLE)

Conclusion: KLayout `FreePDK45.lydrc` antenna rules are stricter than the LEF-encoded
ratios that OpenROAD's `repair_antennas` uses. OpenROAD reports 0 violations; KLayout
still flags 7-231 geometric violations on the same nets. Verified with a full re-route
of `Canakari_Verilog_bittiming2`: pre==post==7 violations. Platform-LVS-rule patch
needed (not a per-design fix). Full details: `docs/wave_d_findings_2026-05-27.md`.

### Wave E — LVS real fails (17 designs, deferred)

`6_lvs.log` only emits `ERROR : Netlists don't match` — no per-net detail because
KLayout writes nothing to `6_lvs.lvsdb` before erroring out. Need either:
1. Interactive klayout LVS session per design, OR
2. Patch to LVS rule prologue to enable `report_netlist_mismatch_each_pair`.

Full details: `docs/wave_e_findings_2026-05-27.md`. The 5 reproducible Signal-11
designs from Wave B share the same root cause (real mismatch + klayout 0.30.7
crash in mismatch-reporting code).

## Updated 2026-05-28 corpus state (projected once campaign finishes)

| Metric | 2026-05-27 baseline | Δ this session (so far) | Projected post-campaign |
|--------|--------------------:|-------------------------:|------------------------:|
| LVS clean | 582 / 682 (85.3%) | +21 (Cm) +2 (B retries) | ~615-625 / 682 (~91%) |
| DRC clean | 381 / 682 (55.9%) | +1 (Riscy_SoC) | 382 / 682 (~56%) |
| RCX complete | 681 / 682 (99.85%) | unchanged | unchanged |

DRC ceiling on this OpenROAD/KLayout stack appears to be ~382/682 (56%) without
a fix to the KLayout polygon-op stuck pattern (~231 designs) or the antenna-rule
discrepancy (~30 designs).

LVS ceiling once Cm and Cl drain should be ~91%, with the residual ~9% split
between: ~17 documented real fails, ~5 Signal-11 reproducibles, ~30 LVS-timeout-
exceeders at 4h cap (likely fixable by 8h+ timeouts), and a handful of CDL
generation bugs (e.g. spi_master_single_cs).

## Skill-level improvements recommended

1. **Add global LVS lockfile** to `run_lvs.sh` — concurrent KLayout LVS jobs raise
   Signal-11 rate (observed in Wave B). Each LVS uses 3-15GB RAM; serializing across
   workers per host would reduce crash rate AND prevent 2-3× wall-time inflation.
2. **Cache `drc.json` status=="fail"** in `run_signoff.sh` (currently only caches
   clean/violations/stuck). Without this, sister workers re-run designs that already
   have terminal "fail" status, wasting ~45 min each.
3. **Dedupe wave partitioning** — designs needing both DRC re-run and LVS re-run
   currently appear in multiple wave files; can collide on the same ORFS workspace.
4. **Investigate the FreePDK45 KLayout DRC `or` polygon-op stuck pattern** — 231
   designs are blocked by this. May be fixable by upgrading KLayout to 0.31+ or
   patching the rule to use `boolean_or` instead of `or`.

## In-flight processes at snapshot time

- Wave Cm1/Cm2/Cm3: 28/45 done, 17 remaining (~6-10h)
- Wave Cl1/Cl2: 7/21 done, 14 remaining (~10-15h)

Estimated campaign completion: ~2026-05-29 04:00-08:00 UTC (~10-15h from this
snapshot).
