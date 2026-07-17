# 2026-07-16 R2G Agent-Logic Issue Report

Date: 2026-07-16  
Repository: `/home/yangao/r2g-skills`  
Remote: `https://github.com/ShenShan123/r2g-skills.git`  
Commit tested: `cb50537e70f328b51ba087d84a3f5b25068ab5bd`  
Remote synchronization at final audit: `HEAD == origin/main`

Artifacts:

- Audit script: `/home/yangao/r2g-skills/tools/audit_agent_logic_2026_07_16.py`
- Raw results: `/home/yangao/r2g-skills/tools/audit_agent_logic_2026_07_16_results.json`
- Preserved pre-pull work: `stash@{0}` (`pre-cb50537-agent-audit-2026-07-16`)

## Scope and Method

This audit targets Agent decision logic only: A/B evidence ownership, causal
isolation, lifecycle transitions, intervention execution, negative-evidence
scope, and dataset provenance. It does not treat local installation or EDA-tool
availability as an Agent defect.

All probes use temporary SQLite databases and temporary project directories.
They do not run ORFS and do not read or modify the shipped `knowledge.sqlite`.
Only reproduced defects are included below. The audit harness executed 11 probes;
all 11 reproduced their condition in three consecutive complete runs with zero
harness errors. The 11 probe failures consolidate into nine distinct issues.

Before the adversarial audit, the latest tree passed its upstream regression
baseline:

- `signoff-loop`: 877 passed, 1 skipped
- `def-graph`: 363 passed, 61 skipped
- `rtl-acquire`: 63 passed

These results show that the findings below are uncovered behavioral gaps rather
than ordinary regressions already detected by the current test suites.

## Executive Summary

### P0: Can Cause Incorrect Learning, Incorrect Promotion, or Untrusted Data

1. Existing but foreign run IDs can certify an A/B win.
2. A tied A/B corpus has an order-dependent lifecycle result.
3. The causal-isolation guard checks only a small knob whitelist.
4. Target-symptom success can hide new LVS and timing regressions.
5. Dataset signoff provenance is not cryptographically bound and is later overwritten.
6. Lifecycle changes between selection/planning and apply/judge are not revalidated.
7. A/B arm identity collides when two symptoms share the same strategy and subject.

### P1: Can Cause Learning Bias or Long-Term Instability

8. Regression auto-demotion crosses platform and design-class boundaries.
9. Recipe application can report success without producing any intervention.

## Issue 1: P0 - Existing but Foreign Runs Can Certify an A/B Win

### Probe

The probe created a candidate recipe for:

```text
(audit-symptom, crypto/small, nangate45, density_relief)
```

It then inserted two real `runs` rows that belonged to unrelated projects,
different designs, and different platforms (`sky130hd` and `asap7`). Those two
run IDs were submitted as the A and B evidence for a decisive win.

### Observed Result

```json
{
  "provenance_complete": true,
  "recipe_status": "promoted",
  "recipe_provenance": "ab_corpus:1w0l"
}
```

The new existence check rejected fabricated IDs, but accepted any IDs that were
present in `runs`.

### Root Cause

`ab_runner._runs_exist()` (`knowledge/ab_runner.py:72`) checks only whether each
ID resolves to a row. `record_trial()` (`knowledge/ab_runner.py:451`) therefore
marks the pair complete without validating that:

- A belongs to the current arm-A project;
- B belongs to the current arm-B project;
- both belong to the same planned trial and base subject;
- design, platform, flow variant, and recipe key match the trial.

The stored schema also has no authoritative planned-arm identity against which
`record_trial()` can perform those joins.

### Impact

A decisive result from unrelated runs can promote a recipe into live use. The
existence check improves traceability, but it does not establish A/B causality.

### Recommendation

Create a durable `ab_trial_plan` or equivalent record before arm execution. Give
each arm a unique ID and persist its expected project path, subject ID, design,
platform, recipe key, recipe version, and role. At judgment, resolve each run ID
through that plan and require an exact match. `provenance_complete` should be a
database-derived ownership predicate, not merely an existence predicate.

Add regression tests with real foreign runs, cross-platform runs, swapped A/B
roles, and runs from another trial.

## Issue 2: P0 - Tied Evidence Produces an Order-Dependent Lifecycle State

### Probe

Two independent subjects produced the same final corpus: one win and one loss.
The only difference between two databases was insertion order.

### Observed Result

```json
{
  "win_then_loss": {
    "status": "promoted",
    "provenance": "ab_corpus:1w0l"
  },
  "loss_then_win": {
    "status": "shadow",
    "provenance": "ab_corpus:0w1l"
  }
}
```

