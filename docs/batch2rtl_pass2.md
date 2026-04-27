# batch2rtl pass 2 — Faraday RISC + BOOM retry + DSP/Leon2 audit

**Date:** 2026-04-27

Continuation of `batch2rtl/` work after the 2026-04-26 first pass which
landed Faraday DMA full-flow and queued BOOM SmallSEBoom for a retry.

## Inputs

```
batch2rtl/
├── BOOM CPU/                12 SmallBoom/MediumBoom/LargeBoom/MegaBoom variants
│   └── *_OpenRAM_FreePDK45/  Chipyard TestHarness top + freepdk45 SRAM stubs
├── Faraday ASIC/
│   ├── DMA/    11 .v files,   ✅ done in pass 1
│   ├── DSP/    84 .v files
│   └── RISC/   79 .v files
└── Gaisler/leon2/   79 .vhd + 3 .v
```

## Outcomes

| Design               | Verdict          | Stage reached                | Notes |
|----------------------|------------------|------------------------------|-------|
| Faraday DMA          | ✅ pass-1 done   | full flow + RCX, DRC stuck   | (no change) |
| Faraday RISC         | ✅ in flight     | place_gp ✓, resize ongoing   | 397K instances |
| Faraday DSP          | ❌ not viable    | parse failed + scale verdict | EEPROM 2 Mb, ECM32kx24 786 Kb |
| Gaisler leon2        | ❌ not viable    | n/a                          | VHDL only, no GHDL plugin |
| BOOM SmallSEBoom     | ✅ synth retry   | synth ✓ in 43 min, floorplan | prior run hung at ABC, retry uses SYNTH_HIERARCHICAL=1 |
| Other 11 BOOM variants | not started   | —                            | Same harness shape as SmallSEBoom; trivial parameter sweep once one converges |

## Key wins

### 1. Faraday RISC behavioral SRAM works at 87 K bits

Original viability doc (2026-04-26) called it intractable based on
assumed CM4k/CM8k/EEPROM SRAM scale. The actual `batch2rtl/Faraday ASIC/RISC/`
ships **3 unique** `tsyncram_*` cuts at much smaller sizes:

| Wrapper          | Rows × bits | Total bits | Instances | Strategy   |
|------------------|-------------|------------|-----------|------------|
| `tsyncram_4x32`  | 4 × 32      | 128        | 1         | behavioral |
| `tsyncram_128x22`| 128 × 22    | 2,816      | 2         | behavioral |
| `tsyncram_512x32`| 512 × 32    | 16,384     | 5         | behavioral |
| **total**        |             | **87,680** | **8**     | behavioral |

Replacement at `design_cases/faraday_risc/rtl/hdl/sram_behavioral.v`.
The wrappers stub out Faraday's 130 nm `SYHD130_*` macros; for ORFS we
replace them with generic single-port behavioral memories that Yosys
infers via memory_collect / memory_map.

**Synth result:** `213 s, 553 MB peak, 5 ABC runs under SYNTH_HIERARCHICAL=1`.
The skill's prior 50K-bit ceiling was overcautious; with hierarchical
synth, 87K-bit total memory is comfortable when no single memory exceeds
the 32K-bit ABC cell-count envelope.

**Multi-clock note:** SYSCLK + BUSCLK both at 10 ns with
`set_clock_groups -asynchronous`. ORFS handled the dual-clock SDC at
synth time; floorplan and place are clean so far.

### 2. BOOM ABC blowup unblocked by SYNTH_HIERARCHICAL=1

The pass-1 BOOM SmallSEBoom run hung at 4 h ORFS_TIMEOUT during ABC step
14 — flat-mode ABC was processing ~1 M cells from 168 K bits of
behavioral memory. Documented at the time, not fixed.

This pass added two flags to `boom_smallseboom/constraints/config.mk`:

```makefile
export SYNTH_HIERARCHICAL = 1
export ABC_AREA           = 1
```

Result: `2601 s synth (43 min)`, `2.86 GB peak Yosys memory`, 36
hierarchy-kept modules each through their own ABC run. Combined ABC
time across both passes: 894 s (15 min). The two big modules
(`DigitalTop`, `BoomCore`) and all 17 SRAM stubs ABCed without
incident. ChipTop ABC was trivial (just port wiring).

Floorplan now in progress. The skill's existing "When NOT to use
behavioral stubs" guidance has been updated: 168 K bits is fine if
hierarchical-synth is enabled.

### 3. Faraday DSP confirmed too large for behavioral

The DSP `m_*.v` files contain real big-bulk SRAM models in HDL form
(not just empty stubs):

- `EEPROM`     — 262,144 × 8  = 2,097,152 bits
- `ECM32kx24`  — 32,768 × 24  =   786,432 bits
- `CM4k/CM8k/DM8k/PM4k`       —   200 K-400 K bits each
- `EM4K/EM8K/EDM8k`           —    65 K-130 K bits each
- `EIO2k`                     —    32 K bits

Behavioral inference at this scale would explode synth memory. Even
with `SYNTH_HIERARCHICAL=1`, ECM32kx24 alone is ~800 K cells post
memory_map. EEPROM at 2 Mb is hopeless.

Solution requires a `fakeram45` tiler that doesn't yet exist in the
skill — one cell per logical SRAM, with multi-tile stitching for cuts
that exceed `2048 × 39`. Tracked as future work.

The DSP also ships Synopsys-style port-list syntax (`name[7:0]` inside
`module foo (...)` rather than `input [7:0] name` only in the body).
Yosys frontend doesn't accept this; added
`tools/fix_synopsys_port_widths.py` to rewrite. 67 edits in 22 files
were needed even for DSP_CORE with the bulk SRAMs excluded.

### 4. Gaisler leon2 hard skip

79 VHDL files vs 3 Verilog helpers; local Yosys is built without
Verific (`ERROR: This version of Yosys is built without Verific support.`)
and no `ghdl-yosys-plugin` is installed. Not feasible without tooling
work outside the scope of this pass.

## Skill changes

| File | Change |
|------|--------|
| `skills/r2g-rtl2gds/scripts/project/validate_config.py` | `get_ports -quiet $port` no longer triggers a false-positive "clock port not found" error. The regex now strips leading flag-style tokens. |
| `skills/r2g-rtl2gds/SKILL.md` | Behavioral-SRAM ceiling guidance updated with Faraday RISC datapoint (87 K bits, hierarchical, 213 s); BOOM SmallSEBoom retry plan documented. |
| `docs/faraday_viability.md` | Replaced fictional CM4k/EEPROM-scale assumptions with the actual sizes from `batch2rtl/Faraday ASIC/{DSP,RISC}/`; corrected the RISC verdict from "skip" to "in progress". |
| `tools/fix_synopsys_port_widths.py` (new) | Rewrites Synopsys-style `name[N:M]` port-list shorthand to plain `name` so Yosys's frontend accepts the input. Idempotent. |

## What's still running

- Faraday RISC: stage 3_4_place_resized (resize). 397 K instances; the
  resize stage is single-threaded and pin-by-pin, so a few-hour wall
  is expected. Will then run 3_5/3_6 (detailed place + cluster), CTS,
  route, finish, and signoff.
- BOOM SmallSEBoom: stage 2_floorplan just started after 43 min synth.
  ChipTop is ~110-150 K instances post-synth; floorplan + place_gp
  will take similar time to RISC.

Run-history will be ingested into the knowledge store once the flows
exit (`knowledge/ingest_run.py`).
