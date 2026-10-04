# Repair-Family Candidate Inventory

This is a replay nomination list, not an admitted benchmark pool. Historical rows must be
reproduced on the current frozen toolchain before they count as evidence.

| Family | Current natural pairs | Historical nominations | Local snapshots resolved |
|---|---:|---:|---:|
| `footprint_congestion` | 6 | 0 | 0 |
| `pin_perimeter` | 0 | 19 | 14 |
| `pdn_floorplan` | 0 | 4 | 2 |
| `antenna_closure` | 0 | 50 | 35 |
| `rule_specific_drc` | 0 | 0 | 0 |
| `timing_closure` | 0 | 0 | 0 |

## Highest-Priority Replays

### pin_perimeter

- `verilog_axi_axi_interconnect` / `axi_interconnect` / `nangate45`: [ERROR PPL-0024] Number of IO pins (1914) exceeds maximum number of available positions (976). Increase the die perimeter from 555.68um to 1071.84um.
- `verilog_axi_axil_interconnect` / `axil_interconnect` / `nangate45`: [ERROR PPL-0024] Number of IO pins (1218) exceeds maximum number of available positions (700). Increase the die perimeter from 399.62um to 682.08um.
- `iscas85_c2670` / `c2670` / `nangate45`: [ERROR PPL-0024] Number of IO pins (373) exceeds maximum number of available positions (368). Increase the die perimeter from 214.96um to 208.88um.
- `verilog_ethernet_eth_mac_1g` / `eth_mac_1g` / `nangate45`: [ERROR PPL-0024] Number of IO pins (1251) exceeds maximum number of available positions (928). Increase the die perimeter from 529.00um to 700.56um.
- `verilog_ethernet_ip_arb_mux` / `ip_arb_mux` / `nangate45`: [ERROR PPL-0024] Number of IO pins (1517) exceeds maximum number of available positions (1004). Increase the die perimeter from 570.74um to 849.52um.
- `verilog_ethernet_ip_demux` / `ip_demux` / `nangate45`: [ERROR PPL-0024] Number of IO pins (1521) exceeds maximum number of available positions (860). Increase the die perimeter from 490.72um to 851.76um.
- `verilog_ethernet_ip_mux` / `ip_mux` / `nangate45`: [ERROR PPL-0024] Number of IO pins (1520) exceeds maximum number of available positions (924). Increase the die perimeter from 526.72um to 851.20um.
- `iscas89_s38584a` / `s38584` / `nangate45`: [ERROR PPL-0024] Number of IO pins (342) exceeds maximum number of available positions (320). Increase the die perimeter from 188.34um to 191.52um.
- `iccad2015_unit03_in1` / `top` / `nangate45`: [ERROR PPL-0024] Number of IO pins (6761) exceeds maximum number of available positions (3648). Increase the die perimeter from 2052.20um to 3786.16um.
- `iccad2017_unit16_F` / `top` / `nangate45`: [ERROR PPL-0024] Number of IO pins (631) exceeds maximum number of available positions (628). Increase the die perimeter from 359.52um to 353.36um.

### antenna_closure

- `verilog_ethernet_udp_mux` / `udp_mux` / `nangate45`: METAL4_ANTENNA
- `verilog_ethernet_ip_mux` / `ip_mux` / `nangate45`: METAL5_ANTENNA
- `verilog_ethernet_ip_demux` / `ip_demux` / `nangate45`: METAL4_ANTENNA
- `verilog_ethernet_ip_arb_mux` / `ip_arb_mux` / `nangate45`: METAL4_ANTENNA
- `verilog_axi_axil_interconnect` / `axil_interconnect` / `nangate45`: METAL4_ANTENNA
- `verilog_ethernet_eth_demux` / `eth_demux` / `nangate45`: METAL6_ANTENNA
- `verilog_ethernet_ip_eth_tx_64` / `ip_eth_tx_64` / `nangate45`: METAL6_ANTENNA
- `iccad2015_unit01_in1` / `top` / `nangate45`: METAL6_ANTENNA
- `verilog_ethernet_ip_eth_rx_64` / `ip_eth_rx_64` / `nangate45`: METAL4_ANTENNA
- `wb2axip_axilsingle` / `axilsingle` / `nangate45`: METAL6_ANTENNA

### pdn_floorplan

- `vtr_verilog_to_routing_min_odin_ii_regression_test_benchmark_verilog_c_functions_clog2_clog2_test` / `simple_op` / `nangate45`: [ERROR PDN-0185] Insufficient width (2.66 um) to add straps on layer metal4 in grid "grid" with total strap width 28.5 um and offset 2.0 um.
- `vtr_verilog_to_routing_min_odin_ii_regression_test_benchmark_verilog_c_functions_clog2_clog2_test` / `simple_op` / `sky130hd`: [ERROR PDN-0185] Insufficient width (8.28 um) to add straps on layer met4 in grid "grid" with total strap width 15.2 um and offset 13.6 um.

## Evidence Gaps

- `rule_specific_drc`: no trusted non-footprint repair pair yet.
- `timing_closure`: old timing verdicts change the period or fail to close WNS; none are admitted.
- `antenna_closure`, `pin_perimeter`, and `pdn_floorplan`: strong nominations exist, but all
  require current-toolchain replay and source provenance reconstruction.
