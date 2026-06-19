#!/bin/bash
# Gate B seed campaign: drive 5 pending sky130hd DRC-fail designs through the
# signoff-fix loop with the new density_relief strategy. Records fix_log.jsonl
# before->after pairs (seeds the dense-reward gradient + the m3.2 density_relief
# recipe), then ingests honestly. Leaves the other 4 m3.2 designs at baseline
# util for the A/B trial. See references/engineer-loop.md "Gate B".
set -u
cd /proj/workarea/user5/agent-r2g
SK=r2g-rtl2gds/scripts/flow
ING=r2g-rtl2gds/knowledge/ingest_run.py
mkdir -p tools/_gateB_logs

designs=(
  "design_cases/vtr_verilog_to_routing_min_odin_ii_regression_test_benchmark_verilog_FIR_ex4EP16_fir_10__sky130hd"
  "design_cases/verilog_ethernet_eth_mac_mii__sky130hd"
  "design_cases/wb2axip_aximrd2wbsp__sky130hd"
  "design_cases/verilog_can_can_fifo__sky130hd"
  "design_cases/verilog_axi_rtl_axil_reg_if__sky130hd"
)

echo "GATEB_START $(date)"
for d in "${designs[@]}"; do
  (
    log="tools/_gateB_logs/$(basename "$d").log"
    echo "START $(basename "$d") $(date)" > "$log"
    ORFS_TIMEOUT=2400 bash "$SK/fix_signoff.sh" "$d" sky130hd --check drc >> "$log" 2>&1
    echo "FIX_DONE rc=$? $(date)" >> "$log"
  ) &
done
wait
echo "ALL_FIX_DONE $(date)"

# Ingest serially (busy_timeout makes concurrent safe, but serial is cleaner here)
for d in "${designs[@]}"; do
  echo "=== ingest $(basename "$d") ===" >> tools/_gateB_logs/ingest.log
  python3 "$ING" "$d" >> tools/_gateB_logs/ingest.log 2>&1
done
echo "ALL_INGEST_DONE $(date)"
