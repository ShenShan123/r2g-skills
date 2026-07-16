# 2026-07-15 R2G Agent Issue Report

Date: 2026-07-15  
Repository: `/home/yangao/r2g-skills`  
Remote: `https://github.com/ShenShan123/r2g-skills.git`  
Commit tested after pull: `8fbba2a65f63bc786dd3d9a4de87be3cb47cda70`  
Pulled update: `feat(skill): RTL2Graph_v3 alignment - raw-label twins + num_drivers no-fill + LEF pin geometry (def-graph)`

Artifacts:

- Audit script: `/home/yangao/r2g-skills/tools/audit_recipe_lifecycle.py`
- Raw JSON result: `/home/yangao/r2g-skills/tools/audit_recipe_lifecycle_results.json`

Scope note: this report includes only probes that still reproduced a problem on the latest pulled commit. Passing probes are intentionally omitted from the issue list. In this batch, P0-5, P0-7, P0-8, P1-3, P1-4, P1-5, P1-7, P1-8, and P1-9 passed their expected fail-closed or safety behavior. P1-6 and P1-10 reproduced new lifecycle/ranking issues.

## Issue 1: P0-4 - An A/B Win with Identical Arm Run IDs Still Promotes

### Probe

The probe created a decisive A/B trial with:

- `verdict = "win"`
- `arm_a_run_id = "same_run_001"`
- `arm_b_run_id = "same_run_001"`

This is not traceable A/B evidence, because both arms point to the same run.

### Observed Result

The trial was correctly stamped as incomplete provenance:

```json
{
  "arm_a_run_id": "same_run_001",
  "arm_b_run_id": "same_run_001",
  "metrics": {
    "judge_version": 2,
    "provenance_complete": false
  },
  "verdict": "win"
}
```

However, the recipe was still promoted:

```json
{
  "observed_status": {
    "status": "promoted",
    "provenance": "ab_corpus:1w0l"
  }
}
```

### Likely Root Cause

`ab_runner.record_trial()` detects missing or identical arm run IDs and writes `provenance_complete=false` into `metrics_json` (`ab_runner.py:370-395`). It also prints a warning for decisive but unverifiable trials.

The problem is that `ab_runner.judge_recipe()` later reads only `verdict` from `ab_trials` and counts all `win` and `loss` rows (`ab_runner.py:430-441`). It does not filter on `metrics_json.provenance_complete`.

Therefore, the system records the provenance problem but does not enforce it at the lifecycle transition boundary.

### Impact

An unverifiable A/B result can promote a recipe into live use. This weakens the core causal claim of the A/B lifecycle: promoted recipes should be backed by two distinct, traceable arm runs.

### Recommendation

Promotion and demotion should count only complete-provenance decisive trials.

Possible fixes:

1. In `judge_recipe()`, select `verdict, metrics_json` and ignore decisive rows where `provenance_complete` is false.
2. Alternatively, in `record_trial()`, downgrade decisive incomplete-provenance verdicts to a non-promoting state such as `inconclusive` or `provenance_failed`.
3. Add a regression test where `arm_a_run_id == arm_b_run_id` and assert that the recipe remains `candidate` or is parked/escalated, never promoted.

## Issue 2: P0-6 - Arbitrary No-op Candidate Recipes Can Still Enter A/B

### Probe

The probe created a candidate recipe whose strategy exists in the lifecycle but has no effective application path. After A/B arm creation, applying the strategy to arm B produced no changes to:

- `config.mk`
- SDC
- environment flags
- project-local artifacts

### Observed Result

The known built-in no-op guard worked for `lvs_resolve_unknown`, but an arbitrary no-op strategy still entered A/B:

```json
{
  "enqueue_returned": true,
  "planned_arm_entries": 2,
  "arm_b_apply": {
    "config_changed": false
  },
  "status_after_plan": {
    "status": "candidate",
    "provenance": "probe"
  }
}
```

The runner printed:

```text
ERROR: strategy 'noop_probe_strategy' not in current plan
```

This means the A and B arms can be scheduled even when arm B has no real intervention.

### Likely Root Cause

The current non-divergent guard is a hardcoded strategy denylist:

```python
NONDIVERGENT_STRATEGIES = frozenset({"lvs_resolve_unknown"})
```

