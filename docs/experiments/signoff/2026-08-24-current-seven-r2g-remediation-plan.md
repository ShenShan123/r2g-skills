# Remediation Plan: Safe Full-R2G Revalidation for the Seven-RTL Cohort

## Priority 0: Make A/B Execution Round-Scoped and Fail Closed

### Problem

`engineer_loop.run()` plans arms for all pending candidates in the knowledge
snapshot. Candidate validation can therefore select a historical source project
outside the current ledger and campaign. The 2026-08-24 AXIL revalidation
attempt demonstrated this by launching an old GCD arm from
`/home/yangao/r2g_ab_pin_side_2026_08_12_run05`.

### Required change

Give every normal run, candidate, A/B trial, and subject an explicit
`round_id`/`campaign_id`. When a normal engineer loop is launched, construct an
immutable allowlist from its ledger's non-arm project roots and their source and
task manifests. `plan_arms_for_candidates()` and `ab_runner.plan_trial()` must
only consider:

1. candidates created by the current round, or candidates explicitly imported
   into the round by an operator;
2. subjects whose resolved project paths, platform, source closure digest, and
   protected-task digest match that round; and
3. arms whose destination directories are children of the round root.

Any missing round identity, nonexistent subject, path outside the allowlist,
or digest mismatch must return `blocked_cross_round_subject` without spawning
an ORFS process or mutating historical artifacts. Historical candidates remain
visible for ranking but cannot become executable work merely because a new
round begins.

### Regression tests

* Seed a database with a pending historical candidate whose only resolvable
  subjects live outside a temporary current round. Run a new ledger and assert
  no child process, arm directory, run row, or recipe lifecycle update is made
  for the historical project.
* Provide two allowlisted current subjects and assert a trial creates arms only
  under the current round and retains distinct owned run IDs.
* Verify that an operator-imported historical subject requires an explicit
  manifest/digest approval and is still copied or vendored into the new round,
  never run in place.
* Harden the experiment wrapper as a second line of defense: every guarded
  `run_orfs.sh` invocation must reject a project argument outside the fixture's
  registered project root.

## Priority 0: Qualify the Repair Benchmark Before Repair

The old seven-task list contains two rows whose SDC does not cover all internal
sequential logic. Add a mandatory pre-baseline classification step:

* `eligible`: source closure and provenance are complete; clock/IO constraints
  cover internal sequential logic; two untouched baselines reproduce a
  non-clean physical signature.
* `ineligible_constraint`: missing clocks, unconstrained endpoints, or missing
  derived/multiple-clock declarations. This is not a repair failure.
* `ineligible_input` or `environment`: malformed source closure, toolchain
  failure, or incomplete platform capability. This is also not a repair
  failure.

For `eth_mac_1g` and `async-fifo-dpretet`, obtain a complete declarative clock
specification and rerun qualification from new source materializations. Do not
invent SDC constraints from post-hoc repair logs.

## Priority 1: Rerun the Five Eligible Tasks Only After P0 Is Fixed

Create a fresh campaign with a new immutable snapshot of the repaired code and
knowledge. Preserve the same Sky130HD, 100 MHz, protected SDC, platform,
toolchain, source revisions, and per-task budget. For each of AXI,
AXI-Lite, USB device, Hazard3, and `serv`:

1. run two untouched baseline repeats;
2. run Full R2G once from a fresh fixture-private round;
3. independently remeasure ORFS completion, route, DRC, LVS, antenna, timing,
   RCX, constraint coverage, provenance binding, protected inputs, and global
   non-regression;
4. repeat a strict-clean repair result from a fresh round; use a third run only
   when the two outcomes disagree; and
5. report actions, Recipe lifecycle source, ORFS-flow count, wall time, and
   zero external LLM-token fact per task.

Only then report `k/n` and a Wilson interval. The original 7-task statement is
allowed only if all seven become constraint-eligible; otherwise report the
qualified denominator and a separate input-qualification funnel.

## Priority 1: Preserve Strict Gates While Addressing Physical Failures

No remediation may relax the 10 ns target, replace the platform, disable a
check, or count a partial flow as a win.

* The two PPL-0024 interconnects are legitimate pin-capacity tasks. Existing
  footprint actions may be selected only if their final strict signoff and
  protected-constraint checks pass; no task-specific perimeter witness should
  be injected.
* USB device and `serv` are useful small-residual DRC/route tasks for validating
  ranking and non-regression after containment is fixed.
* Hazard3 should retain the 7,200 s bound. If a bounded action cannot change
  the stable 44/68 residual vector, record `nonconvergent_within_budget`; do
  not hide it with a relaxed DRC deck or a larger unregistered footprint.

## Acceptance Criteria

The revalidation can resume only when all are true:

| Gate | Required evidence |
|---|---|
| Round isolation | A negative regression test proves no historical path can be launched from a new ledger. |
| Benchmark qualification | Each counted RTL has complete source/provenance and sequential constraint coverage in two baselines. |
| Full-R2G legality | Each submission uses only round-owned projects, actions, knowledge snapshot, and budget. |
| Success gate | Independent strict evaluator confirms ORFS, DRC, LVS, route, antenna, timing, RCX, binding, and global non-regression. |
| Statistical claim | The report uses only legal, repeatable repairs and states its exact denominator and Wilson interval. |

This plan intentionally repairs execution isolation before adding more Recipes.
Adding strategies while the normal A/B loop can execute unrelated historical
subjects would create stronger-looking but untrustworthy evidence.

