# TIMING_RELIEF_BUDGETED_V1 selector-before-execution preflight

This is the first genuinely execution-ordered prospective lane.  Six unseen
RTL lineages were frozen as `2 calibration + 4 held-out`; only their
`CORE_UTILIZATION=50` baseline was materialized and run.  The selector then
read baseline PPA/DEF graph context plus the frozen V4 support memory.  No
40%-utilization after project was created for an abstained lineage.

## Result

- baseline lineages: 6
- baseline full-oracle complete: 4; two are incomplete and fail closed
- pre-action selector: `PROPOSED=0`, `ABSTAINED=6`
- proposal coverage: `0.0`, frozen minimum: `0.5`
- after projects materialized: 0
- selected harmful rate: not evaluable (no selected after arms)
- cross-lineage TE: not evaluable (no selected after arms)
- canonical memory mutation: none
- promotion attempted: false

The abstentions are not treated as failures of the memory oracle.  The
complete baselines abstain because the current conformal intervals cross the
WNS/area/power contract boundaries or are OOD; the incomplete baselines abstain
because LVS/DRC/timing/graph hard evidence is missing.  This satisfies the
registered stopping rule: close the broad `50 -> 40` mechanism instead of
loosening the contract or running unselected after arms.

This directory deliberately contains no observation chain or staging import:
there was no executed after arm.  The earlier six-lineage pair campaign remains
separate external evidence and must not be relabeled as selector-before-run
evidence.
