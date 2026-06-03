# LVS Failure Causes — Corpus Analysis (2026-06-03)

> **Updated 2026-06-03 (post-residual campaign).** The original version of this report was built
> from the knowledge store (`runs.sqlite`), which was **stale** — 9 designs it labelled `skipped`
> were already `clean` on disk, several `crash`/`incomplete` rows had moved, and the `clean_algorithmic`
> bucket was hiding a real defect. A five-domain subagent campaign drove every persistent residual to
> ground truth, shipped skill fixes, and re-ingested the corpus. This document now reflects the
> corrected, re-ingested state. Skill changes are recorded in `references/failure-patterns.md`,
> `references/signoff-fixing.md`, and memory `project_lvs_campaign_2026-06-03`.

What causes LVS failures in the r2g-rtl2gds corpus, grounded in the re-ingested knowledge store
(`r2g-rtl2gds/knowledge/runs.sqlite`) and the per-design `reports/lvs.json` produced by
`scripts/extract/extract_lvs.py`. **Headline: of 674 designs with LVS data, 607 are clean (90%), and
of the 18 `fail` verdicts only TWO are genuine layout defects (both wb2axip) — the other 16, plus
all `crash`/`incomplete`, are KLayout-0.30.7 tooling limits, not layout defects.**

## LVS status distribution (per project, latest run)

| Status | Count | Meaning |
|--------|------:|---------|
| `clean` | 607 | Netlists match |
| `incomplete` | 44 | No verdict under the cap — **mostly a comparer bug, not slowness** (see §4) |
| `fail` | 18 | Comparer reached a "don't match" verdict (sub-classified below) |
| `unknown` | 3 | spi_master (CDL parse error) + 2 ChipTop BOOMs (intractable) |
| `crash` | 2 | KLayout SIGSEGV that did not survive retries (`usbf_device`, `wb2axip_axixclk`) |

**What changed from the stale snapshot:** `skipped` 17→**0** (nangate45 has a rule; 9 were already
clean, the rest re-ran to incomplete or were never run); `crash` 7→**2** (retry — see §1);
`clean_algorithmic` 7→**0** (a dead legacy label — re-extracting folds them into `fail`+sub-class,
and one was a real defect); `fail` 9→18 (now includes the ex-crash and ex-clean_algorithmic
symmetric residuals, correctly labelled).

## The 18 `fail` verdicts, sub-classified (balance-based classifier)

`extract_lvs.py::classify_lvs_mismatch` (refined 2026-06-03) labels each `.lvsdb` by **net balance +
device-count agreement**, not "zero net deltas". A symmetric-matcher residual leaves the unmatched
nets *perfectly balanced* (schematic-only == layout-only) with **every device matched** — the layout
is correct, only instance/net assignment is ambiguous. A genuine defect breaks that balance or emits
"not matching any net".

| mismatch_class | Count | Meaning | Layout correct? |
|----------------|------:|---------|-----------------|
| `symmetric_matcher` | 15 | KLayout-0.30.7 can't disambiguate symmetric structures | **Yes** |
| `real_connectivity` | 2 | Genuine net open — `wb2axip_axi2axilite`, `wb2axip_axilsingle` | **No** |
| (no lvsdb) | 1 | `iccad2015_unit08_in1` — verdict from a pre-patch deck, no db written | unknown |

### The 15 symmetric-matcher residuals (layout clean, tool can't prove it)

All carry **balanced** unmatched nets and **zero device mismatches**. Examples:
`aes_core` (8+8), `vlsi_axi_slave` (40+40), `iccad2017_unit5_F` (64+64), `iccad2017_unit5_G` (0+0),
`blake2s_core`, `iscas85_c499`/`c1355`, `verilog_axi_axil_crossbar_wr` (420+420),
`verilog_ethernet_axis_baser_tx_64`, the two vtr odin benchmarks, and the three crash-revealed
designs `wb2axip_aximwr2wbsp` (326+326), `core_usb_host_top` (22+22), `sha256_axi4_slave` (51+51).
Structures: parallel NAND/XOR/parity trees, crypto mixing rounds, register files / memory arrays,
replicated bit-slices, flat combinational benchmarks. **Raising the comparer budget does NOT help**
(re-confirmed). A per-design `same_nets!` seed CAN clear a *localized* one — validated on
`verilog_ethernet_axis_baser_rx_64` → clean (operator-only; see §5).

### The 2 genuine defects

- **`wb2axip_axi2axilite`** — one net open: `S_AXI_WREADY`'s register driver is on a different
  physical net (`$8924`) than its output buffer (+1 layout net, 346/346 devices match).
- **`wb2axip_axilsingle`** — 16 bus opens (`S_AXI_RDATA`/`M_AXI_AWVALID` bits): 104 vs 120 unmatched
  nets, `is not matching any net` on named ports. **This was mislabeled `clean_algorithmic`** (benign)
  and only surfaced once the classifier and the stale-label re-validation were applied.

