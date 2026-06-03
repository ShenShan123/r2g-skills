# rtl_designs — Directory Structure

This directory contains **724 RTL design entries** curated for open-source EDA flow
evaluation (synthesis, place-and-route, signoff) on the **nangate45** platform.

## Per-Design Layout

Each sub-directory is a self-contained design unit:

```
<design-name>/
  design_meta.json    # Design identity, quality scores, graph metadata
  rtl/                # Verilog source file(s) (1–60 files; avg ≈ 2)
  src_manifest.txt    # Newline-delimited list of RTL file paths (original source paths)
  [config.tcl]        # Optional extra config (rare; e.g. gaisler_leon2)
```

### `design_meta.json` Fields

| Field | Description |
|---|---|
| `design` / `top` | Design name and Verilog top-module name |
| `platform` | Target process (all entries: `nangate45`) |
| `rtl_files` | Ordered list of RTL source paths |
| `rtl_signature` | SHA-256 of RTL content (dedup key) |
| `graph_file` | PyG graph filename (`.pt`) |
| `graph_schema_version` | Schema tag for node/edge feature tensors |
| `design_bucket` | Size class: `small` / `medium` / `large` / `unknown` |
| `design_quality_score` | Floating-point quality metric |
| `design_novelty_score` | Novelty relative to the corpus |
| `design_redundancy_score` | Similarity to other entries |
| `design_action` | Curation decision (`keep` / `reject`) |
| `status` | Flow result (`success` for all 708 meta-carrying entries) |

## Population Summary

| Category | Count |
|---|---|
| Total directories | 724 |
| Directories with `design_meta.json` | 708 |
| Directories without `design_meta.json`* | 16 |
| Single-file RTL designs | 487 |
| Multi-file RTL designs | 221 |

*The 16 directories without metadata are large/complex designs (12 BOOM RISC-V
ChipTop variants, 3 Faraday IP blocks, 1 Gaisler LEON2) that carry only raw RTL and
have not been processed through the meta-generation pipeline.

## Design Size Distribution

| Bucket | Count |
|---|---|
| `small` | 263 |
| `medium` | 155 |
| `large` | 110 |
| `unknown` | 180 |

## Design Families (Top 20 by Prefix)

| Family prefix | Designs | Notes |
|---|---|---|
| `verilog` | 115 | Miscellaneous Verilog-HDL benchmarks |
| `vtr` | 65 | VTR/TITAN benchmark suite |
| `wb2axip` | 46 | Wishbone-to-AXI bridge family |
| `iccad2015` | 44 | ICCAD 2015 contest benchmarks |
| `iscas89` | 41 | ISCAS-89 sequential circuits |
| `iccad2017` | 40 | ICCAD 2017 contest benchmarks |
| `hdl` | 21 | HDL-Bits / educational |
| `RISC` | 13 | RISC-family CPU cores |
| `secworks` | 13 | Secworks crypto IP cores |
| `opdb` | 11 | OpenDB benchmark designs |
| `iscas85` | 11 | ISCAS-85 combinational circuits |
| `i2c` | 10 | I2C controller variants |
| `qspiflash` | 9 | QSPI flash controller family |
| `riscv` | 8 | RISC-V processor variants |
| `SoC` | 7 | System-on-Chip assemblies |
| `koios` | 7 | Koios deep-learning benchmarks |
| `ultraembedded` | 7 | Ultra-Embedded IP blocks |
| `Protocol` | 6 | Protocol-layer modules |
| `picorv32` | 6 | PicoRV32 RISC-V family |
| `PYGMY` | 6 | PYGMY ultra-low-power family |

## Naming Convention

Directory names encode the source repository and sub-path of extraction:

```
<repo-or-family>_<sub-path-components>_<module-or-variant>
```

Examples:
- `APB_Based_GPIO_Core_APB_GPIO_CORE_rtl_GPIO_top` — `GPIO_top` module from the
  `APB_GPIO_CORE/rtl/` path of the APB GPIO Core repo.
- `iccad2015_mgc_des_perf_1` — design `mgc_des_perf_1` from the ICCAD 2015 benchmark.
- `boom_mediumboom` — Medium-size BOOM ChipTop (no meta; raw RTL only).