Both databases contained the same net corpus (`1w1l`), but reached opposite
lifecycle states.

### Root Cause

`record_trial()` invokes `judge_recipe()` after every inserted row.
`judge_recipe()` (`knowledge/ab_runner.py:513`) promotes when wins exceed losses,
demotes when losses exceed wins, and returns `None` on a tie. Returning `None`
leaves the previous state untouched.

Consequently, the first decisive row changes the lifecycle and the second row,
which creates a tie, does not neutralize that transition. The state is therefore
a function of event order rather than a pure function of the full corpus.

### Impact

A recipe can remain promoted even though its complete independent evidence does
not support promotion. Incremental judging makes this particularly risky because
one subject can become live before the rest of the planned cohort completes.

### Recommendation

Define a deterministic state for every aggregate evidence state, including ties.
For example, a candidate with tied evidence should remain `candidate` or move to
an explicit `inconclusive` state; it must not inherit a transient promotion.

Prefer cohort-level lifecycle transition after all planned subjects complete.
Alternatively, store evidence state and derive lifecycle from it atomically. Add
permutation tests proving that every ordering of the same trial set yields the
same final status.

## Issue 3: P0 - The Causal-Isolation Guard Misses Extra Configuration Changes

### Probe

The A and B arms kept clock, die area, core area, and SDC period identical. Arm B
also changed two unrelated knobs:

```text
PLACE_DENSITY_LB_ADDON: 0.20 -> 0.01
ABC_AREA: absent -> 0
```

### Observed Result

```json
{
  "guarded_knobs": ["CLOCK_PERIOD", "DIE_AREA", "CORE_AREA"],
  "veto": null
}
```

The trial remained eligible for judgment even though B differed by more than the
target recipe effect.

### Root Cause

`engineer_loop._arm_spec_mismatch()` (`scripts/loop/engineer_loop.py:1787`)
compares only `CLOCK_PERIOD`, `DIE_AREA`, `CORE_AREA`, and the parsed SDC period.
It does not compare the complete normalized config/SDC/environment delta against
the expected effect of the recipe under test.

The current check protects several important task specifications, but it is not
a general causal-isolation check.

### Impact

An unrelated optimization, disabled check, or hidden environment change can be
credited to the target recipe. This can produce a statistically valid but
causally invalid promotion.

### Recommendation

At planning time, materialize a normalized expected-effect manifest containing:

- config edits;
- SDC edits;
- environment edits;
- permitted generated files;
- immutable task specification and check set.

Before judgment, diff both arms against the same immutable baseline. Require:

```text
delta(A, baseline) = allowed control delta
delta(B, baseline) = delta(A, baseline) + expected recipe effect
```

Any unexplained delta should invalidate the trial. Compare parsed structures,
not raw line order or formatting.

## Issue 4: P0 - Target-Symptom Success Hides Cross-Check Regressions

### Probe

Arm B cleared the target DRC class but simultaneously changed:

```text
LVS: clean -> fail (real_connectivity)
timing: clean -> severe
```

### Observed Result

```json
{
  "arm_a_target_success": false,
  "arm_b_target_success": true,
  "judge_verdict": "win",
  "regression_veto": null
}
```

### Root Cause

`_arm_metric()` (`scripts/loop/engineer_loop.py:1896`) intentionally judges a
DRC recipe on whether its target class cleared. The subsequent veto,
`_ab_new_drc_regression()` (`scripts/loop/engineer_loop.py:1802`), examines only
new DRC categories. It does not compare LVS, timing, route, antenna, ORFS
completion, or check availability.

The veto also ignores a new DRC class unless its count exceeds the entire arm-A
residual count. That materiality rule may be useful for benign newly visible
violations, but it cannot substitute for a global severity check.

### Impact

A recipe can be promoted for fixing one symptom while making the design unusable
under another signoff check.

### Recommendation

Separate target efficacy from global acceptability:

1. Confirm that the target symptom improved or cleared.
2. Compare a global outcome vector covering ORFS completion, route, antenna, DRC
   classes and severities, LVS, timing, and check completeness.
3. Veto the win if B introduces any higher-severity failure or disables a check.

Use an explicit severity partial order rather than adding unrelated metrics into
one scalar score. Store the before/after global vectors in `metrics_json` for
replay and review.

## Issue 5: P0 - DEF and Signoff Provenance Remains Weak and Is Overwritten

### Probes

Two related probes were run.

First, clean DRC/LVS/route/PPA reports were copied from `RUN_R1` into the project
report directory, while the selected DEF came from `RUN_R2`. Design and platform
were unchanged.

Second, the DEF-aware gate was run successfully, then replayed exactly like the
feature/label stage runners, which omit the `--def` argument and write the same
`reports/signoff_gate.json` path.

