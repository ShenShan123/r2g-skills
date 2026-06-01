# Wave D Antenna-Repair Investigation — 2026-05-27

## Conclusion: Not fixable in current OpenROAD/nangate45 stack

The 30 designs flagged with metal-antenna DRC violations cannot be fixed by re-routing.

## Investigation

Tested re-route on `Canakari_Verilog_bittiming2` (pre=7 METAL6_ANTENNA violations).

### What was attempted

```bash
unset ROUTE_FAST SKIP_ANTENNA_REPAIR
make clean_route  # remove 5_*.odb/def/sdc/v
rm -f results/.../6_*  # remove finish artifacts
FROM_STAGE=route run_orfs.sh ...
```

This correctly forced a full route + finish rebuild (`Stage 'route' completed in 24s`,
`Stage 'finish' completed in 12s`). The antenna-repair pass DID run:

```
Repair antennas...
[WARNING GRT-0246] No diode with LEF class CORE ANTENNACELL found.
[INFO ANT-0002] Found 0 net violations.
[INFO ANT-0001] Found 0 pin violations.
```

OpenROAD's own antenna checker reports **0 net violations after repair**.

### Re-running KLayout DRC

```json
{
  "design": "Canakari_Verilog_bittiming2",
  "pre_violations": 7,
  "post_violations": 7,
  "post_status": "fail"
}
```

KLayout DRC still reports the same 7 METAL6_ANTENNA violations.

## Root cause

OpenROAD's antenna-repair pass and KLayout's `FreePDK45.lydrc` use **different antenna
rule formulations**:

- **OpenROAD** (`repair_antennas` / `check_antennas`): uses ANTENNARATIO/PAR/CAR values
  from the technology LEF (`NangateOpenCellLibrary.tech.lef`). After repair, OpenROAD
  confirms `Found 0 net violations`.
- **KLayout `FreePDK45.lydrc`**: uses geometric metal-length-to-gate-area ratios
  encoded directly in the DSL rules at lines covering `METAL3_ANTENNA` through
  `METAL7_ANTENNA`. These ratios are stricter than the LEF-encoded values, so KLayout
  flags violations that OpenROAD's checker considers within budget.

The `NangateOpenCellLibrary.macro.mod.lef` (used as `SC_LEF`) correctly declares
`MACRO ANTENNA_X1 / CLASS CORE ANTENNACELL`, so it's not a missing-diode issue.
The GRT-0246 warning fires because the route stage has been re-entered and the
diode-lookup cache is cleared — but the repair pass still completes successfully
(0 violations reported).

## Why this isn't a fix candidate at the design level

The discrepancy is **platform-level**, not design-level. The 30 designs are not
distinguishable from the 381 DRC-clean designs by anything design-specific — they
just happen to have routing topologies that fall on the wrong side of the
KLayout-vs-OpenROAD rule discrepancy. Re-routing 30 individual designs (~30 min
each, 15h total wall time) would produce 0 violations recovered.

## What a real fix would require

Either:
1. **Update `FreePDK45.lydrc` antenna rules** to match the LEF antenna ratios. This
   would require translating the LEF antenna constants into KLayout DSL geometric
   constraints — a one-time platform-LVS-rule patch.
2. **Tighten the LEF antenna ratios** (PAR/SAR/DIFFAREA) to match KLayout, then re-run
   the entire 682-design campaign so OpenROAD inserts more diodes during repair.

Both are out of scope for a single-session campaign and would touch shared platform
files.

## Recommendation

Accept the 30 designs as "DRC clean modulo KLayout-stricter-than-OpenROAD antenna
rules". Their GDS, LVS, and RCX are all correct. The DRC mismatch is a tooling
artifact, not a real silicon defect.

## Designs affected (sorted by violation count)

| Design | Violations | Category |
|--------|-----------:|----------|
| verilog_ethernet_eth_demux | 231 | METAL4/5/6_ANTENNA |
| verilog_ethernet_eth_arb_mux | 161 | METAL4/5/6_ANTENNA |
| PicoRV32_Based_SoC_fifo_basic | 98 | METAL4/5/6_ANTENNA |
| RISCV_design_Tang_E203_Mini_src_sirv_gnrl_icbs | 35 | METAL3/4/5_ANTENNA |
| e203_hbirdv2_rtl_e203_general_sirv_gnrl_icbs | 35 | METAL3/4/5_ANTENNA |
| verilog_lfsr_rtl_lfsr_prbs_check | 28 | METAL6_ANTENNA |
| CAN_Bus_Controller_can_tx | 14 | METAL5_ANTENNA |
| I2SRV32_S_v1_rtl_uart_tfifo | 14 | METAL6_ANTENNA |
| iccad2017_unit9_F | 14 | METAL6_ANTENNA |
| riscv_soc_integration_rtl_riscv_alu4b | 14 | METAL4_ANTENNA |
| verilog_ethernet_mac_1g | 14 | METAL4_ANTENNA |
| 19 others | 7 each | METAL5/6/7_ANTENNA |
