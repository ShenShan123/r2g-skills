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

## Full historical population

The full campaign includes 148 Nangate45 historical repair entries and 80 SKY130
entries. Known semantic counterexamples remain in the inventory as original-RTL
attempts under the normal transformation guards; inclusion does not validate an
unsafe historical transformation. `prepare_full_n45_cost.py` inventories the
original corpus and authenticated local ROM assets. `prepare_full_sky130_cost.py`
reads original baseline source metadata and hashes; it does not transfer source
contents and refuses to overwrite an existing inventory without an explicit flag.

To include an already measured case, set `reuse_case_directory` to its original
case receipt directory after checking source identity. The runner follows that
attempt and its original owner without another API POST. It never treats the
mere presence of a directory as proof of a completed measurement. Compilation
defines are preserved when creating neutral input manifests.

`summarize_repair_population.py` merges cohort CSV files against the complete
parent manifests, retaining unsubmitted and blocked entries in the denominator.
It deduplicates by platform and CID and rejects conflicting measurements. The
full report distinguishes all planned cases from cases whose API requests have
actually started and from completed measurements.

To compare thread budgets, use `R2G_FIXED_CORES=1` with the desired
`cores_per_design`; this prevents the interactive small-design cap from reducing
ORFS to four threads. It does not change checker decks' internal thread settings
or impose a CPU affinity/cgroup limit. Keep different thread-budget campaigns in
separate homes and reports.

For workers on another host, `serve_model_relay.py` forwards only the configured
model to its fixed HTTPS provider through loopback and an SSH tunnel. Provider
credentials stay on the controller. `sync_remote_cost_reports.py` copies only
bounded experiment receipts and reports, then updates platform-specific totals.
Calibration sessions are excluded because only case-associated sessions count.

Session ledgers use SQLite WAL so report snapshots do not block streamed usage
writes. Status GET requests can retry transient failures without another model
POST. `--recover-unsubmitted` permits one recorded recovery turn in an existing
session only after a closed initial stream and a server check that no associated
job exists; the old attempt remains in the same ledger. Existing jobs are polled
and never automatically resubmitted.