This is defined in `recipe_lifecycle.py:22-32` and is used by the planner-side coverage guard in `engineer_loop.py:1245-1248`.

That catches known no-op strategies, but it does not prove that a newly learned or agent-authored strategy actually changes anything. If a strategy is unknown to the apply layer, it can still be enqueued and planned, then silently produce a no-change B arm.

### Impact

The system may spend A/B compute on experiments that cannot produce a meaningful difference. Worse, if some unrelated noise later creates an apparent win, the recipe evidence is not causally tied to the candidate intervention.

### Recommendation

Add a semantic no-op gate, not only a static denylist.

Possible fixes:

1. Add a dry-run or preview API for each strategy, returning a structured effect summary such as `config_edits`, `sdc_edits`, `env_flags`, and `artifact_writes`.
2. During candidate enqueue or A/B planning, apply the candidate to a temporary copy and diff the expected mutable surfaces. If there is no change, mark the recipe `parked` with provenance `nondivergent_no_real_edit`.
3. Treat "strategy not in current plan" as a structured non-divergent or invalid-recipe outcome rather than only a stderr message.
4. Add a regression test for an unknown/no-op strategy and assert that no A/B arms are scheduled.

## Issue 3: P0-9 - Direct rtl-acquire Graph Conversion Has an Unstructured Toolchain Failure Path

### Probe

The probe checked toolchain/environment failure handling for graph Python:

- `R2G_GRAPH_PYTHON` unset
- `R2G_GRAPH_PYTHON` pointing to a missing executable in `def-graph`
- `R2G_GRAPH_PYTHON` pointing to a missing executable in direct `rtl-acquire graph_convert()`

### Observed Result

Two paths behaved safely:

```json
{
  "rtl_acquire_unset_graph_python": {
    "state": "skipped"
  },
  "def_graph_invalid_graph_python": {
    "manifest": {
      "status": "skipped"
    }
  }
}
```

However, direct `rtl-acquire graph_convert()` still raised an unstructured exception when `R2G_GRAPH_PYTHON` pointed to a missing path:

```json
{
  "rtl_acquire_invalid_graph_python": {
    "exception": {
      "type": "FileNotFoundError"
    },
    "result": null
  }
}
```

### Likely Root Cause

`expand_candidates.py::graph_convert()` handles the unset case explicitly (`expand_candidates.py:729-735`). But if `R2G_GRAPH_PYTHON` is set to an invalid executable, the code calls:

```python
result = run(
    [gpython, str(netlist_graph_script()), str(netlist), str(out_pt), design],
    capture=True,
    extra_env=lib_env,
)
```

at `expand_candidates.py:737-741`.

There is no local `OSError` or `FileNotFoundError` guard around this subprocess call, so the failure escapes as an exception instead of becoming a structured graph-stage/toolchain result.

### Impact

This is narrower than a full toolchain-classification failure: unset graph Python and the `def-graph` shell path are already handled safely. The remaining risk is that a bad `R2G_GRAPH_PYTHON` path in the direct `rtl-acquire` path can abort the expansion path without a clean status such as `graph_skipped`, `graph_failed`, or `toolchain_graph_python_missing`.

If this propagates into ingestion or candidate accounting without classification, an environment error may be harder to distinguish from an RTL/design failure.

### Recommendation

Make invalid graph Python paths a structured toolchain result.

Possible fixes:

1. Validate `R2G_GRAPH_PYTHON` before use: path exists, is executable, and can import the required graph dependencies.
2. Wrap the graph conversion and graph stats subprocess calls in `except (OSError, FileNotFoundError)` and return a structured state, for example:

```text
state = "skipped"
reason = "toolchain_graph_python_missing"
```

or:

```text
state = "failed"
reason = "toolchain_graph_python_invalid"
```

3. Ensure the caller writes this status into candidate metadata/index output and does not route it into RTL/design repair learning.
4. Add a regression test where `R2G_GRAPH_PYTHON=/tmp/does_not_exist_python` and assert that expansion records a structured toolchain skip/failure instead of throwing `FileNotFoundError`.

## Issue 4: P1-6 - Cross-platform Pooled Recipe Can Be Treated as Current-platform Promoted

