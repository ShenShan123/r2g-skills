# Held-Out V3 + Three-Platform Remediation Report (failure-patterns #59)

**Date:** 2026-07-29
**Inputs:** `2026-07-27-three-platform-{pilot-analysis,remediation-plan}-ff01d5c.md`,
`2026-07-28-heldout-v3-{generalization-analysis,remediation-plan}.md` (external, baseline
`ff01d5ccd62fa53e167446bcf33dce9911bda288`)
**Code baseline at close:** `main` @ `577137f` (== `origin/main`; every change pushed per the
campaign directive)

---

## 1. Audit verdict

Ten findings were reported across the four documents. **Nine reproduce in this repo and are
fixed here. One is out of repo scope.**

| ID | Verdict | Where it reproduced |
|---|---|---|
| **P0-HO-01** semantic RTL readiness | CONFIRMED absent | no README-health or undriven-logic gate anywhere in `promote_candidates.py` |
| **P1-HO-01** wrong synthesis log | CONFIRMED | `expand_candidates.py::synth_log_from` first-existing order |
| **P1-HO-01b** memory-limit misclassified | CONFIRMED | `classify_failed_candidates.classify` — no memory branch; every fall-through `exclude/low_value_failure` |
| **P1-HO-02** acquisition leaks into A/B | CONFIRMED | `project_frontend_diagnosis:95` writes `acquire_exclude` into the signoff ledger; `engineer_loop._known_apply_strategy:350` accepts any non-empty verdict |
| **P1-HO-03** frontend capability | CONFIRMED | `expand_candidates.write_project` forces `slang` for any `.sv`, no canary; `classify` maps `"slang"` → low value |
| **P1-HO-04** rejected repair stays active | CONFIRMED | `fix_signoff.sh` restored only `constraints/config.mk` |
| **RMD4-P1-01** non-deterministic bootstrap | CONFIRMED | `eda-install` has no pin file; `_env.sh` tries `/opt/OpenROAD-flow-scripts` before any pin |
| **RMD-FE-P1-01** macro-indirect closure | CONFIRMED | `INSTANTIATION_RE` cannot match a backtick module name; no `` `define`` resolution |
| **FE-dev #2** retry parameter loss | CONFIRMED | `build_scoped_retry_candidates` fixed 8-column rebuild |
| **FE-dev #3** discovery blind spots | CONFIRMED | no `src_v` in `PREFERRED_DIR_PARTS`; blanket `$display` reject |
| **LIM-FE-02** ORFS memory accounting | **OUT OF REPO** | `mem_dump.py` exists only in the ORFS checkout |

Section 8/11 of the external analysis lists five defects as already "Implemented". That was a
**different worktree** (patch digest `0b3ec61…`), not this repo. Only one of the five was
partially present here (`auto_fix_failures.py` already preserved the retry fields it computes —
but `build_scoped_retry_candidates` then discarded them). Treat those claims as hypotheses to
verify, never as repo state.

## 2. The shape of the round

Four of the nine are **evidence-capture** defects, not logic defects: a correct decision
procedure fed the wrong input, or a correct decision leaving the wrong artifacts behind.

- `synth_log_from` computes nothing wrong — it hands the classifier a clean log, so a correct
  classifier issues a permanent exclusion for a recoverable failure.
- The global regression comparator's verdict was right; the rollback restored the *input* and
  left the *outputs* describing the run it had just refused.
- `_known_apply_strategy` correctly asked "has this ever been applied?" — the wrong question,
  because a historical event proves an action was recorded, not that this executor can apply it.
- The bootstrap detector resolved a toolchain correctly; it just resolved a different one than
  production uses, because the pins were consulted one step too late.

*Generalizable rule: when a decision looks correct but its consequence is wrong, audit the
inputs it read and the artifacts it left, not the decision rule.*

## 3. Fixes (all with tests; see failure-patterns.md #59 for full detail)

| ID | Change | Commit |
|---|---|---|
| P1-HO-01 | Failure-log selection by STAGE OUTCOME (error > later stage > mtime) + structured `synth_failure.json` (`failure_stage`/sha256/`has_terminal_error`, observed-vs-cap memory bits, bounded next cap tier); no resolvable error log ⇒ `diagnostic_incomplete` ⇒ retry | `0a0223c` |
| P1-HO-03 | `common/frontend_capability.py` canaries each frontend (`yosys -p "plugin -i slang"`); only a PROVEN frontend is selected; `classify` gains a `defer` bucket writing `failed_candidates_defer.csv`, which discovery does NOT read | `0a0223c` |
| FE-dev #2 | Scoped-retry rebuild carries `synth_variant`/`synth_memory_max_bits`/`synth_frontend`/`resource_tier`/`top_parameters`/`defines`, retry row first | `0a0223c` |
| P1-HO-02 | `knowledge/action_domain.py`; `_known_apply_strategy` narrowed (not removed — P0-6 stale-catalog protection stays); `recipe_lifecycle._refuse_enqueue` + `park_foreign_domain()`; acquisition dispositions move to `acquisition_actions.jsonl` with legacy purge | `0a0223c`, `69fe63a` |
| P1-HO-04 | `fix_signoff.sh` snapshots + restores the whole evidence bundle (config + reports + `.r2g_signoff_run`), with a `.rv_rollback_pending` marker so an interrupted restore reconciles to the complete old state | `69fe63a` |
| RMD4-P1-01 | `scripts/setup/resolve_pins.sh` before detect/plan; explicit > agreeing pins > autodetect; conflicting live pins exit 4 in every mode; selection recorded in `install_manifest.json` | `69fe63a` |
| P0-HO-01 | `common/rtl_readiness.py` — status declaration + structural integrity + functional evidence; corroborated ⇒ reject, either alone ⇒ manual_review; enforced at promote, gated at publish, override cannot launder the verdict | `577137f` |
| RMD-FE-P1-01 | Repo-wide `` `define`` collection incl. headers, macro-in-instantiation-position resolution with cycle detection, resolved targets joining `local_refs`; unresolvable-but-defined ⇒ `closure_incomplete` ⇒ retry | `577137f` |
| FE-dev #3 | `src_v` and variants added; guard-aware `unguarded_testbench_marker()` (nesting-aware strip of `` `ifdef DEBUG``/`translate_off`/`` `ifndef SYNTHESIS``) | `577137f` |

