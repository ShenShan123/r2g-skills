You are repairing one isolated held-out Nangate45 100 MHz design. You may use only
the baseline evidence and same-model feedback supplied for this task. Return one
JSON object with a `proposals` array containing at most one bounded candidate.
The applicability object must use the exact field `required_failure_signatures`;
do not use `failure_patterns`. All `config_edits` values must be JSON strings.

Use `applicability.platform=nangate45`. For an antenna failure use
`failure_domain=antenna_drc` and an exact or wildcard antenna signature. Permitted
antenna edits are `SKIP_ANTENNA_REPAIR` in `0,1`,
`MAX_REPAIR_ANTENNAS_ITER_GRT` in `1,3,5`, and
`MAX_REPAIR_ANTENNAS_ITER_DRT` in `1,3,5,10`. For a setup-only failure use
`failure_domain=setup_timing`, signature `SETUP_TIMING`, and choose from
`ABC_AREA` in `0,1`, `ABC_CLOCK_PERIOD_IN_PS=8000`,
`ENABLE_PLACE_REPAIR_TIMING=1`, `SETUP_SLACK_MARGIN=0.2`, and
`SYNTH_HIERARCHICAL` in `0,1`. A mixed failure may combine bounded edits.

Do not change RTL, constraints, clock, footprint, utilization, density, routing
layers, technology files, or signoff rules. A failed attempt may inform the next
attempt for this task only. Return JSON only.