### Probe

The probe created a recipe that was promoted only for:

- symptom: `METAL3_ANTENNA`
- design class: `logic/small`
- learned platform: `sky130hd`
- strategy: `antenna_density_relief`

Then it diagnosed the same symptom on a different current platform:

- current platform: `asap7`
- no explicit `recipe_status` row for `asap7`

### Observed Result

The indexed recipe lookup relaxed to the pooled platform bucket:

```json
{
  "indexed_match_level": "pooled_platform",
  "learned_platform": "sky130hd",
  "current_platform": "asap7"
}
```

The lifecycle status for the current platform was treated as promoted even though no current-platform row existed:

```json
{
  "same_platform_lifecycle_status": "promoted",
  "filtered_strategies_for_current_platform": [
    "antenna_density_relief"
  ],
  "live_auto_strategy": "antenna_density_relief"
}
```

### Likely Root Cause

`diagnose_signoff_fix.py::load_indexed_recipe()` intentionally relaxes lookup from exact platform to pooled platform:

```text
recipes[sid][design_class][platform]
-> recipes[sid]["*"][platform]
-> recipes[sid]["*"]["*"]
```

That relaxation is useful for transfer, but `recipe_lifecycle.get_status()` still uses `absent row = promoted` for bootstrap compatibility. When the current platform has no lifecycle row, `filter_promoted()` keeps the pooled strategy because the missing row is interpreted as promoted.

So the system has two policies interacting badly:

1. pooled-platform recipe lookup can surface cross-platform evidence;
2. missing lifecycle row means promoted.

Together, a strategy learned on `sky130hd` can become live-auto-applicable on `asap7` without an explicit platform-transfer approval.

### Impact

This can over-trust a platform-sensitive layout recipe. Some strategies are physically meaningful across platforms, but others depend on technology LEF, antenna model, DRC deck, diode behavior, or routing resources. A pooled prior should be allowed to influence ranking only under a clear transfer policy, not silently become current-platform promoted evidence.

### Recommendation

Make pooled transfer explicit at the lifecycle boundary.

Possible fixes:

1. When `match_level == "pooled_platform"`, do not call `filter_promoted()` with the current platform and absent-row grandfathering as sufficient proof.
2. Add a separate lifecycle status such as `transfer_candidate`, `pooled_shadow`, or `pooled_promoted`.
3. Require either:
   - a current-platform promoted row, or
   - an explicit allowlisted transfer rule with enough cross-platform evidence.
4. Consider using pooled recipes only as a prior/tiebreaker unless `platform_count >= 2` and the strategy is marked `platform_agnostic`.
5. Add a regression test where a strategy promoted on `sky130hd` is diagnosed on `asap7`; it should not become live auto-apply unless transfer is explicitly enabled.

## Issue 5: P1-10 - Candidate Recipes Can Still Leak into Live Auto-apply Through Static/cold-start Ranking

### Probe

The probe constructed two DRC antenna strategies:

- `antenna_density_relief`: strong evidence
- `antenna_diode_iters`: weak evidence

It then tested three lifecycle states:

1. both strategies promoted;
2. high-evidence strategy shadow, low-evidence strategy promoted;
3. high-evidence strategy candidate, low-evidence strategy promoted.

### Observed Result

The promoted-ranking path behaved correctly:

```json
{
  "both_promoted": {
    "strategy_order": [
      "antenna_density_relief",
      "antenna_diode_iters"
    ],
    "live_auto_strategy": "antenna_density_relief"
  }
}
```

The shadow gate also behaved correctly:

```json
{
  "shadow_high": {
    "strategy_statuses": {
      "antenna_density_relief": "shadow"
    },
    "live_auto_strategy": "antenna_diode_iters"
  }
}
```

However, the candidate strategy leaked into live auto-apply:

```json
{
  "candidate_high": {
    "strategy_statuses": {
      "antenna_density_relief": "candidate"
    },
    "strategy_order": [
      "antenna_density_relief",
      "antenna_diode_iters"
    ],
    "live_auto_strategy": "antenna_density_relief"
  }
}
```

### Likely Root Cause

The indexed recipe filter correctly strips non-promoted strategies from the learned recipe entry. In this probe, after filtering, only `antenna_diode_iters` remained.

