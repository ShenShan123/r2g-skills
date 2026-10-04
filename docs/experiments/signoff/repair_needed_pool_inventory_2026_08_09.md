# Repair-Needed RTL Evidence Inventory

This file is generated from immutable Experiment 2 screening records. It is development evidence, not a paper result.

## Summary

- Unique designs: **60**
- Unique frozen tasks: **66**
- Natural repair pairs: **6**
- Controlled repair challenges: **0**
- Clean-sentinel candidates: **37**
- Unresolved near misses: **5**
- Quarantined pool records: **7**
- Natural symptom distribution: `{"drc": 6, "route": 2}`

## Natural Repair Pairs

| Fixture | Source | Size | Failure | Initial util | Clean util | DRC | Route | Integrity |
|---|---|---:|---|---:|---:|---:|---:|---|
| `Fuxi_soc_peripherals_lcd_nt35510_controller_src_nt35510_apb_adapter_v1_0` | `MaxXSoft/Fuxi` | small | drc, route | 25.0 | 17.0 | 2.0 | 1.0 | pass |
| `can_fifo` | `dpiegdon/verilog-can` | medium | drc | 20.0 | 12.0 | 10.0 | 0.0 | pass |
| `eth_mac_mii` | `alexforencich/verilog-ethernet` | medium | drc | 25.0 | 17.0 | 36.0 | 0.0 | pass |
| `logikbench_blocks_jesd204b` | `zeroasiccorp/logikbench` | medium | drc | 25.0 | 17.0 | 12.0 | 0.0 | pass |
| `mor1kx_rtl_verilog_mor1kx_ctrl_prontoespresso` | `openrisc/mor1kx` | medium | drc, route | 25.0 | 17.0 | 98.0 | 2.0 | pass |
| `ultraembedded_sdram_axi` | `ultraembedded/core_sdram_axi4` | medium | drc | 25.0 | 17.0 | 60.0 | 0.0 | pass |

## Replay Operators

- `footprint_relief`: 6 independent designs; status `supported_for_controlled_challenge_pilot`.

## Interpretation

Natural repair pairs remain the highest-validity evidence. A replay operator may be used only to build a separately reported controlled-challenge stratum; it does not turn an artificial challenge into a natural failure. Clean sentinels are retained to measure over-repair and global regressions.