## 4. Verification

- **Test suites:** signoff-loop + rtl-acquire + eda-install **1357 passed, 2 skipped**;
  def-graph (untouched, run under the torch venv) **487 passed, 10 skipped**. 83 tests are new.
- **V1 gates:** `tools/run_v1_validation_registry.py gates` → **23/23 executable gates PASS**
  (11 gate groups `executable_pass`).
- **Honesty gates** over the real committed store: **ALL GREEN** (fail↔event parity 379==379).
- **Negative control:** the rollback test was run against the pre-fix code path — it fails with
  `active pointer left on the rejected run`, the exact reported symptom. The fix makes it pass.
- **Non-regression on the live store:** the new A/B domain guard blocks `acquire_exclude` while
  admitting all 11 strategies actually present in `fix_events`; 0 of the 82 pending candidates
  are foreign-domain, so no live recipe is affected.
- **Live-shape replay:** an ORFS run dir with a NEWER successful canonicalize log beside a
  failing `1_2_yosys.log` (the exact mor1kx trap) selects the failing log, extracts
  `observed=25856 / cap=4096 / next=32768`, and classifies `retry/memory_limit`.
- **Bootstrap idempotence** on this host: two consecutive resolutions are byte-identical and
  select the pinned `/proj/.../OpenROAD-flow-scripts`, not an ambient checkout.

## 5. Campaign restored

`tools/campaign_resume_waves.sh` relaunched 2026-07-29 08:13 against the SAME ledger
`design_cases/_batch/sky130hs_r2_campaign.jsonl` (`PLATFORM=sky130hs`, `WORKERS=4`,
`NUM_CORES=8`, `WAVE_MAX=16`), driver **PGID 605597**.

Resumed at **`pending=167`** — the 163 pending plus the 4 `flow` entries the drain entrypoints
reclaimed (`verilog_ethernet_arp`, `…_axis_baser_rx_64`, `…_axis_baser_tx_64`, `…_eth_mac_10g`),
exactly as the #58 stop report predicted. All four skills are deployed as symlinks into the
canonical tree, so the campaign runs the fixed code.

To stop: `kill -9 -605597` (the process GROUP — killing the driver alone orphans the ORFS tree).

**Note on wave numbering:** `tools/_sky130hs_resume_logs/waves.log` is cumulative across the
round and numbering restarts at 1 on relaunch, so `grep 'WAVE_DONE.*wave=N'` can match a
historical line. Monitor by WAVE_DONE line count.

## 6. Open leads (unchanged from #58)

1. **`pin_pdn_short` repair recipe** — IO_PLACER layer / PDN stripe offset; 5 recurring designs.
2. **LIM-HO-02** — DRC scan-bound / deck tuning for the >169 K-cell tail-rule band.
3. **LIM-FE-02** (upstream) — ORFS `mem_dump.py` total-inferred-bits accounting. Until fixed,
   use the largest-instance field for cap decisions and never total bits for cost prediction.
4. 167 open ledger entries finish the round on resume.