### Observed Result

The mixed bundle passed:

```json
{
  "status": "pass",
  "blockers": [],
  "binding_status": "bound"
}
```

The binding check considered the DEF bound because it was located under the
supplied `RUN_R2` directory. It did not inspect the origin of the project-level
reports.

The second probe showed:

```json
{
  "initial_def_fingerprint_present": true,
  "fingerprint_after_extractor_style_gate": null,
  "run_features_omits_def": true,
  "run_labels_omits_def": true
}
```

### Root Cause

`signoff_gate._check_binding()` (`def-graph/scripts/flow/signoff_gate.py:262`)
checks only whether the selected DEF path is contained under `run_dir`. DRC and
LVS reports live under the project-level `reports/` directory and carry no
required DEF digest or run identity.

The recorded DEF fingerprint is only path, file size, and integer mtime
(`signoff_gate.py:247`), not a content digest.

`run_graphs.sh` initially calls the gate with `--def`, but `run_features.sh` and
`run_labels.sh` call the same gate without `--def` and overwrite
`reports/signoff_gate.json`. `build_graphs.py` later embeds this last verdict,
which can lose the selected DEF identity.

### Impact

A clean report bundle from another run can certify the wrong layout, and the
final graph manifest can lose even the weak DEF fingerprint established at the
start of graph generation. This undermines dataset trust and reproducibility.

### Recommendation

Create an immutable, run-scoped artifact manifest after signoff. It should bind
with SHA-256 digests at least:

- DEF and ODB;
- DRC and LVS reports plus their raw tool outputs;
- route/antenna reports;
- SPEF and timing/PPA reports;
- design, platform, flow variant, run ID, tool versions, and check set.

Every signoff report should reference the certified DEF/ODB digest. The graph
stage should consume one manifest and rehash the selected artifacts immediately
before conversion. Feature and label stages should either pass the same DEF to
the gate or stop rewriting the authoritative verdict. Write stage-local verdicts
to separate paths if independent checks are still desired.

## Issue 6: P0 - Lifecycle Changes Are Not Revalidated at Apply or Judge Time

### Probes

Two time-of-check/time-of-use paths were tested.

In the live path, `--next` selected a promoted `density_relief`. The recipe was
then demoted before `--apply` executed.

In the A/B path, arm entries were planned at generation 7. The recipe was demoted
before `judge_finished_trials()` ran.

### Observed Result

Live apply still changed the config after demotion:

```json
{
  "selection": "density_relief",
  "status_before_apply": "shadow",
  "apply_rc": 0,
  "config_changed": true
}
```

The stale A/B trial also ran to a decisive win and re-promoted the recipe:

```json
{
  "planned_generation": 7,
  "generation_after_demote": 7,
  "status_before_judge": "shadow",
  "recorded_verdict": "win",
  "status_after_judge": "promoted"
}
```

### Root Cause

The `--next` path uses `_live_auto_strategy()` and respects lifecycle gates, but
the explicit `--apply` branch (`diagnose_signoff_fix.py:999`) looks up the strategy
by ID and applies it directly. `fix_signoff.sh` performs selection and apply as
two separate process invocations (`scripts/flow/fix_signoff.sh:361` and `:376`),
leaving a lifecycle race window.

For A/B, the freshness guard (`engineer_loop.py:2027`) compares only the stored
generation. `recipe_lifecycle._set()` (`knowledge/recipe_lifecycle.py:127`) changes
status and provenance without changing generation. Therefore a demotion does not
invalidate an already planned arm.

### Impact

A strategy can execute after the safety system has withdrawn it. A stale trial
can also undo a later demotion and restore live promotion using evidence planned
under an obsolete lifecycle state.

### Recommendation

Return a signed or database-backed decision token from selection containing the
full recipe key, lifecycle status, generation, effect hash, and decision nonce.
Apply must atomically compare-and-set that token against the current lifecycle
row before writing any project file.

Likewise, A/B judgment should require the recipe to remain in the expected
validation state and should compare status version plus effect hash, not only a
learner generation. Any lifecycle or recipe-content change should cancel the old
trial and schedule a fresh one.

## Issue 7: P0 - A/B Arm Identity Collides Across Symptoms

### Probe

Two candidate keys used the same subject, platform, and `density_relief`
strategy, but different symptom IDs. With one repeat, each candidate should have
created an independent A/B pair.

### Observed Result

```json
{
  "candidate_count": 2,
  "planner_appended_count": 4,
  "surviving_ledger_arm_count": 2,
  "surviving_symptom_keys": ["symptom-two"]
}
```

