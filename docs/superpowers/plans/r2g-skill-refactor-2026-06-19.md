# r2g-rtl2gds — Skill Simplification Refactor Plan

**Date:** 2026-06-19
**Author:** agent-team synthesis (Cartographer · DeadCodeHunter · LoopSimplifier · ValueGuardian · DocsSlimmer), reconciled by lead
**Status:** PLAN — not yet executed. **Execution branch: `main`** (per operator directive 2026-06-19 — do not cut a side branch).
**Scope:** simplify the implementation, drop genuinely-dead code, merge procedures of the closed learning loop — while spotlighting the two flagship contributions and keeping **all four capabilities** (the three closure capabilities + DEF→dataset extraction) + multi-tech support.

---

## REVISION 2026-06-19 (rev-2) — ML-dataset machinery is KEPT, not dropped

> **Operator directive (overrides the original rev-1 verdicts below):** *Do NOT drop the
> ML-dataset machinery — this skill will be used to convert **all** DEF files into datasets.*
>
> **What changed from rev-1:**
> 1. **`run_labels.sh` + `run_features.sh` + `extract/labels/*` + `extract/features/*` + the two
>    `tools/run_{labels,features}_batch.sh` + `references/{feature,label}-extraction.md` are
>    RECLASSIFIED `DROP → KEEP`** (§2A row deleted; new §2C entry; §5 docs rows flipped to KEEP).
>    Rationale: rev-1 ran a reachability analysis rooted only at the *learning loop* and correctly
>    found these unreachable from it — but they are the **DEF→dataset capability**, a distinct
>    user-facing mission with its own root. Dead-to-the-loop ≠ dead. **Verified standalone** (zero
>    refs from `loop/`/`knowledge/`/`reports/`), so keeping them adds **zero** coupling risk.
> 2. **DEF→dataset extraction is promoted to a first-class capability** — see the capability matrix
>    in §0 (now four rows). It is no longer "accreted scaffolding from unmerged branches."
> 3. **The `presynth.py` carve-out (rev-1 §2A) is moot** — all of `extract/features/` stays in place,
>    so `presynth.py` is *not* relocated. The *latent Win-5 wiring fix* (wire `presynth.py` into
>    `ingest_run.py`) is still worth doing and survives as §7 Phase 8.
> 4. **Impact recomputed** (§8): the largest single deletion (~2,870 code + 215 docs LOC) is removed
>    from the DROP column. Net deletion drops from "~2,850 LOC" to **~390 LOC** (dead report builders
>    only); DEMOTE (§2B) and the four loop merges (§3) are unaffected.
> 5. **Execution on `main`** (operator directive), not a `refactor/skill-slim` branch.
>
> Rev-1 verdicts that are NOT changed: everything in §2B (demote), §3 (the four M-merges), §4
> (vestigial cleanup), §6 (honesty tripwires), and the two flagship contributions in §0.
>
> **ADDENDUM (rev-2, same day) — defer the DEF→dataset *conversion runs*; refactor the key parts first.**
> Operator follow-up: *"At current stage we can defer the ML-dataset conversion function; focus on
> refactoring the key parts of the skill."* Reconciled meaning:
> - **Keep** the machinery + docs in the tree (point 1 above stands — do **not** delete it).
> - **Defer** *exercising* it: no DEF→dataset campaign now; the §7 "run `run_labels`/`run_features`
>   on the validation design" step is **optional** this pass, not a release gate.
> - **Do not elevate** it to a SKILL.md first-screen headline yet — it is a **deferred/secondary**
>   capability until the operator un-defers it (the §0 matrix keeps the row but tags it *deferred*).
> - **Priority for this session = the refactor of the key parts** (the learning-loop core: §3 M1–M4
>   + §4 vestigial cleanup), not the 45nm stale-file cleanup or the completion campaign.

---

## 0. What this refactor is *for* (the spotlight)

The refactor must leave these unmistakably front-and-center. They are the answer to "what makes this skill special":

1. **Engineer-Learning-Loop** — `scripts/loop/engineer_loop.py`, the autonomous driver that closes
   *flow → fix → ingest → learn → A/B-validate → promote/demote* unattended, escalating only true unknowns. **Contribution #1.**
