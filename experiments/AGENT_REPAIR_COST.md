# Agent API repair cost measurement

`measure_agent_repair_cost.py` measures the current public chat/tool workflow on
original repair-case RTL. It calls `/api/chat`, lets the model submit the design,
polls jobs without model requests, and asks for one final evidence-based explanation.
Each case has its own session. Existing sessions and jobs are resumed without
automatically resubmitting an uncertain POST.

This is **Agent orchestration plus the deterministic repair engine**. The current
Agent has no tool that individually selects or edits every physical repair.
These measurements therefore do not establish the cost of autonomous LLM recipe
discovery. Historical recipe labels belong only in the evaluator manifest, never
in the model's input. Use original sources and the current platform defaults;
do not describe this as a strict replay of an older fixed-period baseline.

## Accounting

- Provider-reported input/output/total tokens and available cache/reasoning
  components are persisted per model call and exposed at
  `/api/sessions/{id}/usage`. Missing counters remain null; they are not zero.
  A request attempt is counted even when authentication or streaming fails.
- Set `R2G_ORFS_AUDIT_FILE` in the isolated service environment before starting
  jobs. Pipeline stages propagate job/design/stage/execution identities to all
  children. `run_orfs.sh` emits wrapper start/end events, including preflight
  failures. The optional make shim records successfully spawned external make
  processes, including direct signoff restaging calls.
- Report wrapper calls and external make launches separately. Split clean
  targets from build/check targets. Neither number means full place-and-route
  repetitions: stages may resume or do no work, and GNU make's absolute-path
  internal recursion is not counted. Preserve unmatched start events after
  interruption instead of inventing successful exits.
- Separate initial submission from the final explanatory turn, physical
  DRC/LVS results from graph verification, and calibration API calls from cases.
- This does not measure money charged by a provider, tool CPU cost, or formal
  equivalence of all repaired designs. Preserve source hashes and actual repair
  events for review; exclude known semantically invalid transformations.

## Run

Start a separate service with its own `R2G_HOME` and audit path, using existing
application credential loading. Never put credentials in a manifest, log, or
command line. Do not restart a production service with active designs.
`experiments/agent_repro/serve_metered.py --source-home <existing-app-home>
--home <experiment-home> --port 8772 --workers 8 --cores 4` starts such a service
with the saved model configuration and an in-memory credential resolver. Run it
with the installed Agent Python and set the audit path in its environment.

```sh
python3 experiments/measure_agent_repair_cost.py \
  --manifest design_cases/api_repair_cost_20261028/case_candidates.json \
  --api http://127.0.0.1:8772 --workers 8 --dry-run
```

After reviewing the selected original inputs, omit `--dry-run`. The server's
EDA worker/core limits are independent of the runner's chat concurrency. Raw
manifests, conversations, reports, databases, and layouts belong under ignored
`design_cases/`; only reusable accounting and runner code belongs in Git.

`summarize_agent_repair_cost.py --source <experiment-dir>` reads the persisted
ledger and produces `STATUS.md`, `costs.csv`, and `status_snapshot.json`. It
separates actual repair attempts from historical cases that now pass directly.
For live updates, add `--watch --interval 20 --runner-pid <pid>`; the watcher
exits after the runner ends and never spends model tokens. Supplemental cohorts
use separate output directories and denominators, preserving original failures.