The planner reported four appends, but the ledger retained only one A/B pair and
the second candidate overwrote the first pair's `ab_key`.

### Root Cause

Arm directories and design keys are named from only:

```text
subject + arm role + strategy[:8] + repeat
```

See `engineer_loop.py:1539` and `:1555`. Symptom ID, design class, platform,
recipe version, and trial ID are absent. `Ledger` stores one merged entry per
`design` (`engineer_loop.py:84` and `:105`), so the second plan updates the first.

The judge also groups entries by `(base, strategy)` rather than a unique planned
trial (`engineer_loop.py:1985`), creating a second collision surface even if
directory names are later fixed.

### Impact

Evidence can be attributed to the wrong symptom, one candidate can silently lose
its experiment, and mixed arm samples can drive an incorrect promotion.

### Recommendation

Allocate a durable `trial_uuid` before materializing arms. Include it in the arm
directory, ledger key, run metadata, and judge grouping. Group by the full trial
identity, not by parsed design names. The full recipe key and version should be
immutable fields of the trial plan.

Also remove `strategy[:8]` as an identity component; it is suitable for display
only and can collide across strategy names.

## Issue 8: P1 - Regression Auto-Demotion Crosses Scope Boundaries

### Probe

A `nangate45/crypto` recipe was promoted. Two later regressions were inserted for
the same symptom ID and strategy, but from `asap7/cpu` runs.

### Observed Result

```json
{
  "auto_demote_returned": true,
  "nangate45_recipe_status": "shadow",
  "provenance": "repeated_regression"
}
```

### Root Cause

`ab_runner.auto_demote_on_regression()` (`knowledge/ab_runner.py:568`) queries
`fix_events` by only `symptom_id` and `strategy`, then applies the result to the
specific `(symptom, design_class, platform, strategy)` lifecycle key supplied by
the caller. It does not filter the evidence by platform, design class/family,
check type, effect fingerprint, or live-vs-backfill provenance.

### Impact

Failures in a transfer domain can disable a recipe that remains reliable in its
validated exact domain. This can cause avoidable strategy loss and unstable
cross-platform behavior.

### Recommendation

Scope regression evidence at least by symptom, check type, platform, recipe
effect fingerprint, and evidence provenance. Use exact-domain evidence for
automatic demotion. Treat cross-platform evidence as a separately weighted
transfer signal unless the recipe was explicitly promoted as platform-agnostic.

Store the recipe key or lifecycle row ID directly on each live application event
so demotion does not depend on reconstructing scope from loosely related fields.

## Issue 9: P1 - Apply Can Succeed Without Producing Any Effect

### Probe

A severe timing report produced a valid `period_relax` strategy, but the project
had no `constraints/constraint.sdc`. The strategy was applied through the normal
CLI.

### Observed Result

```json
{
  "apply_rc": 0,
  "reported_applied": "period_relax",
  "reported_sdc_edit": {"CLOCK_PERIOD": "11.55"},
  "constraint_sdc_exists": false,
  "project_files_changed": false
}
```

### Root Cause

The `--apply` branch reports success after identifying a strategy. SDC edits are
performed only inside `if sdc_path.exists()` (`diagnose_signoff_fix.py:1015`), but
the absence of the target file is not returned as an error or no-op. No post-apply
effect check verifies that any declared edit actually landed.

### Impact

The loop can rerun an expensive backend stage after a zero-effect intervention,
then record the resulting failure against a recipe that was never actually
applied. This wastes compute and contaminates learning.

### Recommendation

Implement recipe application as a validated transaction:

1. Resolve and validate every target before writing.
2. Compute the expected normalized effect.
3. Apply all edits atomically or roll back all edits.
4. Re-read the files/environment and verify the actual effect fingerprint.
5. Return structured `applied`, `no_effect`, `precondition_failed`, or
   `partial_apply_rolled_back` status.

Only a verified `applied` result should trigger a stage rerun or become positive
or negative recipe evidence.

## Recommended Fix Order

1. Fix A/B identity and ownership together: durable trial plans, unique arm IDs,
   and run-to-arm joins (Issues 1 and 7).
2. Make lifecycle transitions deterministic and token-validated at both apply and
   judge boundaries (Issues 2 and 6).
3. Replace partial A/B checks with expected-effect and global-regression guards
   (Issues 3 and 4).
4. Introduce a run-scoped cryptographic artifact manifest and preserve it through
   graph generation (Issue 5).
5. Scope negative evidence correctly and require verified non-zero interventions
   (Issues 8 and 9).

The first two groups are the highest priority because they determine whether an
A/B result belongs to the claimed experiment and whether that result is allowed
to change live Agent behavior.
