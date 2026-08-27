# Batch-0 `riscv32i` exact-toolchain replay (2026-08-27)

This is a bounded, staging-only ORFS observation under the Batch-0 source
freeze.  It ran only the source-disjoint support candidate
`sky130hs:riscv32i:u50->u40` with the packaged ORFS tree at
`/data2/quewk/r2g-repro/OpenROAD-flow-scripts` (OpenROAD 26Q3 and Yosys 0.68).
It is not a promotion attempt and it did not mutate canonical memory.

Both arms completed the six ORFS stages and the independent RTL equivalence
oracle returned `PASS`.  The strict signoff gate failed for both arms because
Netgen reported `netgen_topology` LVS mismatch; timing was also severe and the
strict def-graph step therefore failed closed as `invalid`.  The observation
utility verdict was `HARMFUL`: reducing utilization from 50% to 40% increased
die area and power even though setup timing improved.  Consequently all seven
manifest observations remain `INCOMPLETE_EXTERNAL_ONLY`, no observation is
learner-eligible, staging imported zero records, and canonical snapshots are
unchanged.

The scratch campaign remains disposable under `/tmp`; this directory stores
the durable, machine-readable summary and the hashes needed to locate and
audit the external receipts.
