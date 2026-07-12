# Changelog

Notable changes to the `r2g-skills` collection. Earlier history lives in the
git log (the commit messages are the long-term record — see CLAUDE.md "When
You Fix a Bug").

## 2026-07-12 — Codex robustness-suggestion audit: 5 latent bugs + per-metric/observability hardening (#38)

Audited 7 Codex robustness suggestions against the actual code (grading in
`docs/superpowers/plans/2026-7-12-codex-suggestion.md`). The 2026-07-10 sweep had
already shipped the big items; this pass closes **5 latent bugs** and 4 **partial**
gaps, each with tests + failure-patterns #38. Full suites green:
signoff-loop 818, def-graph 386, rtl-acquire 60.

### signoff-loop
- **Antenna non-convergence counter was cumulative, not consecutive** (`fix_signoff.sh`;
  #38a). `antenna_noimp` incremented but never reset on an improving antenna iteration, so
  a design converging via interleaved wins/no-ops (10→5→5→3→3) was falsely aborted at the
  2nd cumulative no-op. Now resets on each improving antenna iteration.
- **Diagnosis mislabeled route/LVS `ERROR` lines as synthesis errors** (`build_diagnosis.py`;
  #38b). `parse_synth_errors` got the full concatenated log; now scoped to the `synth.log`
  section (new `section_text`). Also **consolidated `run_summary`** (codex #7): stage
  durations + repair repetitions + DRC/LVS/route/timing status in `diagnosis.json`.
- **ORFS resume provenance** (codex #3). `run_orfs.sh` stamps per-stage `ts_start`/`ts_end`
  + output `artifact` into `stage_log.jsonl` (additive; contract preserved), tees the
  reuse/rerun decision to `flow.log` with its concrete `R2G_RERUN_REASON` (from
  `fix_signoff.sh`), and writes `resume_meta.json`.

### def-graph
- **Antenna is now its own gate dimension** (`signoff_gate.py` `_check_antenna`; codex #5) —
  clean/fail/nonconverged/not_covered/unknown, decoupled from routing-DRC so a
  routing-clean-but-antenna-dirty design is visible in `signoff_health` (a caveat, never a
  new blocker).
- **`_check_route` gates on the count, not the status string** (#38c): a foreign
  `status="clean"`+`violations>0` no longer reads clean; a genuine `unknown` no longer
  mislabels `dirty`.
- **Graph SKIP manifests carry the specific upstream reason** (`graph_skip_manifest.py`;
  codex #6) — antenna-nonconvergence marker / ORFS `orfs_fail_stage` / signoff blockers /
  newest `stage_log.jsonl` failing stage threaded into `graph_dataset.json`, not a bare
  "no 6_final.def".

### rtl-acquire
- **Promote manifest re-dumped after `--run`** (`promote_candidates.py`; #38d) — a failed
  immediate flow now records `promoted_flow_failed` on disk, not a stale `promoted`.
- **High-mem round guard scoped to runnable candidates** (`run_expansion_round.py`
  `runnable_high_mem_designs`; #38e) — a `resource_tier=high` row filtered out by
  `--priorities` no longer hard-blocks a round it was never in.
- **Low-priority deferral queue** (`expand_candidates.py`; codex #1) — risk-flagged /
  `resource_tier=high` candidates stable-sorted to the tail of the round (observable via a
  `risk_deferred` stage marker); `--no-defer-risky` opts out. Deeper static analysers
  (memory-bit estimate, dependency-completeness) remain a documented follow-up.

## 2026-07-11 — campaign driver single-instance guard end-anchors pgrep (#37)

### signoff-loop
- **The wave driver's single-instance guard no longer false-matches its own
  launching shell** (`tools/campaign_resume_waves.sh`; failure-patterns #37).
  The guard rejected a double-launch with an **un-anchored**
  `pgrep -f "campaign_resume_waves\.sh"`, which also matched the operator's
  launching shell — its `setsid bash tools/campaign_resume_waves.sh …` command
  line literally names the script. In the natural `… & sleep N; pgrep`
  confirm-it-came-up pattern the launcher outlives the check, so the guard saw a
  "rival" and refused to start, leaving a round dead-in-the-water after a reboot
  **while every DB honesty gate stayed green** (a driver that never starts is
  invisible to a store that only records runs that happened). Fix: END-ANCHOR the
  pattern (`campaign_resume_waves\.sh$`) so it matches only a process *exec'd* on
  the script, plus a `$PPID` exclude for the residual exact-match case; the robust
  per-ledger `flock` remains the primary guard underneath. A `R2G_GUARD_SELFTEST=1`
  hook runs the guard in isolation (report + exit before any wave work) so it is
  unit-testable. Tests: `signoff-loop/tests/test_campaign_driver_guard.py`
  reproduces the false-positive and proves a genuine second driver is still caught.
  **Lesson:** a `pgrep -f` liveness guard must match on the process's *exec
  identity* (anchored path), not on any command line that happens to *name* it.

## 2026-07-10 — robustness sweep across all four sub-skills

Six operator-reported robustness gaps, each closed with code + tests + a
failure-pattern entry (`signoff-loop/references/failure-patterns.md` #34–#36):

### rtl-acquire
- **Keyword screening is risk-marking, not rejection.** The RAM/hard-macro
  denylist no longer hard-rejects candidates on a raw whole-text substring hit
  (picorv32 was thrown away because the formal-only `RISCV_FORMAL_BLACKBOX_*`
  macro names contain "blackbox"). Tokenized, comment-stripped matching lives
  in `scripts/common/rtl_risk.py`; flags ride the candidate CSV `notes`
  (`risk_flags=…`); the synth attempt arbitrates, and the repair-side
  classifier excludes only on failure evidence (memory tokens only).
- **CWD-proof candidate paths.** `~`/`$VAR` expand; relative paths bind to the
  candidate CSV's directory, then the repo root — never the caller's CWD
  (`references/candidate_csv_schema.md` "Paths").
- **Retry mechanics.** Failed candidates always retried by default (unchanged);
  new `expand_candidates.py --force` re-runs recorded successes, and
  `discover_download_candidates.py --retry-excluded` re-emits candidates parked
  in `failed_candidates_exclude.csv`.
- **One-click promote** (`scripts/promote/promote_candidates.py`): synth-proven
  candidate (index `status==success`, optional publish gate) → ready-to-run
  signoff-loop full-flow project under `design_cases/` — vendored RTL, template
  config.mk carrying the proven synth knobs + floorplan directive (drops
  `R2G_FLOW_SCOPE=synth_only`), clock-port-detected SDC (virtual-clock
  fallback), `validate_config.py` as the readiness gate, optional `--run`.

### signoff-loop
- **Stage-scoped reflow instead of full rebuilds** (#35). `run_orfs.sh` now
  runs `make clean_<FROM_STAGE>` before a resume so a config edit is
  guaranteed to apply (ORFS's Makefile has no dependency on config.mk — a
  plain resume silently NO-OPed the edit) while earlier stages' artifacts are
  reused; `R2G_RESUME_NO_CLEAN=1` keeps the pure crash-resume. `fix_signoff.sh`
  resumes from each strategy's `rerun_from` by default (`--resume` is a no-op
  alias; `R2G_FIX_FULL_REFLOW=1` restores the old full rebuild).
- **Antenna repair non-convergence auto-exit** (#36). Two non-improving
  antenna strategies end the check with the terminal verdict
  `antenna_nonconverged` (ingested as `no_change` — negative evidence) and
  persist `reports/antenna_nonconverged.json`; later fix sessions auto-exclude
  the proven-futile strategies instead of re-burning the same diode+reroute
  reflows (the SHA-1/SHA-256 loop). `R2G_FIX_RETRY_NONCONVERGED=1` retries
  deliberately; the marker self-clears on CLEAN.

### def-graph
- **Automatic signoff gate before dataset generation** (#34). A `6_final.def`
  alone no longer builds a dataset: the shared `scripts/flow/signoff_gate.py`
  checks DRC ∈ {clean, clean_beol}, LVS ∈ {clean, skipped}, ORFS completion
  (`stage_log.jsonl`), and route/antenna residuals — fail-closed on missing
  reports. `run_graphs.sh` enforces; `run_labels.sh`/`run_features.sh` warn;
  `R2G_SIGNOFF_GATE=enforce|warn|off` overrides. The verdict is stamped into
  `graph_manifest.json` as `signoff_health`, and
  `tools/verify_graph_dataset.py`'s Group-C gate is now fail-closed (a dataset
  with neither signoff reports nor a recorded gate verdict FAILS instead of
  passing vacuously).

Tests: signoff-loop 806 passed / 1 skipped; def-graph 372 passed / 14 skipped
(torch venv); rtl-acquire 51 passed.
