# ORFS Batch-0 exact-toolchain smoke (2026-08-26)

This receipt records a bounded, scratch-only execution of the existing
`orfs-batch0-v1` manifest.  The executor used the packaged OpenROAD/Yosys pair
under `/data2/quewk/r2g-repro/OpenROAD-flow-scripts`, with no post-prepare
constraint or RTL edits.  The pair was freshly prepared after the
`timing_contract` gate landed; both arms matched the manifest's 1.4 ns target
and SDC digest.

The SPI `CORE_UTILIZATION 50 -> 40` held-out pair completed synthesis, route,
finish, independent source-identity equivalence, strict signoff, PPA
extraction, and DEF graph extraction on both arms.  It is therefore an
`ELIGIBLE_POSITIVE` external observation, but remains `learner_eligible=false`
because its split is `heldout`.  The staging import admitted zero rows and the
protected canonical snapshot was unchanged.  This is a toolchain/oracle
receipt, not a production promotion or a claim about capability evolution.

An adjacent diagnostic scratch run also preserved negative evidence: JPEG
before failed at CTS (`CTS-0080 Sink not found`) and JPEG after timed out in
detailed routing.  Both attempts had `input_binding=true`; neither was
promoted to learner evidence.

Machine-readable summary: [`batch0_exact_pair_report.json`](batch0_exact_pair_report.json).