2. **Two-database Memory system** — `knowledge.sqlite` (what *resulted*; git-tracked; sole learner input)
   + `journal.sqlite` (what was *done*; gitignored; forensics), separated by a verified firewall. **Contribution #2.**

…in service of **four** user-facing capabilities, across six open-source PDKs:

| Capability | Spine entrypoint |
| --- | --- |
| **DRC fix** | `fix_signoff.sh --check drc` (antenna-diode, density/route relief) |
| **LVS fix** | `fix_signoff.sh --check lvs` (Netgen on sky130, KLayout elsewhere) |
| **Fmax search** | `fmax_search.py` (loose-first fastest closing period) |
| **DEF→dataset extraction** *(deferred — code KEPT, runs deferred)* | `run_labels.sh` (Y-side: congestion/wirelength/timing/IR-drop) + `run_features.sh` (X-side: graph nodes/edges/metadata), both reading the same `6_final.def`; batch via `tools/run_{labels,features}_batch.sh` |

PDKs: `nangate45` (default), `sky130hd`, `sky130hs`, `asap7`, `gf180`, `ihp-sg13g2` — defined in `scripts/extract/techlib/profile.py`.

**Hard rule for the whole refactor:** capability entrypoint *names* stay byte-stable
(`fix_signoff.sh`, `fmax_search.py`, `engineer_loop.py`, `ingest_run.py`, `learn_heuristics.py`,
**`run_labels.sh`, `run_features.sh`**) — they are referenced by tests, CLAUDE.md, and runbooks. Rename only safe-to-drop sprawl.

---

## 1. Surface-area baseline (what we are simplifying)

- **77 scripts/modules.** Cartographer's spine map: **32 on the runtime spine (42%)**, **45 on-demand/operator-only (58%)**, **0 true orphans** (everything has *a* caller, but most "callers" are operators, not the loop).
- **~17.7K LOC** non-test Python; **748-line** SKILL.md; **~4.7K lines** of references (failure-patterns.md alone = 1,783); 14K-LOC test suite.
- The mission core (RTL→GDS→signoff→learn) is wrapped in accreted scaffolding: a dormant second A/B harness, ~7 dead report builders, and operator CLIs intermixed with loop code. (The DEF→dataset pipeline — `run_{labels,features}` — was originally on this list in rev-1 but is **not** scaffolding: it is a kept first-class capability, see the rev-2 banner above.)

---

## 2. DROP / DEMOTE / KEEP — reconciled verdicts

Two cross-agent conflicts were settled by direct grep (lead-verified):

> **Conflict A — `presynth.py`:** KEEP (carve out). `suggest_config.py:66-100` + `ingest_run.py:777-849`
> read `reports/presynth_features.json` (emitted by `presynth.py`) into `runs.presynth_features_json`
> as the Win-5 KNN key. ValueGuardian correct; DeadCodeHunter's "drop all of `features/`" overreached.
>
> **Conflict B — `eval_heuristics.py`:** DEMOTE is safe. The only `eval_heuristics` token in `ab_runner.py`
> is a *code comment* (`:11`), not an import/call. The live A/B path re-implements invariant 11 itself.
> DeadCodeHunter correct; ValueGuardian's "called by ab_runner" was a false positive.

### 2A. DROP — dead leaves (≈ 390 LOC), zero loop/capability coupling

