# ORFS source-disjoint calibration cohort (2026-08-29)

This is shadow-only evidence for the `DENSITY_RELIEF` action on `sky130hs`.
Three independent RTL source lineages (`future_prospective_logic_v12/v13/v14`)
were run as `CORE_UTILIZATION 50 -> 40`.  All six before/after ORFS arms
completed, Yosys source equivalence passed, strict signoff returned zero, and
both graph extractions completed.  Capture marked every row
`oracle_complete=true`, but the calibration split is intentionally
`learner_eligible=false`.

The physical predictor found three compatible strict-clean graph contexts and
covered all finite metrics (`3/3` lineages, empirical coverage `1.0`).  The
safety gate nevertheless rejected the cohort: all three rows were harmful
under the current max-regression policy (`harmful_rate=1.0`), primarily from
area growth and timing regression.  The result is therefore
`shadow_calibration_failed`, not a usable Parametric policy.  No authority,
canonical-memory, lifecycle, or production-runtime mutation occurred.

The original `nangate45` attempt remains a separate negative record: its
platform had no action-compatible authority context and calibration abstained
before prediction.  The campaign materializer fix in this revision also binds
template-owned `PDN_TCL` paths when a source-only/custom RTL top changes, so a
missing `grid_strategy-*.tcl` under the new logical top cannot masquerade as an
RTL or model failure.

Raw ORFS trees and the temporary shadow authority snapshot are under
`/tmp/tehm-orfs/`; this directory stores only auditable summaries and hashes.
