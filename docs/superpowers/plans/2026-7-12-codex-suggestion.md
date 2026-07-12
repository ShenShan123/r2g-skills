# Codex robustness suggestions — revised assessment & closure (2026-07-12)

Seven robustness suggestions were proposed (Codex, verbatim originals preserved in
§"Original suggestions" at the bottom). This document **revises** them against the
*actual* r2g-skills codebase as of 2026-07-11 (post robustness-sweep #34–#37): each is
graded **DONE / PARTIAL / GAP**, with file:line evidence, and — for the partials — a
*revised, minimal, in-architecture* action. Several suggestions were already shipped in
the 2026-07-10 sweep; re-implementing them would be duplicate/regression risk. The value
of this pass is the genuine **gaps** and the **bugs** the audit surfaced while reading.

Audit method: a 7-way parallel investigation (one deep reader per suggestion) cross-checked
by direct reads of `signoff_gate.py`, `run_graphs.sh`, `run_orfs.sh`, `fix_signoff.sh`,
`build_diagnosis.py`, `rtl_risk.py`, `expand_candidates.py`, `promote_candidates.py`.

Legend: **DONE** = suggestion fully satisfied by existing code · **PARTIAL** = core exists,
a named sub-capability missing · **BUG** = correctness/honesty defect found while reading.

---

## 1. Granular pre-screening for RTL candidate quality — **PARTIAL**

**What exists.** Tokenized RAM/hard-macro *risk-marking* (not rejection) via
`rtl-acquire/scripts/common/rtl_risk.py:25-65`; flags ride the candidate CSV `notes`
(`risk_flags=…`), and the synth attempt is the arbiter (2026-07-10 sweep). A
hand-authored/auto-fix `resource_tier=high` column can hard-block a round.

**Gap (revised action).** The suggestion's *"route high-risk candidates to a low-priority
queue instead of polluting the main flow"* is **absent**: `expand_candidates.py` never parses
`risk_flags` and runs candidates in plain CSV order, so a memory-heavy design burns a synth
slot interleaved with clean ones. → **Close the ordering gap only** (smallest safe change):
a *stable deferral sort* in `expand_candidates.py.main()` (after the `--priorities` filter,
skipped when `--candidate-names` is given) that pushes risk-flagged / `resource_tier=high`
rows to the tail, with an observable `risk_deferred` stage marker. The deeper static checks
the suggestion also names — memory-**bit** estimate and module-dependency-**completeness**
tagging — are a **documented follow-up** (larger; needs an HDL array-size parser), NOT done
in this pass.

**Bugs found.** (a) `run_expansion_round.py:473` — the `resource_tier=high` guard scans *all*
CSV rows *before* the `--priorities` filter, so a `high`-mem row that would be filtered out
(e.g. `priority=low`) still hard-blocks the whole round. (b) `discover_download_candidates.py:839`
never emits the documented `resource_tier` column (schema/coverage hole — follow-up).

## 2. Automated handoff synth-only → full-flow — **DONE** (one honesty bug)

`promote_candidates.py:198-316` implements all 7 promote steps (eligibility → vendor RTL →
`config.mk` → SDC → `validate_config.py` gate → `metadata.json` + `reports/promote.json`).
Fully satisfies the suggestion.

**Bug found.** `promote_candidates.py:299-315` — `promote.json`/`metadata.json` are written
*before* the optional `--run` flow can flip `result['status']` to `promoted_flow_failed`, so
the on-disk manifest persists `status="promoted"` even when the immediately-run ORFS flow
failed. → **Re-dump the manifest after the `--run` block.**

## 3. Prevent redundant ORFS-wrapper triggers — **PARTIAL**

**What exists.** Stage-scoped resume (`run_orfs.sh:238-242` `make clean_<FROM_STAGE>`; #35)
already prevents the *actual* redundant re-execution; `stage_log.jsonl:262` records
`{stage,status,elapsed_s}`.

**Gap (revised action).** The suggestion's *"log artifact paths + timestamps per stage"* and
*"explicitly state the rerun reason in the logs"* are missing from the run's *own* persisted
artifacts: (i) `stage_log.jsonl` has no output-artifact path or absolute timestamps; (ii) the
`clean_<stage>` decision is a plain `echo` (stdout only — **not** tee'd to `flow.log`, bug
below) that names no cause. → Extend `run_stage()` to add `ts_start`/`ts_end`/`artifact`/
`artifact_mtime`; append structured `{"event":"clean"|"reuse", …, "reason":…}` rows reading a
new optional `R2G_RERUN_REASON`; `tee -a flow.log` the decisions; and plumb a concrete reason
from `fix_signoff.sh` (which already journals the strategy) into that env var.

**Bug found.** `run_orfs.sh:239,288` — reuse/rerun decisions are `echo` (stdout only), absent
from the persisted `flow.log` a post-hoc reviewer reads.

## 4. No-progress exit for antenna repair — **DONE** (one over-abort bug)

`fix_signoff.sh:324-467` implements the non-convergence auto-exit (#36): two non-improving
antenna strategies → terminal `antenna_nonconverged` verdict + `reports/antenna_nonconverged.json`
marker + negative-evidence ingest; later sessions auto-exclude the futile strategies.

**Bug found.** `fix_signoff.sh:445-447` — `antenna_noimp` is only ever incremented, never reset
on an *improving* antenna iteration (unlike the generic `noimp` at :435), so the counter is
**cumulative, not consecutive**. A slowly-converging design with interleaved wins and no-ops
(10→5 win, 5→5 no-op, 5→3 win, 3→3 no-op) aborts at 3 residuals despite clear 10→3 progress. →
**Reset `antenna_noimp=0` on an antenna-scoped improving iteration** (match `noimp` semantics).

## 5. Decouple routing-DRC from overall cleanliness — **PARTIAL**

**What exists.** `signoff_gate.py:174-204` *already* tracks five independent dimensions —
`drc`, `lvs`, `orfs`, `route`, `timing` — each pass/fail/unknown. Strong match for the
suggestion's "track each individually".

**Gap (revised action).** **Antenna** is the one metric NOT broken out: a full-deck antenna
violation shows only as `drc=fail` (undifferentiated from metal spacing), and under
`clean_beol` (ANTENNA rule group disabled) or a route-residual-only fallback, antenna is
*invisible* — `route=clean + orfs=complete` can yield `pass` with antenna never checked. →
Add `_check_antenna(reports_dir)` reading `drc.json` antenna-class categories **and**
`reports/antenna_nonconverged.json`; record `antenna` as its own dimension
(clean/fail/nonconverged/unknown/not_covered-beol) as a caveat (not a new blocker — a full-DRC
antenna failure already blocks via `drc`). This also feeds §6.

**Bugs found.** `signoff_gate.py:132` — `_check_route` trusts `status=="clean"` via
short-circuit before checking `tv==0` (latent honesty hole if a foreign writer emits
`clean`+`tv>0`); and a `route.json` `status="unknown"` is mislabeled `dirty` rather than
`unknown`. → Gate on `tv==0`; map unknown→unknown.

## 6. Enhance graph-generation manifests with specific backend reasons — **PARTIAL**

**What exists.** The signoff gate stamps `signoff_health` into `graph_manifest.json` on the
*build* path (`run_graphs.sh:98-164`).

**Gap (revised action).** On the **skip** paths — DEF-missing (`run_graphs.sh:84`) and
gate-blocked enforce (`:100`) — the manifest carries a *generic* `"no 6_final.def"` /
`"not signed off"` string; the specific upstream cause (ORFS `orfs_fail_stage` from
`ppa.json`/`stage_log.jsonl`, or `antenna_nonconverged`) is never threaded in, and
`reports/antenna_nonconverged.json` is never read anywhere. → Enrich `run_graphs.sh`'s
`skip()` with a best-effort `upstream` object (from `signoff_gate.json` blockers, else
`antenna_nonconverged.json`, else the newest backend `RUN_*/stage_log.jsonl` failing stage /
`ppa.json orfs_fail_stage`); and have `signoff_gate.py._check_antenna` surface the
non-convergence reason so it rides `signoff_health` on build paths too (shared with §5).

## 7. Automated log summaries / structured diagnostics — **PARTIAL**

**What exists.** `signoff-loop/scripts/reports/build_diagnosis.py` → `reports/diagnosis.json`
(parses `flow.log` + tool logs into structured issues).

**Gap (revised action).** No *single* summary unifies the five dimensions the suggestion
names: `diagnosis.json` omits **stage durations** (`stage_log.jsonl`) and **repair repetitions**
(`fix_log.jsonl`); those live only in the DB `runs` row. → **Extend `build_diagnosis.py`** (do
not add a new script) to also emit `stages:[{stage,status,elapsed_s}]`+`total_elapsed_s` from
`stage_log.jsonl` and `fix_iterations`/`fix_iters_to_clean` from `fix_log.jsonl`, turning
`diagnosis.json` into the consolidated run-summary the suggestion asks for.

**Bug found.** `build_diagnosis.py:33-38,187-194` — `parse_synth_errors()` flags *any* line
containing `ERROR`/`Error:` but `main()` feeds it the *full concatenated* text (flow.log +
drc/lvs/rcx/route logs), so a `[ERROR GRT-…]` routing line or an LVS-mismatch line is
mislabeled a **synthesis** error — a false-positive diagnosis that can send a fixer down the
wrong lever. → **Scope `parse_synth_errors` to the `synth.log` section only** (as #8/#9
already scope theirs).

---

## Implementation status (this pass — 2026-07-12)

All BUGS and the small/medium in-architecture gap closures below are implemented with tests +
a `failure-patterns.md` entry (#38) + CHANGELOG. The deeper #1 static analysers (memory-bit
estimate, dependency-completeness tag) and the `resource_tier` discovery-column coverage hole
are left as documented follow-ups (see failure-patterns #38 "Deferred").

| # | Change | Files | Test |
| - | ------ | ----- | ---- |
| 7-bug | `parse_synth_errors` scoped to synth section | `build_diagnosis.py` | `test_build_diagnosis.py` |
| 4-bug | antenna_noimp consecutive reset | `fix_signoff.sh` | `test_antenna_nonconverged.py` |
| 2-bug | promote manifest re-dump after `--run` | `promote_candidates.py` | `test_promote_candidates.py` |
| 5-bug | `_check_route` gate on `tv==0`, unknown→unknown | `signoff_gate.py` | `test_signoff_gate.py` |
| 1-bug | resource guard applied after priority filter | `run_expansion_round.py` | `test_run_expansion_round.py` |
| 5-F | antenna as its own gate dimension | `signoff_gate.py` | `test_signoff_gate.py` |
| 6-F | specific upstream reason into graph manifest skip | `run_graphs.sh`,`signoff_gate.py` | `test_signoff_gate.py` |
| 3-F | stage_log artifact+ts+rerun-reason, tee to flow.log | `run_orfs.sh`,`fix_signoff.sh` | `test_stage_log_provenance.py` |
| 7-F | consolidate durations+repetitions into diagnosis.json | `build_diagnosis.py` | `test_build_diagnosis.py` |
| 1-F | low-priority deferral sort for risk-flagged candidates | `expand_candidates.py` | `test_expand_candidates.py` |

---

## Original suggestions (verbatim, preserved)

1. **Granular Pre-screening for RTL Candidate Quality** — designs like sha512 fail synthesis on
   large memory/array structures. Add lightweight pre-checks after rtl-acquire to *tag* risks
   rather than filter; route high-risk candidates to a low-priority queue.
2. **Automated Handoff from Synth-Only to Full-Flow** — 1-click promotion of successful
   synth-only candidates into ORFS full-flow projects (RTL copy, configs/constraints, manifests).
3. **Prevent Redundant Triggers in the ORFS Wrapper** — log artifact paths + timestamps per stage
   to prioritize reuse; explicitly state the reason when a rerun is necessary.
4. **No-Progress Exit Mechanism for Antenna Repair** — monitor antenna counts, auto-abort with an
   "antenna repair non-converged" report if no improvement over N iterations; save the report.
5. **Decouple Routing DRC from Overall Layout Cleanliness** — separate routing-DRC, antenna,
   timing, DRC, LVS into individual pass/fail/unknown; gate the high-quality dataset on critical
   checks; record risks in the manifest.
6. **Enhance Graph Generation Manifests** — retain the DEF gate but pass specific backend failure
   reasons (e.g. "finish interrupted because antenna repair non-converged") into the manifest.
7. **Automated Log Summaries and Structured Diagnostics** — a log parser/summary that extracts
   stage durations, repetitions, violations, DRC/LVS status, and failure reasons into a
   structured `run_summary.json`/`diagnosis.md`.
