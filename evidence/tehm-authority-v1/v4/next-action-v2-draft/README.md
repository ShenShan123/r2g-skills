# Independent next action: `CORE_UTILIZATION 50 -> 45`

The broad `DENSITY_RELIEF 50 -> 40` mechanism was closed after the real
selector-before-execution preflight produced zero proposals out of six
lineages.  This directory is a separate, pre-registered action signature; it
does not reuse the V1 contract digest, V1 calibration policy, V1 rule, or V1
promotion authority.

## Frozen draft boundary

- contract: `TIMING_RELIEF_BUDGETED_V2_50_TO_45`
- action: `flow.CONFIG_DELTA / DENSITY_RELIEF / CORE_UTILIZATION 50->45`
- objective and resource budgets: explicitly copied into this new contract
  before any V2 execution; they remain subject to engineering review
- prospective split: 2 calibration + 4 held-out decision lineages
- platform: `sky130hs`
- source freeze and manifest digest: bound in the JSON artifacts here
- ORFS execution: none
- canonical mutation: none
- promotion attempted: false

The next required phase is an independent V2 support/calibration campaign.
Until that policy is calibrated and a new selector preflight is run, no V2
after arm or production action may be executed.