But `build_plan()` still emits the full static strategy catalog. Since the promoted low-evidence strategy had a poor learned score, the candidate high strategy re-entered through the static/cold-start path with a neutral score of `0.5`, ranking above the weak promoted strategy.

`diagnose_signoff_fix.py::_annotate_live_gates()` correctly annotates the candidate lifecycle status:

```json
{
  "antenna_density_relief": "candidate"
}
```

But `_live_auto_strategy()` currently skips only:

- `requires_ab_promotion`
- `lifecycle_status == "shadow"`
- `dead_here`

It does not skip `lifecycle_status == "candidate"`.

### Impact

A recipe that is still awaiting A/B validation can be applied in a blind live run if it is present in the static catalog and ranks above a weaker promoted strategy. This undermines the candidate/promoted separation: candidate means "needs validation", but the live runner can still execute it.

### Recommendation

Make live auto-apply require an explicitly safe lifecycle state.

Possible fixes:

1. In `_live_auto_strategy()`, skip every non-promoted lifecycle status, not only `shadow`.
2. Treat `candidate`, `shadow`, and possibly `parked` differently in reporting, but all should be blocked from blind live auto-apply unless the caller is an A/B arm using `--rank-first`.
3. Add a helper such as:

```python
def is_live_allowed(status: str) -> bool:
    return status in ("promoted", "grandfathered_static")
```

4. Consider annotating static catalog strategies with `lifecycle_status="unvalidated_static"` when a matching lifecycle row exists but is non-promoted.
5. Add a regression test where a candidate strategy has a better cold-start or learned rank than a promoted weak strategy; `--next` must select the promoted strategy or stop, never the candidate.

---

## Agent-Only Adversarial Audit Addendum (2026-07-16)

Tested commit: `c420ddba6f368dafe8b8017b00aec62224e1a412`

Artifacts:

- Reproducible probe harness: `/home/yangao/r2g-skills/tools/audit_agent_logic_2026_07_16.py`
- Raw machine-readable evidence: `/home/yangao/r2g-skills/tools/agent_logic_audit_2026-07-16.json`

### Scope and Method

This addendum covers only agent behavior: A/B causality, lifecycle transitions,
learning isolation, evidence validity, negative-memory scope, repair termination,
and dataset provenance. It deliberately excludes EDA installation, executable paths,
tool versions, server resources, and other runtime-environment failures.

Each probe ran against a temporary project tree and a fresh temporary SQLite store.
No probe wrote to the campaign knowledge store. The harness exercised the current
production functions directly rather than reimplementing their decisions. Only
reproduced problems are listed below; a synthetic observation was retained only when
it changed a lifecycle state, selected a live action, changed learned statistics, or
allowed a dataset gate to pass.

The 17 requested probes reproduced 17 policy or correctness gaps. Closely related
probes are grouped into 11 findings to avoid reporting the same root cause twice.

## Issue 6: P0-10 and P1-11 - A/B Evidence Is Not Bound to Real, Independent Arm Runs

### Probe

P0-10 recorded a decisive win using two different IDs, `fake-A` and `fake-B`, neither
of which existed in `runs`. P1-11 then recorded five wins with distinct run IDs but
the same subject path and the same subject fingerprint.

### Observed Result

P0-10 promoted the recipe even though no matching run existed:

```json
{
  "recipe_status": "promoted",
  "provenance_complete": true,
  "matching_run_rows": 0
}
```

P1-11 counted one repeatedly reused subject as five independent wins:

```json
{
  "trial_count": 5,
  "independent_subject_count": 1,
  "recipe_status": "promoted",
  "lifecycle_evidence": "ab_corpus:5w0l"
}
```

### Root Cause

`ab_runner.record_trial()` defines complete provenance as two non-empty, unequal
strings (`ab_runner.py:380-383`). It does not verify that the IDs exist, refer to the
current A and B project paths, share the same subject and platform, or belong to the
current trial. `judge_recipe()` then counts every verifiable-looking win equally
(`ab_runner.py:431-447`) and has no independent-subject key.

### Impact

A recipe can be promoted by foreign, fabricated, or pseudo-replicated evidence. The
stored A/B result is syntactically complete but does not establish a causal experiment.

