# V2 selector-before-ORFS preflight

The frozen V2 prospective cohort contains six new RTL lineages (2 calibration,
4 held-out).  All six 50% baselines were materialized and run before selector
evaluation.  The read-only, action-bound selector returned:

- `PROPOSED=0`, `ABSTAINED=6`, after projects materialized: `0`;
- proposal coverage `0.0`, below the frozen `0.5` threshold;
- stop status: `STOP_50_TO_45_LOW_PROPOSAL_COVERAGE`;
- no selected after ORFS, cross-lineage TE, harmful-rate, or promotion gate
  is claimed;
- canonical snapshot unchanged (`physical_effects=0`, `transitions=9`).

The six abstention reasons are preserved in `selector_decisions.json`.  Three
baselines were not selector-eligible because strict signoff/graph was
incomplete; the remaining three had objective or budget interval failures.
This closes the V2 action for now.  The next action is additional independent
mechanism registration or a better-typed action, not widening this selector.