> **rev-2:** the ML-dataset machinery row (rev-1's ~1,560 LOC) and its two reference docs are
> **removed from this DROP table** — see §2C (KEEP) and the rev-2 banner. The carve-out trap is moot.

| Target | LOC | Evidence |
| --- | --- | --- |
| Dead report builders: `build_run_compare.py`, `build_run_history.py`, `list_artifacts.py`, `write_success_summary.py`, `summarize_run.py`, `collect_orfs_results.py`, `collect_reports.py` | ~386 | Zero non-test, non-self refs. `summarize_run.py` duplicates a helper inside `build_run_history.py`. |
| Stale `knowledge/*.sqlite.*.bak` backups | n/a | noise; remove after confirming no refs. |

**No carve-out needed (rev-2):** all of `extract/features/` is KEPT (it is the X-side of the dataset
capability), so `presynth.py` stays in place — no relocation, no consumer-comment churn.
**The latent Win-5 fix survives as a standalone improvement (§7 Phase 8):** `presynth.py` is currently
invoked by *nothing* on the spine, so Win-5 only fires if a human runs it manually → wire it into
`ingest_run.py` (or an ORFS post-synth hook) so the KNN feature actually populates each flow. This is
now a pure *repair*, decoupled from any deletion.

### 2B. DEMOTE to `tools/` (≈ 1,700 LOC moved, capability preserved)

Operator CLIs with **zero** loop/ingest/learn wiring — keep the capability, unclutter the skill:

- `knowledge/eval_heuristics.py` (980) — dormant payoff bench, superseded by `ab_runner.py` as the live A/B system.
- `knowledge/analyze_execution.py` (358), `trace_provenance.py` (125), `monitor_health.py` (131), `query_knowledge.py` (106) — operator forensics/CLIs.

Keep them runnable (move, don't delete); update the few SKILL.md operator-step pointers + test imports. **`trace_provenance.py` caveat:** it is the *only* legitimate (read-only) journal reader and the only end-to-end provenance tool — demote location, never delete.

### 2C. KEEP — load-bearing despite "looks droppable"

- **DEF→dataset machinery (rev-2 reclassification, ~2,870 code + 215 docs LOC):**
  `run_labels.sh` + `run_features.sh` (the two stage entrypoints), all of `extract/labels/*`
  (`extract_congestion.py`, `extract_wirelength.py`, `extract_timing.tcl`, `extract_irdrop.tcl`,
  `compute_label_stats.py`) and all of `extract/features/*` (`nodes_*`, `edges_*`, `metadata.py`,
  `compute_feature_stats.py`, `case_paths.py`, `presynth.py`), the batch drivers
  `tools/run_{labels,features}_batch.sh`, and the docs `references/{feature,label}-extraction.md`.
  **This is a first-class capability** (convert every `6_final.def` into joinable X/Y ML CSVs), not
  loop scaffolding. Standalone-verified (no `loop/`/`knowledge/`/`reports/` refs) → keeping it is
  zero-risk to the learning loop. **Do not merge/rename the two entrypoints** (byte-stable names,
  §0). The shared module `def_parse`/`lib_db`/`cell_type_map` under `extract/features/` is part of
  this and stays.
- **`detect_contradictions.py`** — wired into `engineer_loop.py` + `build_lineage_view.py` + dashboard, **and is an executable CI honesty gate** (`tests/test_honesty_invariants.py:348` asserts `find_contradictions(...)==[]`). Demote *surface*, not existence.
- **`repair_run_status.py` + `backfill_fix_events.py`** — the only tools that retroactively maintain `failure_events`↔`orfs_status` parity (the H3 invariant) on historical rows. Operator-only but production-critical.
- **`search_failures.py`** — called by `diagnose_signoff_fix.py` to surface the matching prose lesson at fix-decision time (symptom-indexed lookup).
- **`sync_lessons.py` + `fix_log_manager.py`** — both called by `ingest_run.py` in-pipeline.
- **Dashboard** (`generate/serve_multi_project_dashboard.py`, `render_drc_violation.py`, `render_gds_preview.py`) — SKILL-documented operator UX; `render_drc_violation` is a documented DRC-fix aid. Keep (optionally consolidate later, low priority).
- **Signoff tool alternates** `run_magic_drc.sh` / `run_netgen_lvs.sh` — sky130 production engines. **DO NOT merge into the KLayout runners**: that would erase the platform-aware Netgen routing (`fix_signoff.sh:115-120`), the project's highest-value bugfix (12/12 LVS false-fail recovery).
- All of `techlib/*` (multi-tech source of truth), all six platform runners, the entire Engineer-Loop + Memory core.

---

## 3. Merge the closed learning loop — from ~8 steps to 4

The loop's *capability* is sacrosanct; its *procedure surface* is over-large. Four merges (LoopSimplifier, all firewall-safe and invariant-checked):

**M1 — `knowledge update <run>`: one composite command = ingest + learn + mine.**
The seam already exists: `ingest_run.py:952-958` auto-invokes `fix_log_manager.manage()` post-ingest under `R2G_FIX_AUTOLEARN` (default on), in-process, in try/except, *after* the durable commit at `:907`. Extend that block to also call `learn_heuristics.learn()` + `mine_rules.mine()`.
- *Safe because:* learners only read what ingest wrote; `run_id = sha1(project_path : ppa.json mtime_ns)` makes re-ingest idempotent; `knowledge_db.connect` arms `busy_timeout`.
- *Guardrails:* (a) keep learn/mine in try/except so a learner crash never aborts the durable ingest; (b) the two JSON files are full-rewritten each call, so under parallel flows keep **learn/mine at batch granularity** (as `engineer_loop._learn():240-244` already does) or file-lock the rewrite. **Ingest stays per-flow; learn debounces.**
- *Strengthens* the "ingest after EVERY flow" invariant — one command can't be half-run.

**M2 — one learner module, two outputs (`learn_heuristics` + `mine_rules`).**
Both do the identical per-strategy trajectory rollup with the same verdict vocabulary (`learn_heuristics.py:259-278` ≈ `mine_rules.py:90-110`; mine's docstring admits the copy). Consolidate the *shared scan + helpers*; **keep the two outputs as two files with distinct consumers**. Bonus bug-fix: `fix_trajectories` is written by learn but only read by mine → running mine on a learn-stale DB yields stale candidates; "rebuild once, derive both" fixes it by construction.
- *Guardrails:* `diff_and_enqueue` (Gate-A) stays wired **only** to the heuristics output, never the `failure_candidates` arrays (the human-review queue must never auto-feed promotion); keep two CLI verbs so mining the queue doesn't bump generation/enqueue.
- *Scope honesty:* ~60-70% of each body is output-specific — this is a shared-scan consolidation, not a full unification.

**M3 — collapse `ab-enqueue` + `ab-drain` into one `ab` verb (plan+run+judge).**
`ab-drain` already does plan→run→judge (`engineer_loop.py:455-483`); `ab-enqueue` only force-enqueues. Collapse to `ab [--enqueue <key>] --ledger L`. Both enqueue paths are idempotent no-ops on an existing row, so enqueue→drain can't double-enqueue.
- *Keep:* arm-A control (`R2G_FIX_EXCLUDE`) vs arm-B forced (`R2G_FIX_RANK_FIRST`) separation, the LCB-over-*k* verdict, and "only a `win` promotes." One verb is *harder* to skip → better Gate-A alarm posture.

**M4 — centralize the duplicated safety clamp (highest-value, lowest-risk).**
The `PLACE_DENSITY_LB_ADDON ≥ 0.10` floor is policy duplicated as two implementations: a real numeric clamp in `suggest_config.py:372-378`, but **no clamp in `diagnose_signoff_fix.py`** — honored only by every strategy author manually omitting the knob (prose disclaimers at `:126/:162/:181`). Also duplicated: size-class thresholds (100/5000/50000) and two `config.mk` parsers. Extract a shared **`config_safety`** module (`clamp_config()` + `size_class()` + one parser) called by *both* apply-side scripts. Turns an unenforced convention into a provably-applied rail.

### The simplified turn of the wheel

```
1. Flow            run_orfs + signoff  → reports/*.json
2. knowledge update <run>   [M1]  = ingest (always) + learn + mine [M2] + Gate-A enqueue   (one command)
3. Apply           suggest_config (seed, pre-flow) + diagnose_signoff_fix (repair, post-flow),
                   sharing one config_safety clamp [M4]                                     (two phases, one rail)
4. ab              [M3]  = plan + run + judge + promote/demote                               (one verb)
                   unknowns → escalations; loop never blocks
```

### KEEP-SEPARATE (do not over-merge)
- **The two DBs** — firewall verified (no learner references the journal; only `trace_provenance.py` reads it, read-only). Never merge.
- **`suggest_config` (seed, pre-route) vs `diagnose_signoff_fix` (repair, post-route)** — disjoint phases; merge only the *clamp* (M4), keep entrypoints split.
- **`heuristics.json` (auto-consumed) vs `failure_candidates.json` (human-gated)** — two files, distinct consumers, even under M2.

---

## 4. Vestigial code (delete or wire, separate from the merges)

- **DELETE:** `ab_runner.auto_demote_on_regression` (`:264-281`, zero runtime callers — the documented "2 regressions → auto-demote" edge is dead code; *either* wire it into `process_one`'s post-fix path *or* drop it + its doc claim) and `learn_heuristics._fetch_rows` (`:39`, never called).
- **DOC-FIX:** `recipe_lifecycle.stage_shadow` has no runtime caller; the `shadow` *state* is live but only as an A/B *demotion sink*, never an authoring *source*. The real lifecycle is `candidate ⇄ promoted/shadow`, **not** the documented `shadow→candidate→promoted`. Correct CLAUDE.md / engineer-loop.md / knowledge-README to match reality.
- **SCOPE OUT (keep, don't touch in this refactor):** `config_lineage`, the `symptoms` *table*, and journal `tool_bugs` are observability/forensics projections read by dashboard/provenance, not learners. They are *not* dead — leave them alone.

---

## 5. Documentation slimming (DocsSlimmer)

**SKILL.md: 748 → ~330 lines.** New first screen leads with a "What makes this a learning skill, not a wrapper" block (the 2 contributions) + the 3-capability triptych + the 6-PDK matrix — *above* Environment Setup. Promote the Engineer-Loop + two-DB section **above** the linear 18-step workflow (today it's buried at line 248, reading as plumbing); demote the per-stage steps to a "Manual single-run path" subsection. Move out to references: env resolution order, MVP Scope, Macro/SRAM designs, ORFS Backend Details, Shell Safety Rules, "Running Signoff" (redundant with Workflow step 6) → mostly into `orfs-playbook.md` (their natural, partially-duplicated home).

**References:**
| File | Action |
| --- | --- |
| `failure-patterns.md` (1783) | **SPLIT** → `failure-patterns.md` (flow+signoff triage, ~900) + `batch-campaign-patterns.md` (~600). Loadability win. |
| `lessons-learned.md` (388) | **MERGE** ~5 unique items into failure-patterns, then DROP (dated narrative, mostly duplicated). |
| `feature-extraction.md` + `label-extraction.md` (215) | **KEEP** (rev-2) — they document the DEF→dataset capability (§2C). Light-slim only; do not drop. |
| `orfs-playbook.md` | **GROW** — absorbs the SKILL.md moves above (dedupe, don't double-maintain). |
| `engineer-loop.md` | KEEP, but link `knowledge/README.md` as the canonical schema source (stop duplicating the DB-split diagram). |
| `knowledge/README.md`, `signoff-fixing.md`, `ppa-report-guide.md`, `workflow.md`, `spec-template.md` | KEEP. **rev-2:** `workflow.md`'s dataset phase is NOT slimmed away — DEF→dataset is a first-class capability; keep it documented (light copy-edit only). |

**Spotlight opener (draft, rev-2):** *"Turn a hardware spec or RTL into a signed-off GDSII through OpenROAD (ORFS) — fully unattended. When signoff fails it diagnoses and repairs the real layout across three closure capabilities — DRC fixing, LVS fixing, and Fmax search — across six PDKs, and it converts every resulting layout into joinable ML feature/label datasets (DEF→dataset). What sets it apart from a flow wrapper: it learns from every run, via two flagship systems — a two-database memory behind a firewall, and an engineer loop that closes flow→fix→learn→A/B-promote on its own."*

---

## 6. Honesty-invariant tripwires — every change must clear these

From CLAUDE.md + `tests/test_honesty_invariants.py` (these are *already executable CI gates*; the refactor must keep them green):

1. **Ingest after EVERY flow** (clean/failed/partial). M1 strengthens this; don't regress it.
2. **`failure_events` mirrors `orfs_status`/`orfs_fail_stage`** (H3). Every writer of those columns must emit the event — preserved by M-merges only if both writers still do.
3. **`ab_trials` non-empty whenever `fail`/`partial` rows exist** (Gate A). Empty `ab_trials` beside fail rows = the loop is inert and lying.
4. **`busy_timeout` on every connection** — don't "simplify" the connect helper into dropping it (loses runs under the campaign pool).
5. **Latest-row-only repair; old fail + new pass coexist.** `outcome_score` is a pure function of one run's own artifacts — never a sibling-row SELECT.
6. **One success predicate** = `knowledge_db.is_success` (imported by learn/monitor/dashboard/eval). Never inline/duplicate.
7. **`heuristics.json` + `failure_candidates.json` are full-rewritten each learn/mine** — never write a learned strategy directly into either; new strategies pass the human-review gate; `failure-patterns.md` is never auto-written.
8. **Read-only projections stay read-only** (`build_lineage_view`, dashboard panels open `mode=ro`).
9. **Contradiction gate green on ship** (`detect_contradictions.find_contradictions(...)==[]`).

**Fast check after each commit:** `count(runs where orfs_status='fail') == count(rows carrying an orfs-fail-% failure_event)`; once fail/partial rows exist, `ab_trials` is non-empty; full `pytest` green.

> **ADDENDUM 2026-06-20 (campaign resume; new honesty tripwire #10).** A 10th invariant surfaced
> live and is now enforced: **the `fix_signoff.sh` exit gate is fail-CLOSED** — a check counts as
> signed off ONLY for `status ∈ {clean, clean_beol, skipped}`; any other status (`stuck`,
> `incomplete`, `crash`, `unknown`, `fail`, …) is a residual → exit 2 → escalate. Before this fix
> the gate was a fail-OPEN denylist (`{fail,failed,residual,timeout}`), so DRC `stuck` (FEOL hang)
> and LVS `incomplete` (no verdict) fell through as exit 0 and `engineer_loop._process_one` marked
> the design `clean` in the campaign ledger (the knowledge `runs` row stayed honest, so the learner
> was not lied to — but the ledger/clean-count was). Found on `cf_fir_24_16_16`; 11/101 nangate45
> ledger-`clean` designs were mislabeled and reconciled to honest `escalated`. Regression:
> `tests/test_fix_signoff_clean_gate.py`; full detail in `references/failure-patterns.md`
> ("stuck/incomplete mislabeled `clean` by the fix-loop exit gate"). **Fast check:** every ledger
> `clean` design's latest `runs` row must have `drc_status` AND `lvs_status` both in
> `{clean, clean_beol, skipped}`.

> **ADDENDUM 2026-06-22 (campaign resume; tripwire #3 strengthened to be PER-PLATFORM).**
> Tripwire #3 ("`ab_trials` non-empty whenever `fail`/`partial` rows exist") is too weak: a corpus
> can have non-empty `ab_trials` overall and still be **inert for an entire platform**. Live, all
> `ab_trials` were sky130hd while every nangate45 candidate sat in `recipe_status='candidate'`
> forever. Root cause: `ab_runner.plan_trial` Tier 1 keys on `run_violations` (the **post-fix**
> residual snapshot), so a recipe that *fully clears* its symptom (`antenna_diode_repair` → 0 DRC)
> leaves no residual and is unreachable; the Tier-2 evidence-name-list matched the bare DESIGN_NAME
> against the campaign's `<Repo>_<design>` dir basenames and resolved nothing. Fix: a new Tier-2
> `_symptom_designs` resolves subjects from `fix_trajectories`/`fix_events` by `symptom_id`
> (symptom-confirmed, on-disk-precise). Regression `tests/test_ab_fixhist_subjects.py`; detail in
> `references/failure-patterns.md` ("a SUCCESSFUL recipe is unreachable by the A/B planner").
> **Strengthened fast check:** for each `platform` that has a `recipe_status='candidate'` row whose
> symptom has ≥2 on-disk exhibitors in fix-history, that platform must eventually gain an `ab_trials`
> row — "non-empty *somewhere*" no longer counts as the loop being live for *this* platform.

---

## 7. Execution plan (phased; each phase = a commit, suite green between)

> **EXECUTION LOG 2026-07-01 (house-cleaning pass; staged, UNCOMMITTED — add hash on commit).**
> **Phase 1 DONE (deletion portion):** removed all 7 dead report builders — `build_run_compare.py`,
> `build_run_history.py`, `collect_orfs_results.py`, `collect_reports.py`, `list_artifacts.py`,
> `write_success_summary.py`, `summarize_run.py` (386 LOC). No `.bak` files existed. Fixed their live
> doc refs: dropped `SKILL.md` step 16 (renumbered 17→16, 18→17) and `workflow.md` step 5. Re-verified
> dead 4 ways (this plan + reference-graph + HEAD re-grep + an adversarial refute sweep). Suite still
> collects 911, 0 errors. **NOT YET DONE in Phase 1:** SKILL.md project-layout/Resource-Map trims.
> **CORRECTION — `extract_progress.py` is NOT droppable (do not delete it in a future pass):** it was
> briefly deleted in this pass, then RESTORED. It is the *sole producer* of `reports/progress.json`,
> which `generate_multi_project_dashboard.py:141` consumes and renders as the per-design Stage/Status
> table (`:366-375`). It lives in the live-extractor family (`scripts/extract/`, beside
> extract_drc/lvs/rcx/route), not the dead-prototype family (`scripts/reports/`). "Producer with no
> invoker" is an UNSAFE deletion signal alone — check the *consumer* side. Phase 3's
> `auto_demote_on_regression` deletion was DEFERRED here: it is genuinely dead (zero callers incl.
> tests) but sits in hot-path `ab_runner.py`, unsafe to edit while the sky130hd campaign is live.

1. **Phase 1 — DROP dead leaves** (§2A, rev-2). Delete only the dead report builders + `.bak` files. (ML-dataset machinery is KEPT — no carve, no delete.) Update SKILL.md project-layout + Resource-Map + §16. ~390 LOC out. Lowest risk.
2. **Phase 2 — DEMOTE operator CLIs to `tools/`** (§2B). Move `eval_heuristics` + 4 forensics CLIs; fix imports/pointers. ~1,700 LOC relocated.
3. **Phase 3 — Vestigial cleanup** (§4). Delete `auto_demote_on_regression` + `_fetch_rows`; correct the `shadow`-lifecycle docs. Small, isolated.
4. **Phase 4 — M4 `config_safety`** — extract shared clamp/size-class/parser; wire both apply scripts. (Do M4 first of the merges: pure refactor, high safety value, no behavior change beyond *adding* the missing diagnose clamp.)
5. **Phase 5 — M2 learner consolidation** — shared trajectory scan, two outputs; keep two CLI verbs + Gate-A wiring.
6. **Phase 6 — M1 `knowledge update`** — fold ingest+learn+mine; preserve per-flow-ingest / batch-learn split + try/except.
7. **Phase 7 — M3 `ab` verb** — collapse enqueue+drain.
8. **Phase 8 — Wire `presynth.py` into ingest** (the Win-5 repair) so the feature actually fires per flow.
9. **Phase 9 — Docs** (§5): SKILL.md restructure + reference split/merge + CLAUDE.md updates (note this plan's hash + superseded invariants per the project's doc-update rule).

Each phase: run the flow on one nangate45 + one sky130hd design, **ingest**, confirm honesty fast-check, full `pytest`. Per CLAUDE.md, ingest after every validation flow so the loop sees the refactor's own runs. **rev-2 add:** also run `run_labels.sh` + `run_features.sh` on the nangate45 validation design and confirm the CSVs still emit + join (the DEF→dataset capability is now part of the green bar).

---

## 8. Impact summary (rev-2)

- **~390 LOC deleted** (dead report builders only) + **~1,700 LOC relocated** to `tools/` → smaller skill surface, **zero** core-loop/capability risk (every cut cross-checked against the spine map *and* the honesty tripwires). *(rev-1 claimed ~2,850 LOC deleted; rev-2 removes the ~2,870-LOC ML-dataset block from the DROP column because it is a kept capability — net deletion shrinks accordingly. The simplification value now comes mostly from the DEMOTE + loop-merge work, not raw deletion.)*
- **Learning loop: ~8 procedure steps → 4**, with one new enforced safety rail (M4) and one latent bug fixed (M2 trajectory staleness) and one capability repaired (Win-5 wiring).
- **SKILL.md 748 → ~330**, references de-duplicated, flagship contributions promoted to the first screen; the DEF→dataset capability surfaced in the capability triptych (now four).
- The two contributions, **four capabilities** (three closure + DEF→dataset), six PDKs, and all nine honesty invariants are **preserved and more visible**.

---

## Appendix — open items to verify during execution
- **A.** (rev-2: `presynth.py` is no longer relocated — it stays in `extract/features/`.) Before wiring it into ingest (Phase 8), confirm nothing else emits `reports/presynth_features.json` today (grep shows no flow-script producer; consumers degrade gracefully when absent).
- **B.** Before deleting each report builder, re-grep on the actual branch HEAD (some refs live only in `tools/` operator scripts outside the skill).
- **C.** After M1/M2, re-assert Gate-A enqueue still fires (`recipe_status` gains a candidate on a fresh fail) — the single most-regressed invariant historically.
- **D.** Decide `auto_demote_on_regression`: wire it (preferred — completes the documented lifecycle) vs delete it + the doc claim.
