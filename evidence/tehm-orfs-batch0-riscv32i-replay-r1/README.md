# Batch-0 `riscv32i` exact-toolchain replay (2026-08-27)

This is a bounded, staging-only ORFS observation under the Batch-0 source
freeze.  It ran only the source-disjoint support candidate
`sky130hs:riscv32i:u50->u40` with the packaged ORFS tree at
`/data2/quewk/r2g-repro/OpenROAD-flow-scripts` (OpenROAD 26Q3 and Yosys 0.68).
It is not a promotion attempt and it did not mutate canonical memory.

The initial replay completed the six ORFS stages and the independent RTL
equivalence oracle returned `PASS`, but the strict signoff gate failed for both
arms because Netgen reported `netgen_topology` LVS mismatch; timing was also
severe and the strict def-graph step therefore failed closed as `invalid`. The
observation utility verdict was `HARMFUL`: reducing utilization from 50% to
40% increased die area and power even though setup timing improved.
Consequently all seven manifest observations remain
`INCOMPLETE_EXTERNAL_ONLY`, no observation is learner-eligible, staging
imported zero records, and canonical snapshots are unchanged.

The scratch campaign remains disposable under `/tmp`; this directory stores
the durable, machine-readable summary and the hashes needed to locate and
audit the external receipts.

`power_connectivity_probe.json` and the `follow_up_replay` block in
`replay_report.json` record a follow-up diagnostic replay using the packaged
OpenROAD 26Q3/RCX toolchain, the same exact extracted SPICE, and each arm's
powered schematic. The riscv netlist retains two RTL-generated child modules;
propagating VDD/VSS through those module ports changed Netgen from
`6128 vs 6132` nets and top-pin failure to `6128 vs 6128` and `Circuits match
uniquely`. With the packaged writer, both `riscv32i_before_u50` and
`riscv32i_after_u40` now have DRC clean, LVS clean, and RCX complete receipts;
the only remaining strict-gate caveat is timing (`WNS -0.498750 ns` and
`-0.249082 ns`). This is a derived-schematic repair and bounded replay only:
it is not a new oracle-complete observation or support admission. The default
`_env.sh` still selects OpenROAD/RCX binaries whose database schema is
incompatible with this packaged ODB, and its logical `6_final.v` fallback would
miss five route-inserted antenna diodes.
