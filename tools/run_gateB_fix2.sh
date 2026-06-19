#!/bin/bash
set -u
cd /proj/workarea/user5/agent-r2g
SK=r2g-rtl2gds/scripts/flow
ING=r2g-rtl2gds/knowledge/ingest_run.py
mkdir -p tools/_gateB_logs
designs=(
  "design_cases/Verilog_Implementation_of_UART_SPI_I2C_Protocols_i2c__sky130hd"
  "design_cases/pipelined_rv32i_hdl_spi_controller__sky130hd"
  "design_cases/verilog_axis_axis_switch__sky130hd"
  "design_cases/RV32I_Memorycontroller__sky130hd"
)
echo "GATEB2_START $(date)"
for d in "${designs[@]}"; do
  ( log="tools/_gateB_logs/$(basename "$d").log"
    echo "START $(basename "$d") $(date)" > "$log"
    ORFS_TIMEOUT=2400 bash "$SK/fix_signoff.sh" "$d" sky130hd --check drc >> "$log" 2>&1
    echo "FIX_DONE rc=$? $(date)" >> "$log" ) &
done
wait
echo "ALL_FIX_DONE $(date)"
for d in "${designs[@]}"; do
  echo "=== ingest $(basename "$d") ===" >> tools/_gateB_logs/ingest2.log
  python3 "$ING" "$d" >> tools/_gateB_logs/ingest2.log 2>&1
done
echo "ALL_INGEST_DONE $(date)"
