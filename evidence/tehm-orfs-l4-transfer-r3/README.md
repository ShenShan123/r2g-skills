# ORFS L4 held-out transfer attempt (2026-08-27)

This is an evaluation-only held-out capture from the packaged ORFS toolchain.
The source-frozen `selector_fifo16` lineage used a `CORE_UTILIZATION` 85→75
change on `sky130hs`.  The 85 arm failed at route (`flow_rc=2`); the 75 arm
completed synthesis, route, finish, DRC, LVS, strict signoff, PPA, graph and
the remaining provenance checks.

The result is a genuine ORFS fail→pass observation, but it is not an L4
transfer witness: the failed before arm cannot satisfy the exact two-sided
14-check oracle.  Capture therefore kept the row in the held-out staging
database with `learner_eligible=false`; canonical memory, causal learner
support, production runtime and promotion state were unchanged.

The complete run remains under `/tmp/tehm-orfs/selector-fifo-density-heldout-r9`.
`orfs_l4_transfer_report.json` records the source-freeze, manifest, staging
database and toolchain digests plus the per-arm exact-check result.
