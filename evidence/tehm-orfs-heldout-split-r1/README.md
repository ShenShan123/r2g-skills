# ORFS held-out split capture smoke (2026-08-27)

This receipt records two bounded exact-toolchain campaigns exercising the new
held-out capture lane.  Both campaigns used the packaged ORFS tree
(`OpenROAD 26Q3-1510-g6cb3f2b704`, `Yosys 0.68`, fingerprint
`5f6545216c040a863a4c169d90f895f218ad038bfd61991445f760ed43567c91`) and an
isolated `/tmp/tehm-orfs/.../staging/tehm.sqlite` destination.

`selector-arbiter-routing-heldout-r1` completed all 14 full-oracle checks,
equivalence, strict signoff, and graph extraction.  The pair was an
`UNKNOWN→PASS` neutral observation, so it was captured as `heldout` with
`learner_eligible=0`; it is not L4 fail→pass evidence.

`selector-alu-density-heldout-r1` is a negative/incomplete control: both arms
failed placement at 80→70% utilization.  Capture retained the failed pair as
`heldout`, `learner_eligible=0`, `oracle_complete=0`, and `outcome=FAIL`.
Neither campaign wrote canonical memory, triggered consolidation, or entered
production runtime.

The machine-readable summaries are in
[`heldout_split_capture_report.json`](heldout_split_capture_report.json).
Scratch projects and full reports remain under `/tmp` per the storage policy.
