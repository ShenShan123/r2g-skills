# TEHM density-relief efficacy witness (scratch ORFS)

This directory records a compact, reproducible summary of one real ORFS A/B
trial executed after the causal online capability upgrade.  The source design
and ORFS artifacts remain under `/tmp/tehm-orfs/...`; this repository keeps the
receipt summary only, not the generated EDA tree.

- Rule: `rule_dcdcb203a5b1fae1` (`DENSITY_RELIEF`, `CORE_UTILIZATION 95 -> 30`).
- Trial: `trial_94ea979308b4b0250a13`, source lineage
  `stress-density95-densityrule-v2:sky130hs:selector_fifo16:base0`.
- Control arm A: ORFS place failed with exit code 2 because the 95% core
  utilization exceeded the placement-density limit.
- Rule arm B: full ORFS flow completed (`synth` through `finish`), route was
  clean, and timing was clean (`WNS=0.138271 ns`, `TNS=0`).
- Verdict: `win` (`A=[0.0]`, `B=[1.0]`), obligations and rollback both 1.0.
- Execution used `R2G_ORFS_SERIAL_AB=1` to keep the two high-utilization arms
  from exhausting host memory.  The default runner remains parallel.

This is a stress efficacy witness, not a production promotion result.  The
strict authority receipt is intentionally ineligible because independent
cross-lineage transfer, harmful-rate, and conformal-coverage evidence are not
attached.  No canonical-memory, runtime, or lifecycle promotion mutation was
performed.
