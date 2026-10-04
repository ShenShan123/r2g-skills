# Full R2G Supplemental Evaluation

This is a post-development system evaluation on the original 13 Experiment 3
B tasks. Some retained recipes were developed with B-task evidence. This arm
does not estimate unseen-task generalization and does not replace M0-M3 or
Pure LLM results.

## Execution

- Retain the entire current native signoff-loop memory in an isolated snapshot.
- Use the native ranked diagnosis, lifecycle gates, and original fixed-task
  action policy. Do not force candidate recipes or widen promotion scopes.
- Original 100 MHz clock, fixed footprint, four cores, 7200 seconds per attempt,
  at most three attempts per task. Zero LLM calls and no online learning.
- Use independent, baseline-reset attempts, as in the original comparison.
  Exclude attempted strategy IDs and duplicate effects; stop at strict clean.
- Native config actions run through the existing Experiment 3 materializer and
  evaluator. This is a budgeted native-policy adapter, not an unrestricted
  production engineer-loop run. Unsupported mutation types fail explicitly.
- Use complete original baseline geometry, after checking its protected digest
  and metrics against the formal portable baseline.
- No legal action is a recorded outcome. Infrastructure-incomplete evidence
  stops the queue for review, not a conclusive repair failure.

## Checkpoints and Results

`manifest.json` records scope, code hashes and inputs. `snapshot.json` records
the original memory/code copy. `tasks/*/selection_*.json` records native actions;
`tasks/*/attempt_*/trial_evidence.json` preserves the existing strict score.
`progress.json` and `complete.json` summarize the independent supplement.
No relaxed clean criterion is introduced and original scores stay untouched.

Host: 208. One worker on CPUs 128-131. Resume using the same runner with
`--execute`; completed task results and complete attempt evidence are reused.
Runtime files, policy and original inputs must not change during the run.

Initial preflight: seven DRC tasks select pin_side_rebalance; four setup tasks
select hierarchical_place_timing_repair; tanh and blake2s have no legal native
action under the fixed-area policy. These counts are coverage, not repair wins.
