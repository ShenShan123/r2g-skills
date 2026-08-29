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

## Semantic preflight r1

The read-only preflight separates a config-file delta from an executed ORFS
intervention. It is external shadow evidence only; it does not mutate the
canonical store, authority ledger, lifecycle registry, or production runtime.

- `sky130hs` and `sky130hd` use the same platform hook digest
  `a84110f70e0ff1540f4cfed6730d56b3011c6c06a6c897a4bd600522bb175dd3`.
- Each hook executes `set_global_routing_layer_adjustment ... 0.2`, so a
  candidate edit of `ROUTING_LAYER_ADJUSTMENT=0.05` is `NO_OP` and
  `INAPPLICABLE`.
- `nangate45` also hardcodes layer-specific adjustments and is rejected for
  this action family.
- `asap7` directly consumes `$::env(ROUTING_LAYER_ADJUSTMENT)` and is the
  current `EFFECTIVE` calibration target.

When `ORFS_ROOT` is declared but the hook cannot be read, the preflight is
`UNKNOWN` and the real executor does not launch either arm. Compatibility
fake-flow fixtures without `ORFS_ROOT` are marked `NOT_CHECKED` rather than
being treated as production semantic evidence.