### Recommendation

1. Add a stable `trial_uuid` and an `ab_trial_arms` record containing arm, run ID,
   resolved project path, subject fingerprint, platform, and configuration hash.
2. Before recording a decisive result, require both run IDs to exist and match the
   planned ledger entries for that trial.
3. Base promotion thresholds on independent subjects or configuration families, not
   raw trial-row count alone.
4. Add database constraints or an explicit validator so a trial cannot self-certify
   its own provenance through arbitrary strings in `metrics_json`.

## Issue 7: P0-11 and P0-12 - The A/B Judge Does Not Enforce Causal Isolation or Specification Equality

### Probe

P0-11 gave arm B the target utilization edit plus an unrelated clock-period edit.
P0-12 made both arms signoff-clean, but made B much faster by relaxing the clock period
and enlarging the die.

### Observed Result

Both trials were accepted as wins and both recipes were promoted:

```json
{
  "P0-11": {
    "judge_verdict": "win",
    "recipe_status": "promoted",
    "recorded_unrelated_edit": {"CLOCK_PERIOD": "20"}
  },
  "P0-12": {
    "judge_verdict": "win",
    "judge_reason": "cost_tiebreak",
    "recipe_status": "promoted",
    "spec_was_relaxed": true
  }
}
```

### Root Cause

The repeated judge consumes success, elapsed time, and fix iterations
(`ab_runner.py:76-149`). The planner records only a pre-run baseline config hash
(`engineer_loop.py:1547-1552`). There is no post-run comparison proving that A and B
differ only by the tested recipe, and no immutable experiment-contract hash covering
clock targets, die/core constraints, enabled checks, RTL inputs, and flow policy.

### Impact

The agent can credit a recipe for improvement caused by an unrelated edit or can win
by making the design task easier. This is reward hacking, not evidence that the repair
improved the original design objective.

### Recommendation

1. Materialize an immutable experiment contract for both arms: RTL digest, platform,
   clock constraints, die/core target, signoff policy, and enabled checks.
2. Compute canonical pre-run and post-run diffs for `config.mk`, SDC, environment flags,
   and agent-written hooks.
3. Require `actual_B_delta == declared_recipe_effect` and `actual_A_delta == empty`.
4. Mark extra edits as `confounded` and specification changes as `invalid_trial`; neither
   state may influence promotion or demotion.

## Issue 8: P0-13 - Target-Symptom Success Can Hide a New Severe Regression

### Probe

Arm A retained one `M1_SPACING` violation. Arm B cleared `M1_SPACING` but introduced
eight `NEW_FATAL_SHORT` violations. Both arm results came from real temporary `runs`
and `run_violations` rows.

### Observed Result

```json
{
  "a_target_cleared": false,
  "b_target_cleared": true,
  "b_new_drc_class_count": 8,
  "judge_verdict": "win",
  "recipe_status": "promoted"
}
```

### Root Cause

`engineer_loop._drc_symptom_cleared()` asks only whether the selected target class has
reached zero (`engineer_loop.py:1637-1663`). `judge_finished_trials()` converts that
single Boolean into the arm success sample. No veto compares the full DRC-category
vector or checks for new LVS, timing, area, or power regressions.

### Impact

A locally effective but globally harmful recipe can enter live use. The current v2
targeted judge solves the old problem of unrelated pre-existing residuals, but it
over-corrects by ignoring newly caused residuals.

### Recommendation

Use a two-part verdict:

1. `target_effect`: whether the intended symptom improved or cleared;
2. `regression_guard`: whether B introduced any new class or materially worsened any
   protected signoff/PPA dimension relative to A.

A win requires both a positive target effect and a clean regression guard. Pre-existing
unrelated residuals may remain non-blocking, but newly introduced residuals must veto
promotion.

## Issue 9: P0-14 - A/B Evaluation Arms Feed Back into the Ordinary Learner

### Probe

The probe inserted one naive and one learned A/B arm run, then a successful fix event
under the B-arm project path, and rebuilt heuristics through the production learner.

### Observed Result

```json
{
  "source_run_count": 2,
  "arm_paths_present_in_learning_read": true,
  "ab_arm_recipe_attempts": 1,
  "ab_arm_recipe_successes": 1
}
```

