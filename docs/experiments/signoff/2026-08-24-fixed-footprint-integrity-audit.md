# Fixed-Footprint Integrity Audit for Recipe Readiness

## Finding

The current five-task Full-R2G capability rerun must not be used as a
fixed-footprint repair-rate numerator without an explicit task-policy change.

The terminal `serv` repair is recorded as `density_relief`, but its authoritative
`constraints/config.mk` contains the baseline `CORE_UTILIZATION=25` followed by
the generated repair delta `CORE_UTILIZATION=17`. Lower utilization increases
the auto-sized core/die area. The final evaluator records the resulting target
as `17%`, while the unresolved USB and Hazard3 tasks remain at `25%`.

Likewise, the existing `pin_perimeter_floor` implementation derives an explicit
larger `DIE_AREA`/`CORE_AREA` from the PPL-0024 perimeter request. This can be a
valid bounded area-feasibility recovery, but it is not a fixed-footprint action.

The current evaluator correctly checks that the final configuration is bound and
that signoff artifacts match that configuration. It does not compare the final
floorplan geometry or utilization against the baseline task manifest. Thus a
strict-clean result with a changed footprint can pass the evaluator's present
`protected_inputs` gate.

## Consequence

The reported `3/5 strict-clean` is a valid result for the broader **bounded
physical-repair** policy, but it is not evidence for the narrower frozen
fixed-footprint policy written for the current Recipe-readiness goal. Counting
it unchanged would make the acceptance criterion internally inconsistent, not
merely optimistic.

This is an experiment-integrity issue, not a claim that the underlying
strategies are invalid. The remedy is to freeze one interpretation before any
new promotion or validation evidence is counted:

1. **Fixed-footprint track:** reject any candidate or final run whose
   `DIE_AREA`, `CORE_AREA`, or `CORE_UTILIZATION` differs from the baseline;
   remeasure the baseline and Full-R2G numerator under that invariant.
2. **Bounded-area-recovery track:** permit only pre-registered geometry changes,
   record absolute/relative area and utilization deltas, and compare every method
   under the same bounded action space. Do not call its repair rate
   fixed-footprint.

The active readiness goal selected the first interpretation. Therefore its
baseline manifest and evaluator must receive a geometry-delta check before the
five-task regression, the independent cohort, or any A/B promotion can support
that goal's score.

## Reproduction

```text
/home/yangao/r2g_exp2_current5_r2g_capability_2026_08_24/
  methods/full-r2g/serv/project/constraints/config.mk
  methods/full-r2g/serv/project/reports/fix_log.jsonl
  methods/full-r2g/serv/validations/full_r2g_final.json
```

The action implementations are in
`r2g-skills/signoff-loop/scripts/loop/engineer_loop.py`:
`_lower_core_util()` and `_set_explicit_die()`.
