# Successful API repair cost protocol

The fixed populations are 148 Nangate45 and 80 SKY130 cases. Each full attempt
starts with a fresh API session, an original input copy checked against the
population manifest, the normal platform defaults, and eight fixed EDA cores.
The model orchestrates the installed Agent; physical repairs run through its
deterministic strategy/recipe pipeline. This experiment does not claim that the
model invents or personally selects every physical edit.

For each platform and case, retain the **earliest eligible successful attempt**.
Keep a first successful attempt when unrelated tooling is subsequently fixed.
For a failed attempt, diagnose the cause, fix reusable infrastructure or recipe
integration, validate separately, then start another complete fresh API attempt.
Do not combine a failed session's tokens with another run's signoff or continue
from manually repaired inputs. Do not choose the cheapest of multiple successes.

The primary outcome is full-deck DRC clean and LVS matched, bound by run and
GDS/DEF hashes to the same archived final layout. Dataset checks are a separate
outcome; ordinary dataset failures do not erase physical dual-clean evidence.
A verifier that ran zero checks because its environment was broken invalidates
that measurement attempt. Original functionality and signoff conditions must be
preserved. Unsafe or impractical residual cases remain explicitly unresolved.

Formal cost includes **every normal API call and ORFS retry inside the accepted
full attempt**, including frontend retries, synthesis, implementation, automated
repair, and the final result explanation. Store dispatch and explanation tokens
separately as well. Provider input/output/cache counters retain their meanings;
cache reads are an input subset and must not be added twice. Missing counters or
unfinished responses are unknown, never zero.

Engineering/debugging, manual tests, calibration, failed full attempts, and
later duplicate successes remain in a separate attempt history and do not enter
formal cost. This is a **success-conditioned cost**, not end-to-end project cost
or the average cost over all attempted designs. Initial direct dual-clean cases
have their own costs and do not count as successful repairs. Frontend processing
and backend compatibility repairs are distinguished from physical repair.

ORFS main count is `run_orfs` wrapper starts. Also report actual spawned
top-level `make` invocations, split into build/check and clean; recursive make is
excluded. Require complete paired start/end journals before accepting costs.

## Tools

1. `measure_agent_repair_cost.py` submits and records fresh measured API attempts.
2. `export_repair_cost_evidence.py --source COHORT` runs on the execution host,
   copies small signoff JSON into a receipt, and hashes original inputs and
   archived final artifacts. It performs no EDA or model work.
3. `account_successful_repair_cost.py --config CONFIG` validates receipts and
   produces immutable accepted-attempt selections, per-platform case CSV files,
   platform totals, all-attempt exclusions, and pending cases.
4. `watch_successful_repair_cost.py --config CONFIG --watch` periodically exports
   evidence, mirrors only bounded remote reports, and refreshes the ledger.

The JSON config names the output directory, master `manifests`, and `sources`.
Each source declares `path`, `eligible`, `code_commit`, `cores_per_design`, and
`host`. An eligible cohort also needs its `launch_parameters.json` eight-core
receipt. A retired/debug cohort sets `eligible: false` and `exclusion_reason`.
To refresh a live source, set `refresh_evidence: true`; for a remote source also
declare `ssh_host` and `remote_path`. Source data, GDS, datasets, credentials, and
runtime databases are not transferred by the reporting watcher.

`accepted_attempts.json` binds session, job, source receipts, cost receipts and
signoff evidence hashes. Once accepted, changing or losing any selected evidence
stops accounting for review. `attempts.csv` retains excluded costs and reasons;
`pending_cases.json` preserves unsuccessful designs in the fixed denominator.
Only `successful_repair_only` in `platform_success_totals.json` is the requested
formal repair total. `already_clean_separate` must not be folded into it.

Experiment receipts and large datasets stay in ignored experiment directories.
Commit the reusable measurement/verification tools and this protocol to Git.