### Root Cause

`learn_heuristics._fetch_learnable_rows()` excludes only `is_bench=1`
(`learn_heuristics.py:38-45`). It does not exclude `eval_arm` or `_abA_`/`_abB_`
projects. The trajectory rebuild similarly consumes every hot or archived fix event,
including events generated inside A/B arms.

### Impact

The same experiment can affect lifecycle promotion through `ab_trials` and then affect
ordinary recipe ranking through `runs` and `fix_events`. This creates circular evidence
and can make an agent-tested recipe appear independently corroborated when it is not.

### Recommendation

1. Add an explicit run role such as `production`, `ab_control`, `ab_treatment`,
   `benchmark`, or `backfill`.
2. Preserve A/B arms for audit and evaluation, but exclude them from ordinary learner
   counts by default.
3. If A/B outcomes are intentionally reused for learning, account for each trial once
   in a dedicated evidence channel and expose that provenance in ranking output.

## Issue 10: P0-15 and P0-16 - Lifecycle Safety Is Fail-Open and Trial Transitions Are Non-Atomic

### Probe

P0-15 made lifecycle/negative-gate state unavailable while leaving a static candidate
strategy selectable. P0-16 interrupted `record_trial()` immediately after the
`ab_trials` commit and retried the same operation after restart.

### Observed Result

```json
{
  "P0-15": {
    "lifecycle_annotation_present": false,
    "selected_strategy": "candidate_static"
  },
  "P0-16": {
    "rows_after_crash": 1,
    "status_after_crash": "candidate",
    "rows_after_retry": 2,
    "status_after_retry": "promoted"
  }
}
```

### Root Cause

`_annotate_live_gates()` explicitly returns an unannotated plan when its database read
fails (`diagnose_signoff_fix.py:748-810`). `_live_auto_strategy()` treats the absence of
a blocking annotation as permission to proceed (`diagnose_signoff_fix.py:492-531`). This
extends Issue 5: not only an explicit `candidate`, but also an unreadable lifecycle can
fall back to live static selection.

Separately, `record_trial()` commits the trial row before calling `judge_recipe()`
(`ab_runner.py:390-401`). The trial insert and recipe-status transition are not one
transaction, and there is no unique trial UUID preventing retry duplication.

### Impact

The agent can act with unknown safety state, and a process interruption can leave a
split-brain history or inflate promotion evidence on retry.

### Recommendation

1. Represent lifecycle availability explicitly and stop blind auto-apply whenever it is
   unknown; cold-start static behavior should be allowed only when the strategy is known
   to have no lifecycle row, not when the store is unreadable.
2. Execute trial insert, corpus judgment, and lifecycle update in one transaction.
3. Give every planned trial a deterministic UUID and make trial insertion idempotent.
4. Run automatic reconciliation at startup for committed-but-unjudged trials, with no
   second trial row created.

## Issue 11: P0-17 - The Dataset Gate Does Not Bind the DEF to Its Signoff Reports

### Probe

The probe combined an R2 `6_final.def` with clean DRC, LVS, route, timing, and ORFS
records labeled as R1 while keeping design and platform names unchanged.

### Observed Result

```json
{
  "def_declared_run": "R2",
  "reports_declared_run": "R1",
  "gate_status": "pass",
  "blockers": [],
  "gate_has_artifact_digest": false
}
```

### Root Cause

`signoff_gate.evaluate()` receives a project directory and a run directory and checks
their report contents independently (`signoff_gate.py:247-279`). It never receives the
selected DEF path and does not compare run IDs, timestamps, or content digests. The ORFS
check validates only completion inside the supplied run directory
(`signoff_gate.py:89-129`).

### Impact

A clean report bundle from one run can certify a layout artifact from another run. The
resulting graph manifest may claim signoff health that does not belong to the graph's
physical design.

### Recommendation

1. Generate a run manifest at flow completion containing run ID and SHA-256 digests for
   DEF/ODB/GDS, netlist, SPEF, and signoff reports.
2. Pass the exact selected DEF/ODB path to `signoff_gate.py` and verify it against that
   manifest.
