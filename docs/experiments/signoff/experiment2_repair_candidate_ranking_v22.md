# Experiment 2 Repair-Candidate Risk Ranking

This ranking is a cheap prioritization heuristic only. Formal admission still
requires two matching Default-ORFS failures at 100 MHz and an independent
strict-clean feasibility run under the preregistered action bounds.

| Rank | Candidate | Size | Port bits | Cell area (um^2) | I/O pressure | Band |
|---:|---|---|---:|---:|---:|---|
| 1 | `eth_mac_1g` | medium | 1251 | 10715.0 | 12.09 | very_high |
| 2 | `mor1kx_rtl_verilog_mor1kx_ctrl_prontoespresso` | medium | 844 | 15918.0 | 6.69 | high |
| 3 | `Hazard3_hdl_hazard3_csr` | medium | 509 | 9341.0 | 5.27 | high |
| 4 | `axi_register` | small | 488 | 10784.0 | 4.70 | medium |
| 5 | `Hazard3_hdl_hazard3_irq_ctrl` | small | 123 | 1011.0 | 3.87 | medium |
| 6 | `forencich_axis_broadcast` | small | 147 | 1789.0 | 3.48 | medium |
| 7 | `emaczero_eth_stats` | medium | 574 | 30582.0 | 3.28 | medium |
| 8 | `Hazard3_hdl_debug_dtm_hazard3_jtag_dtm_core` | small | 169 | 6218.0 | 2.14 | low |
| 9 | `Hazard3_hdl_debug_cdc_hazard3_apb_async_bridge` | small | 158 | 6037.0 | 2.03 | low |
| 10 | `mor1kx_rtl_verilog_mor1kx_cpu_espresso` | medium | 588 | 105091.0 | 1.81 | low |
| 11 | `mor1kx_rtl_verilog_mor1kx_cpu_prontoespresso` | medium | 588 | 105630.0 | 1.81 | low |
| 12 | `verilog_ethernet_eth_axis_tx` | small | 143 | 6649.0 | 1.75 | low |
| 13 | `emaczero_eth_pause` | small | 155 | 9559.0 | 1.59 | low |
| 14 | `emaczero_crc32` | small | 76 | 2382.0 | 1.56 | low |
| 15 | `cores_ftdi_async_bridge_rtl_ftdi_if` | small | 136 | 7681.0 | 1.55 | low |
| 16 | `axicb_crossbar` | large | 1179 | 676176.0 | 1.43 | low |
| 17 | `cores_uart_rtl_uart_wb` | small | 80 | 4407.0 | 1.21 | low |
| 18 | `emaczero_mdio_master` | small | 75 | 3911.0 | 1.20 | low |
| 19 | `riscv_alu` | medium | 100 | 8731.0 | 1.07 | low |
| 20 | `forencich_axi_cdma` | medium | 268 | 76608.0 | 0.97 | low |
| 21 | `i2c-master-alexforencich` | small | 65 | 4628.0 | 0.96 | low |
| 22 | `cores_usb_device_src_v_usbf_sie_rx` | small | 67 | 5218.0 | 0.93 | low |
| 23 | `Hazard3_hdl_debug_dtm_hazard3_jtag_dtm` | small | 86 | 8825.0 | 0.92 | low |
| 24 | `cores_spdif_rtl_spdif` | small | 37 | 2883.0 | 0.69 | low |
| 25 | `uart_axis` | small | 44 | 4562.0 | 0.65 | low |
| 26 | `mor1kx_rtl_verilog_mor1kx_dmmu` | medium | 257 | 158093.0 | 0.65 | low |
| 27 | `zipcpu_wbuart32_axiluart` | medium | 106 | 27884.0 | 0.63 | low |
| 28 | `mor1kx_rtl_verilog_mor1kx_immu` | medium | 256 | 170865.0 | 0.62 | low |
| 29 | `spi-master-nandland` | small | 29 | 2263.0 | 0.61 | low |
| 30 | `emaczero_icmp_echo` | medium | 184 | 91934.0 | 0.61 | low |
