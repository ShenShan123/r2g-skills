# Faraday DSP / RISC viability assessment

**Date:** 2026-04-26 (initial), revised 2026-04-27 after batch2rtl import
**Question:** Can the Faraday DSP and RISC designs in `batch2rtl/Faraday ASIC/` be flowed through `r2g-rtl2gds`?

**Updated verdict:** **Yes** — both fit behavioral SRAM stubs. The earlier
"no" verdict was based on assumed SRAM sizes (CM4k/CM8k/EEPROM scale,
~MB-class). The actual SRAM cuts in `batch2rtl/Faraday ASIC/{DSP,RISC}/`
are much smaller and lie under the behavioral-stub ceiling.

## Faraday RISC SRAM sizes (actual, from batch2rtl import)

| Wrapper module     | Used as            | Rows × Bits | Total bits | Strategy |
|--------------------|--------------------|-------------|-----------|---------|
| `tsyncram_4x32`    | IRAM_VALID         | 4 × 32      | 128        | Behavioral |
| `tsyncram_128x22`  | ICACHE_TAG, DCACHE_TAG | 128 × 22 | 2,816 (×2) | Behavioral |
| `tsyncram_512x32`  | ICACHE_INST, DCACHE_DATA, DRAM_DATA, IRAM_DATA, ICACHE_INST0 | 512 × 32 | 16,384 (×5) | Behavioral |

- **Total memory bits across 8 instances:** 87,680 (~88K)
- **Largest single memory:** 16K bits (well under 32K-bit
  `SYNTH_MEMORY_MAX_BITS`)
- **Synth result:** 213 s under `SYNTH_HIERARCHICAL=1`, 974MB peak
  memory, no ABC explosion

The original wrappers (`batch2rtl/Faraday ASIC/RISC/rtl/syn/run/sram_*.v`)
instantiate Faraday's internal `SYHD130_*` macros (130nm tech). They are
empty stubs — the wrappers wire up the macro pins but nothing implements
the macro itself. We replace them with behavioral synchronous SRAM
modules (`design_cases/faraday_risc/rtl/hdl/sram_behavioral.v`). The
single-port semantics (CLK + WEN/REN + ADDR + DATA_IN/DATA_OUT) infer
cleanly through Yosys `memory_collect` / `memory_map`.

### Multi-clock handling (was claimed blocker)

`RISC.cons` defines `SYSCLK` and `BUSCLK` both at 6 ns. We define both
in the SDC at 10 ns (relaxed for nangate45) with
`set_clock_groups -asynchronous` so ORFS analyzes them as independent
domains. Set `set_false_path` from async resets / JTAG / config inputs
matches the original's intent. ORFS handled the multi-clock SDC without
issue at synth time — confirmed below.

## Faraday DSP SRAM sizes (actual, from batch2rtl import)

| Wrapper module | Style       | Rows × Bits | Total bits | Strategy |
|----------------|-------------|-------------|-----------|---------|
| `SW10200C`     | 2-port (A/B) | 32 × 12     | 384       | Behavioral |
| `SW10201A`     | 2-port (A/B) | 32 × 26     | 832       | Behavioral |

- **Total memory bits across all instances:** ~1.2K (negligible)
- The DSP design itself is large (84 RTL files, ~6,500 lines for the
  top, multi-process-node alternates `t_pin.013/.018/.035.v`, plus a
  DFT wrapper). The complexity sits in **logic**, not memory.

### DSP-specific blockers (real ones)

- **Multiple module-name collisions** across alternative tech-node
  source files (`t_pin.013.v` / `.018.v` / `.035.v` all define
  `module PINs`). Must select one.
- **PLL black-box** in the `TOP` module — non-synthesizable; use
  `DSP_CORE` as the synth top instead.
- **DFT wrapper** in `wrapper.v` (`DSP_CORE_top` + `DSP_CORE_wrapper`)
  adds scan-test infrastructure. Skip the wrapper file unless you
  define `FD_DFT`.
- **Multi-clock generation** inside `CLKC` module (`DSPCLK` derived from
  `T_CLKI_PLL` / `T_CLKI_OSC` via `T_Sel_PLL` mux). Externally-driven
  via `DSPCLK_insert_buf_i`. Treat as single clock for MVP.

The DSP path is more involved than RISC because of these structural
issues, not because of the SRAMs. Marked as a separate tracked task.

## Why the original assessment was wrong

The 2026-04-26 viability doc listed Faraday SRAM names like CM4k, CM8k,
DM8k, ECM32kx24, EEPROM (32K-256K rows). Those names appear in older
Faraday FS90A_B documentation but are **not** present in the actual RTL
shipped in `batch2rtl/Faraday ASIC/`. The shipped DSP RTL uses small
test SRAMs (32×12, 32×26) and the shipped RISC RTL uses cache-line cuts
(4×32, 128×22, 512×32). Always inspect the actual RTL before assuming
SRAM scale.

## Recommendation

- **Faraday RISC**: in progress. Synth passed (213 s); floorplan / place
  / route / signoff to follow on the same nangate45 platform with
  behavioral SRAM stubs and dual-clock SDC.
- **Faraday DSP**: tractable but requires module-collision pruning and
  picking the right synth top (`DSP_CORE`, not `TOP`). SRAMs are
  trivial (1.2K total bits). Tracked separately.
- **BOOM 12 variants**: in progress. SmallSEBoom is being retried with
  `SYNTH_HIERARCHICAL=1` to escape the 4 h ABC bottleneck observed
  with the flat-mode 168K behavioral memory bits.
- **Gaisler leon2**: hard skip. VHDL only — local Yosys lacks both
  GHDL plugin and Verific support.