3. Embed the verified run ID and artifact digests in `graph_manifest.json`.
4. Reject mixed or unbound bundles in enforce mode; warn mode may proceed only with an
   explicit `provenance_incomplete` marker.

## Issue 12: P1-12 and P1-14 - Negative Evidence Is Too Broad by Symptom and Too Narrow by Strategy ID

### Probe

P1-12 recorded two failures for a strategy on DRC symptom A, then diagnosed distinct
DRC symptom B on the same project. P1-14 created two strategy IDs with byte-identical
config/SDC/environment effects and marked only the first ID `dead_here`.

### Observed Result

```json
{
  "P1-12": {
    "failed_symptom": "06a2898894dd61af",
    "current_symptom": "2651be2eed9d1780",
    "dead_here_on_current_symptom": 2
  },
  "P1-14": {
    "effect_digests_equal": true,
    "first_is_dead_here": 2,
    "selected_strategy": "alias_two"
  }
}
```

### Root Cause

The dead-evidence query groups by project path, check type, and strategy
(`diagnose_signoff_fix.py:779-801`). It does not include `symptom_id`, so one DRC class
can blacklist a strategy for every DRC symptom on that project. Conversely, identity is
only the strategy string, so an equivalent edit under another name bypasses the gate.

### Impact

The agent can both over-generalize and under-generalize failure memory: it may suppress
a useful fix for the wrong symptom while retrying the same ineffective action through
an alias.

### Recommendation

Key negative evidence by `(project or subject fingerprint, platform, check, symptom,
effect_fingerprint)`. Preserve strategy ID for explanation, but use a canonical digest
of config, SDC, environment, hook, and rerun-stage effects for retry suppression.

## Issue 13: P1-13 and P1-15 - Promoted Recipes and Planned Arms Can Become Stale

### Probe

P1-13 inserted two consecutive live regressions for a promoted recipe and ran the normal
learner rebuild. P1-15 planned A/B arms, changed the recipe lifecycle to `shadow`, and
then exercised the arm-B `rank-first` selection path.

### Observed Result

```json
{
  "P1-13": {
    "status_after_normal_learn_rebuild": "promoted",
    "manual_auto_demote_helper_returned": true,
    "status_after_explicit_helper_call": "shadow"
  },
  "P1-15": {
    "planned_arm_count": 2,
    "arm_has_recipe_generation": false,
    "arm_has_recipe_hash": false,
    "status_changed_to": "shadow",
    "rank_first_selected_after_demotion": "stale_recipe"
  }
}
```

### Root Cause

`ab_runner.auto_demote_on_regression()` implements the intended demotion policy
(`ab_runner.py:459-477`), but production searches found no normal ingest/learn/diagnose
caller. It takes effect only when explicitly invoked.

Planned arm entries contain the strategy ID and baseline config hash, but no recipe
generation or effect hash (`engineer_loop.py:1542-1555`). The `rank-first` A/B path
intentionally bypasses lifecycle gates, and there is no pre-execution freshness check.

### Impact

A recipe can remain globally promoted after repeated live harm, while queued arms can
execute and judge an intervention whose lifecycle or implementation changed after
planning.

### Recommendation

1. Invoke regression auto-demotion from a deterministic production boundary after live
   fix-event ingestion.
2. Stamp each arm with lifecycle generation, strategy implementation/effect hash, and
   planned trial UUID.
3. Before execution and before judging, compare those stamps with current state. Cancel
   or re-plan stale arms instead of force-running them.
4. Keep `rank-first` bypass only after the arm's planned candidate identity has been
   revalidated.

## Issue 14: P1-16 and P1-17 - Evidence Validation and Evidence Provenance Are Not Enforced

### Probe

P1-16 supplied negative wall times, a negative iteration count, and `NaN` in A/B
metrics. P1-17 learned the same successful fix once as `live` and once as
`backfill:synthetic` in separate, otherwise identical stores.

### Observed Result

```json
{
  "P1-16": {
    "negative_wall_time_verdict": "win",
    "negative_wall_time_reason": "cost_tiebreak",
    "nonfinite_json_stored": true,
    "negative_iteration_stored": true,
    "recipe_status": "promoted"
  },
  "P1-17": {
    "live_counts": {"attempts": 1, "successes": 1, "wins": 0, "failures": 0},
    "backfill_counts": {"attempts": 1, "successes": 1, "wins": 0, "failures": 0},
    "learned_stats_preserve_provenance": false
  }
}
```

