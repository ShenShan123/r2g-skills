# Current R2G Revalidation of Seven Prior Repair-Needed RTLs

## Decision

The current R2G build cannot support a claim that it repairs 80% of this
seven-RTL set. This is not a low repair-rate result. The experiment was
stopped before a legal Full R2G repair submission could be completed because
the normal A/B planner attempted to execute a historical subject outside the
frozen experiment. Counting that as a normal repair attempt, disabling A/B to
avoid it, or silently using the resulting partial artifacts would all make the
comparison invalid.

Two of the seven old tasks also fail the current constraint-coverage gate, so
they are not legal single-clock 100 MHz repair challenges. The baseline audit
is complete and reproducible; the repair-rate numerator is deliberately
undefined until the containment defect is fixed and the eligible tasks are
rerun from fresh snapshots.

## Frozen Scope and Evidence

| Item | Value |
|---|---|
| Agent worktree | `/home/yangao/r2g-skills` at `b31caab84fe6c2145416f22eef6ac4f5c220303c` |
| Platform and target | Sky130HD, 100 MHz / 10 ns |
| Baseline | Untouched Default ORFS, two independent runs per RTL |
| Flow budget | 4 cores; 7,200 s maximum per ORFS flow; 14,400 s maximum per method/design |
| Knowledge seed | Canonical `knowledge.sqlite`, SHA-256 `a3c41e99bb5799407b830dea09519bb3dbab9b6c8bb9078605fe55085873d254` |
| Heuristics seed | SHA-256 `be94d0991c31c7b07eb0523c3809735b83bd2e4feee5f8ef36b7422fab989749` |
| ORFS | `a5ff7ef7dac4338e6e5fad7710b85fc6c8f3503c` |
| Campaign root | `/home/yangao/r2g_exp2_current7_revalidation_2026_08_24` |

The campaign manifest binds every source URL, Git commit, top module, RTL
closure digest, task specification, runtime tree, toolchain, and initial
knowledge snapshot. Earlier preflight campaigns with invalid CPU binding or
missing-DEF evaluator behavior were archived and excluded.

## Repeated Baseline Classification

Both baseline repeats produced the same terminal signature for every row below.
`Eligible` means that the fixed SDC covers internal sequential logic and the
failure is therefore a valid 100 MHz physical-design challenge.

| RTL | Stable baseline result | Constraint coverage | Classification |
|---|---|---|---|
| `eth_mac_1g` | Place abort `PPL-0024`: 1,251 IO pins vs 750 positions; required perimeter 1,701.36 um | 78 unclocked register pins; 127 unconstrained endpoints | Ineligible |
| `forencich_axi_interconnect` | Place abort `PPL-0024`: 1,914 IO pins vs 1,112 positions; required perimeter 2,603.04 um | Complete | Eligible |
| `forencich_axil_interconnect` | Place abort `PPL-0024`: 1,218 IO pins vs 836 positions; required perimeter 1,656.48 um | Complete | Eligible |
| `async-fifo-dpretet` | ORFS complete; route 0, DRC 4, LVS/timing/antenna/RCX clean | 21 unclocked register pins; 28 unconstrained endpoints | Ineligible |
| `cores_usb_device_src_v_usbf_device_core` | ORFS complete; route 0, DRC 32; LVS/timing/antenna/RCX clean | Complete | Eligible |
| `Hazard3_hdl_hazard3_frontend` | ORFS complete after long detailed routing; route 44, DRC 68; LVS/timing/antenna/RCX clean | Complete | Eligible |
| `serv` | ORFS complete; route 1, DRC 2; LVS/timing/antenna/RCX clean | Complete | Eligible |

The two incomplete-coverage rows are benchmark-input defects, not Agent repair
failures. A physical result for them would not establish that the registered
10 ns task was checked over all relevant sequential logic. They must be either
given a complete multi-clock/derived-clock constraint specification or excluded
before a repair comparison.

## P0 Finding: Full R2G A/B Planning Escapes the Current Campaign

The normal Full R2G controller was started only for the two eligible
interconnect tasks. It used a fixture-private copy of the canonical knowledge
database and was given no witness, answer boundary, manual RTL edit, or special
Recipe. After `forencich_axil_interconnect` completed its local observation and
repair flow, its normal `engineer_loop run` entered A/B planning and launched:

```
/home/yangao/r2g_ab_pin_side_2026_08_12_run05/subjects/
gcd_abA_density_550B43_0
```

That GCD project is a historical A/B subject, not a member of the seven-RTL
campaign. The attempt was stopped immediately after it was observed. The AXI
controller was also stopped before it could enter the same global drain. No
external OpenROAD, KLayout, Magic, or Netgen processes remained after the
containment action.

The code path is structural: `engineer_loop.run()` calls
`plan_arms_for_candidates()` after each normal design; that function iterates
over every pending `recipe_status` row in the knowledge database.
`ab_runner.plan_trial()` can then resolve subjects from historical fix evidence
without an allowlist rooted in the current ledger/campaign. A fixture-private
database prevents database writes to the canonical store, but it does not stop
the planner from launching an absolute historical project path.

This is a P0 execution-isolation defect. It invalidates an unattended
benchmark run because one task can consume unrelated projects, mutate their
backend artifacts, and use non-cohort evidence in an A/B decision. The defect
is in the Agent's lifecycle execution semantics, not in the independent
evaluator.

## What Is and Is Not Measured

| Quantity | Result |
|---|---|
| Original prior-task audit set | 7 RTLs |
| Constraint-eligible, repeated physical challenges | 5 RTLs |
| Constraint-ineligible rows | 2 RTLs |
| Legal completed Full R2G repairs | 0 |
| Legal Full R2G repair rate | Not estimable |
| Claim of at least 6/7 repairs or 80% repair rate | Unsupported |

For reference only, treating the missing legal numerator as a literal `0/7`
would give a 95% Wilson interval of `[0.0%, 35.4%]`; for the five eligible
tasks it would be `[0.0%, 43.4%]`. Neither is reported as an R2G repair rate,
because the denominator contains ineligible tasks and the eligible tasks were
not allowed to complete normal Full R2G execution safely.

## Non-Agent Limits Observed

* Hazard3 is a real fixed-task detailed-routing difficulty: both complete
  baselines ended with exactly 44 route and 68 DRC violations, while timing,
  LVS, antenna, RCX, and constraints were clean. It is not an environment
  failure.
* USB-device and `serv` are valid strict-signoff failures with small, stable
  residuals. They are useful future repair tasks after isolation is fixed.
* The PPL-0024 interconnect failures are valid footprint/pin-capacity tasks,
  but their repair behavior must be evaluated only after the P0 containment
  gate prevents the global A/B queue from reaching historical subjects.

## Reproduction Paths

* Per-repeat independent evaluations:
  `/home/yangao/r2g_exp2_current7_revalidation_2026_08_24/methods/baseline-r{1,2}/<fixture>/validations/baseline_final.json`
* Full R2G containment evidence:
  `/home/yangao/r2g_exp2_current7_revalidation_2026_08_24/methods/full-r2g/forencich_axil_interconnect/logs/full_r2g_2.log`
* Current planner implementation:
  `r2g-skills/signoff-loop/scripts/loop/engineer_loop.py`,
  `plan_arms_for_candidates()` and `run()`.