## Root causes, ranked (corrected)

### 1. KLayout-0.30.7 comparer SIGSEGV — a non-deterministic heisenbug, RETRY-fixable

SIGSEGV in `db::NetlistCrossReference::sort_circuit()`/`gen_log_entry()` during the **compare** (after
extraction succeeds). The same GDS+CDL crashes most runs and survives ~1-in-N; a surviving run gives
the **true verdict**. `run_lvs.sh` now retries (`LVS_CRASH_RETRIES`, default 4; auto-1 for >150K
cells). **6 of 7 crash designs resolved** — 3 to `clean` (fifo_basic, verilog_axi_axi_fifo_wr,
butterfly_top_module_8_point), 3 to `fail`/symmetric (aximwr2wbsp, core_usb_host_top,
sha256_axi4_slave). `threads(1)`/`verbose(false)`/tcmalloc don't fix it; `flat` mode dodges the crash
but yields garbage mismatches. No newer KLayout exists on this host (≥0.30.10 would fix it at source).

### 2. KLayout-0.30.7 symmetric-matcher limit (15 of 18 `fail`)

The dominant `fail` cause. Layout correct; matcher can't fingerprint topologically identical
instances. Honest residual `lvs_symmetric_matcher_residual`; not back-end fixable; `same_nets!`
seeding is the only validated escape (operator-only, doesn't generalize).

### 3. Genuine connectivity defects (2 of 18 `fail`) — both wb2axip, see above.

### 4. `incomplete` (44) — mostly a comparer bug, not honest slowness

Three distinct causes, triaged by log: **comparer SIGSEGV** (e.g. `usbf_device`, 23K cells, crashes
at ~750s <1GB — *smaller* than aes_core which finishes → structure, not size), **comparer internal
assertion** `dbNetlistCompareCore.cc:1003 bt_count != failed_match` (e.g. `sdspi`), and **honest
extraction timeout** (layout-netlist extraction is super-linear: ~2700s@51K, ~10200s@62K — the old
3600s cap SIGTERM'd ≥50K designs mid-extraction). Only the third is helped by a bigger cap
(`run_lvs.sh` tiers raised: >50K→14400s, >100K→21600s, >250K→28800s, base 5400s). Memory never binds
(peak ≤1.65GB @242K). `Killed`/137 at low wall-time = external SIGKILL (shared-host contention — run
LVS serially). ChipTop 5–9M BOOMs die mid-geometry → intractable here.

### 5. CDL parse error (`unknown`, 1 design)

`spi_master_single_cs`: KLayout's SPICE reader mis-tokenizes an escaped-bracket negative-index
instance name (`Xr_CS_Inactive_Count\[-1\]$_DFFE_PN0P_`), aborting with `Pin count mismatch ...
Netlist::read` before any compare. Status `unknown`, reason `cdl_parse_error`. Not a layout defect.

## Why most LVS failures are NOT back-end-flow-fixable

| Cause | Remedy | Back-end fixable? |
|-------|--------|-------------------|
| comparer SIGSEGV | **retry** (`LVS_CRASH_RETRIES`) — now automatic; or newer KLayout | No (but auto-retried) |
| symmetric-matcher | `same_nets!` seeding (operator, localized only), or newer KLayout | No |
| incomplete (extraction) | bigger cap (auto-scaled), run serially | No (resource) |
| incomplete (comparer crash/assert) | newer KLayout | No |
| real_connectivity | inspect GDS/DEF at the named net (genuine defect) | No (defect) |
| cdl_parse_error | sanitise CDL instance name / avoid `[-1]` bit-blast | N/A |

So `diagnose_signoff_fix.py` reports these as honest, specifically-labelled residuals rather than
spawning doomed re-runs — except the crash, which is now retried automatically.

## How to reproduce this analysis

```bash
DB=r2g-rtl2gds/knowledge/runs.sqlite
# Re-ingest first if reports changed (idempotent): per design,
#   python3 r2g-rtl2gds/knowledge/ingest_run.py design_cases/<proj>
# Per-project latest status distribution:
sqlite3 "$DB" "
  WITH latest AS (SELECT r.* FROM runs r JOIN
    (SELECT project_path, MAX(ingested_at) mx FROM runs GROUP BY project_path) m
    ON r.project_path=m.project_path AND r.ingested_at=m.mx)
  SELECT lvs_status, lvs_mismatch_class, COUNT(*) FROM latest
  WHERE lvs_status<>'' GROUP BY lvs_status, lvs_mismatch_class;"
```

See also `references/signoff-fixing.md` (residual taxonomy + "Symmetric-matcher seeding") and
`references/failure-patterns.md` ("LVS symmetric-matcher residual", "LVS KLayout
sort_circuit/gen_log_entry SIGSEGV", "LVS incomplete is mostly a comparer bug").