### Root Cause

The A/B judge performs arithmetic without validating finite, non-negative inputs, and
`json.dumps()` stores non-standard `NaN` by default. The learner retains provenance in
raw `fix_events`, but `_build_trajectory()` and all recipe projections omit it, so live
and imported evidence become indistinguishable after aggregation.

### Impact

Corrupt measurements can drive lifecycle decisions, and lower-trust reconstructed
history receives the same statistical weight as directly observed live evidence.

### Recommendation

1. Validate every arm sample against a strict schema before judging: finite non-negative
   duration, non-negative integer iterations, known success state, and valid run ID.
2. Serialize metrics with `allow_nan=False` and reject malformed legacy records during
   replay instead of treating them as decisive.
3. Preserve evidence provenance through trajectories and recipe aggregates.
4. Use separate counters or explicit weights for live, A/B, backfilled, and imported
   evidence. Backfill may generate a candidate hypothesis but should not claim
   live-equivalent confidence.

## Issue 15: P1-18 - The Agent Does Not Recognize Cross-Check Repair Cycles

### Probe

The probe modeled two individually successful recipes: one clears DRC while breaking
timing, and the other clears timing while restoring the DRC problem. It then ran the
production negative-gate annotation and live selector over the repeated global state.

### Observed Result

```json
{
  "selection_sequence": [
    "fix_drc_break_timing",
    "fix_timing_break_drc",
    "fix_drc_break_timing"
  ],
  "drc_strategy_gated": false,
  "timing_strategy_gated": false,
  "cross_check_state_fingerprint_present": false
}
```

### Root Cause

Negative evidence is check-local and considers `cleared` a success. Therefore neither
strategy becomes `dead_here`, even when the pair recreates a prior global signoff state.
There is no state fingerprint spanning DRC category vector, LVS class, timing tier, and
active config. `fix_signoff.sh` does cap one invocation at eight iterations
(`fix_signoff.sh:87,348`), so this is not an unbounded inner-shell loop; the gap is that
the same cycle can reappear across check phases or later sessions without a semantic
cycle diagnosis.

### Impact

The agent can repeatedly spend full-flow compute alternating between locally successful
repairs while making no global progress.

### Recommendation

After every repair, hash the global state `(config effect, DRC vector, LVS state, timing
tier, route/antenna state)`. If a state repeats with no strict Pareto improvement, stop
with `repair_cycle_nonconverged`, record the cycle path, and prevent its strategy sequence
from becoming positive training evidence.

## Issue 16: P1-19 - Match Level Is Descriptive but Does Not Constrain Ranking

### Probe

The probe ranked an exact recipe with two successful exact attempts against a pooled
recipe with 90 successes in 100 lower-specificity attempts.

### Observed Result

```json
{
  "top_strategy": "pooled_recipe",
  "ranking": [
    {"strategy": "pooled_recipe", "score": 0.8921568627450981,
     "provenance": "prior(pooled,tried=100)"},
    {"strategy": "exact_recipe", "score": 0.75,
     "provenance": "learned(n=2,tried=2)"}
  ]
}
```

### Root Cause

`fix_model.rank_strategies()` applies the same Beta-smoothed score to exact and pooled
statistics (`fix_model.py:31-99`). Its confidence floor only limits pooled evidence below
five attempts (`fix_model.py:28,74-82`). Once that floor is crossed, match specificity is
reported in provenance but is not a score weight or safety guard.

### Impact

Large but weakly matched history can override current-platform/current-class evidence.
This may be desirable exploration for explicitly transferable recipes, but it is unsafe
as an implicit default for platform-sensitive physical-design actions.

### Recommendation

1. Make match level part of the ranking policy, for example with hierarchical Bayesian
   shrinkage or explicit exact/class/platform transfer weights.
2. Require a strategy capability flag such as `platform_agnostic` before pooled-platform
   evidence can become the live top choice.
3. Report local and pooled scores separately and require A/B validation when pooled
   evidence would displace an exact promoted recipe.

---
