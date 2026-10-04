# Sky130HD ACE2 Hierarchical-Synthesis Candidate

Status: **early-screen rejected.** This result is development negative evidence;
it is neither a strict-signoff result nor a learner/promotion claim.

## Frozen Task and Delta

- Subject: `ace2_absolute_rope_score_core` from `argus-aiteam/ace-2`, commit
  `af5dff46c9d752a75c5419e5d5f67b8f8741aff3`.
- Task: Sky130HD, 100 MHz, source-bound RTL closure, unchanged 10 ns SDC and
  fixed footprint (`CORE_UTILIZATION=20`, `PLACE_DENSITY_LB_ADDON=0.20`).
- Repeated baseline setup WNS: `-3.12182 ns`, with DRC, LVS, route, antenna and
  RCX clean.
- Sole effective delta: `SYNTH_HIERARCHICAL=0 -> 1`.

## Result and Interpretation

The candidate reached the completed CTS timing-repair checkpoint in
`RUN_2026-08-24_19-06-08_721247_3f79`. It reported setup WNS `-3.162 ns` at
207 violating endpoints, slightly worse than the frozen baseline and still far
from timing closure. Under the pre-registered early-reject rule, the process
was stopped before detailed route and signoff because this effect could not
become a competitive fixed-task repair.

Together with the previously rejected `ABC_CLOCK_PERIOD_IN_PS=8000` and
`ABC_AREA=0` candidates, this exhausts the three-candidate budget for the
generic timing-mapping mechanism on this natural ACE2 failure. Further variants
of those controls must not be renamed and retried; a later attempt requires a
different physical mechanism and fresh independent natural evidence.
