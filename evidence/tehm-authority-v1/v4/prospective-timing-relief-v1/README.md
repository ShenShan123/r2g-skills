# TIMING_RELIEF_BUDGETED_V1 prospective external evidence

This directory records the first six-lineage ORFS cohort evaluated against the
typed `TIMING_RELIEF_BUDGETED_V1` contract.  The contract and action signature
were frozen before the independent validation phases.  The six custom RTL
lineages were first executed as an exploratory base/after pair capture; the
campaign manifest here was then frozen before equivalence, strict signoff, DEF
graph, observation, and staging-only evaluation.

This is a separate executed campaign from the earlier metadata-only frozen
manifest whose future names were `mock-alu`, `mock-array`, `fifo`, `uart-no-param`,
`aes-lvt`, and `riscv32i`; those names remain unexecuted and are not silently
claimed by this evidence.

## Frozen boundary

- platform: `sky130hs`
- action: `DENSITY_RELIEF`, `CORE_UTILIZATION 50 -> 40`
- lineages: `counter32`, `alu32` (calibration), `mux32`, `parity32`, `accum32`,
  `ctrl32` (held-out)
- independent equivalence: 6/6 PASS
- strict signoff and timing: 12/12 projects PASS
- DEF graph context: 12/12 complete
- external observations: 6/6 `ELIGIBLE_POSITIVE`
- staging import: 0; support partition is intentionally empty, so calibration
  and held-out observations cannot become learner support
- canonical mutation: none; promotion attempted: false

## Utility result

The contract is a typed proposal filter, not a replacement for the raw Pareto
gate.  Three observations pass the contract (`counter32`, `mux32`, `parity32`)
and three fail it (`accum32`, `alu32`, `ctrl32`).  The raw Pareto result remains
`HARMFUL` for all six observations because the area increase is still present.
No result in this directory authorizes canonical import or production runtime
use.

Replaying the current V4 conformal policy through the new selector yields
`PROPOSED=0`, `ABSTAINED=6`.  All six abstentions are due to intervals crossing
the WNS objective and the area/power budget boundaries.  The immediate blocker
is therefore calibration sharpness, not missing ORFS execution; the next
cohort must add action-bound, same-platform support and recalibrate intervals
before any proposal can be considered.

The exploratory pair-capture staging store and artifacts are kept outside this
directory under `/tmp/tehm-authority-v1/prospective-batch-full/staging/*_pair_capture_preexisting`;
the checked-in `staging/tehm.sqlite` is the clean, zero-import snapshot for the
manifest above.
