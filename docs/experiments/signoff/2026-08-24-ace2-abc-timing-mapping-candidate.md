# Sky130HD ACE2 ABC Timing-Mapping Candidate

Status: **early-screen rejected.** This exact effect fingerprint is negative
development evidence, not learner evidence or a promotion claim.

## Frozen Task

- Subject: `ace2_absolute_rope_score_core` from `argus-aiteam/ace-2`, commit
  `af5dff46c9d752a75c5419e5d5f67b8f8741aff3`.
- Platform/task: Sky130HD, 100 MHz, registered 10 ns SDC.
- Baseline: two complete source-bound runs with setup WNS `-3.12182 ns`, while
  DRC, LVS, route, antenna and RCX were clean.
- Sole effective delta: `ABC_AREA=1 -> 0`. The candidate retained the source
  closure, SDC, `CORE_UTILIZATION=20`, `PLACE_DENSITY_LB_ADDON=0.20`, and the
  full strict-signoff check set.

## Result

The candidate reached a valid post-CTS timing-repair checkpoint in
`RUN_2026-08-24_18-57-36_716048_2ef6`. Its first checkpoint reported setup WNS
`-5.860 ns` across 215 endpoints, substantially worse than the repeated frozen
baseline. The flow was deliberately stopped before detailed route and signoff:
it could no longer be a competitive fixed-task candidate, and spending the
remaining route/DRC/LVS budget would not create promotion-quality evidence.

This rejects the `ABC_AREA=0` effect for this ACE2 timing mechanism. It does
not imply that the subject is unrepairable, nor does it classify an incomplete
flow as a tool or design failure.
