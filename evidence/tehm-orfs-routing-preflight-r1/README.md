# ORFS routing action-family preflight (2026-08-27)

This is a bounded diagnostic receipt, not learner or promotion evidence.  The
campaign used the packaged ORFS tree at `/data2/quewk/r2g-repro/OpenROAD-flow-scripts`
and ran only one `sky130hs/gcd` pair for
`ROUTING_CAPACITY_RECOVERY` (`default -> ROUTING_LAYER_ADJUSTMENT=0.05`).

Both ORFS arms completed with `flow_rc=0`, route was clean, and the source DEF
graph extraction completed.  The pair is still incomplete for the TEHM hard
oracle: only route was definitive (`obligation_coverage=1/3`), timing was
violated (`WNS=-0.256353 ns`), and DRC/LVS reports were absent.  The adapter
therefore records `oracle_complete=false`, `original_failure=UNKNOWN`, and
`utility_verdict=NEUTRAL`; it is not a support transition.

The raw run tree remains regenerable scratch data under
`/tmp/tehm-orfs/orfs-next-routing-preflight`.  The JSON receipt records the
content hashes and exact tool binding.  The strict capture gate assigns such a
pair to calibration with `learner_eligible=0`; if a prior campaign left an
incomplete transition as `training/learner_eligible=1`, recapture fails closed
instead of overwriting immutable membership.
