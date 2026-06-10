# Fix-Learning Loop (self-evolution from violation-fixing iterations) — Design Spec

**Date:** 2026-06-05
**Skill:** `r2g-rtl2gds`
**Status:** Design approved (brainstorming); implementation plan executed.
**Implementation (2026-06-06, branch `feat/fix-learning-loop`):** Part A (mechanism, Tasks 0–15)
complete and TDD-green (full suite 373 passed / 8 skipped, +42 tests); Part B Task 16
(backfill + repair + first learn) run locally — 382 fix_events → 337 resolved + 45 abandoned
trajectories → 122 fix_recipes entries. Tasks 17–20 (pilot + corpus campaign, real EDA flows)
paused at the Task 18 go/no-go checkpoint. See the implementation log at the end of
`docs/superpowers/plans/2026-06-05-fix-learning-loop.md` for commit hashes and the 8 plan
divergences (e.g. T3 pre-fix category snapshot, T8 standalone `rank_proposals`, real `_batch`
record shapes, and the `repair_run_status` near-no-op finding).
**Authors:** user5 + agent discussion team (learning-loop mapper `acc45c02f5421d3c4`,
violation-fixing mapper `a498343376a94aa6b`, corpus surveyor `a580e2031dfae07d0`,
self-evolution-surface mapper `a784d714f324ac04f`), synthesized and source-verified by the
lead.

---

> **Note (2026-06-10, engineer-loop spec):** the knowledge store DB file `knowledge/runs.sqlite` was RENAMED to `knowledge/knowledge.sqlite` (decision-11 evidence/conclusions split: journal.sqlite = evidence, knowledge.sqlite = conclusions). References below to `runs.sqlite` are historical; the `runs` TABLE name is unchanged.


## 1. Goal & one-paragraph summary

Make the skill **learn from every violation-fixing iteration**. Today the flow *executes*
fix iterations (timing / DRC / LVS) and then *forgets* them: `fix_signoff.sh` already writes
a per-iteration `reports/fix_log.jsonl`, but **nothing reads it back to learn**. This feature
captures every iteration — **including failed attempts** — into a durable knowledge store,
aggregates them into per-family **fix recipes** (which strategy clears which violation-class,
and which strategies *don't*), and feeds an **evidence-ranked, priority-ordered list of next
moves** into the diagnosis layer so the next fix attempt starts from what has historically
worked. The skill thus turns its three precious populations — **success**, **failure**, and
**failure→success** — into a compounding asset. Application is "observe + suggest": the model
**re-orders** candidate strategies; it **never** overrides the hard safety clamps. The whole
mechanism replicates the proven **Fmax deterioration-model** pattern already in this codebase
(pure model module + cold-start defaults + threshold-gated activation + persisted as a
`heuristics.json` sub-key + online self-correction).

This is a **learning/recommendation** feature, not a new autonomous fixer. It makes the
*existing* fixers smarter and honest about what they've learned.

## 2. Key decisions (locked)

| # | Decision | Value |
|---|----------|-------|
| D1 | Foundation | **Approach 1** — replicate the Fmax `observe → learn → persist → apply` template (`fmax_model.py`). Reject extending `config_lineage` in place (low fidelity) and the full machine-writable-catalog service (Approach 3, deferred). |
| D2 | Record granularity | **Every iteration, including failed attempts.** A strategy that *failed* to clear violation-class Y is first-class training data (tells the model what not to try next). |
| D3 | Storage shape | **Three tiers**, all lossless & re-derivable. **Tier-1 `fix_events`** = append-only raw per-iteration log (every iteration incl. failures; **keep ALL rows**, never delete). **Tier-2 `fix_trajectories`** = per-episode path (ordered strategy sequence → outcome/verdict; the "fixing trajectory" you *read* before proposing). **Tier-3 `fix_recipes`** = per-family/per-platform aggregate persisted as a `heuristics.json` sub-key. "Resolved" is the Tier-2 `outcome` column, never a deletion. |
| D4 | Application | **Enumerate *all* applicable solutions** for the violation-class as a **priority-ranked candidate list** with evidence (clearance rate, `n`, expected reduction, cost) + rationale, designed for **fall-through** (try top → on no-improvement fall to next → each attempt writes a new record with outcome + verdict). Hard safety clamps stay **absolute**. |
| D5 | Survivorship | Aggregate over **all** events incl. **abandoned** sessions (violation never cleared) — so "no known strategy clears this" is learnable, not silently dropped. |
| D6 | Backfill + repair | Mine `design_cases/_batch/*.jsonl` (recover/retry/antenna/beol passes) into `fix_events`; **repair the 747 `orfs_status='partial'` dead rows** so `is_success` and the learner finally see the real corpus. |
| D7 | Timing capture | Add a thin **timing fix journal** to `check_timing.py` so the (currently un-logged, agent-driven) timing fixes become `fix_events` like DRC/LVS. |
| D8 | Fixing worklist | **Staged**: build mechanism (TDD) → backfill+repair → **pilot one-of-each** → checkpoint → fixing campaign. The *fixing* worklist = **retryable + violations** (28 RTL-include re-runs + ~5 PD-recovery + 4 timing / 1+7 DRC / 2 LVS). **Skip the 9 intractable BOOMs.** |
| D9 | Raw record detail | **Maximally detailed, lossless.** Keep **all** violation categories (full before/after category *vector*, not just the dominant one), rule-/net-/path-level detail where the tool emits it, the full config + env-flag + tool-version context, and per-stage metrics — **per design family per platform**. Plus a per-run **violation snapshot** for *every* run (incl. clean ones) so the DB holds the complete violation landscape, not only fix iterations. |
| D10 | Corpus enrichment | After the fixing campaign, **re-run ALL RTL designs** in the project through the now-instrumented flow (Phase F), in concurrency-safe waves, to populate the per-family/per-platform database broadly. Decided: **all designs** (the 9 intractable BOOMs are **time-boxed** — they record as `abandoned`/intractable negative data rather than burning unbounded compute). Honest caveat: already-clean re-runs yield **success baselines + violation snapshots**, not new fix recipes (recipes require designs that *have* violations). The Phase-D checkpoint is a **go/no-go on the compute spend**, not a scope question. |
| D11 | Autonomous manager | A `fix_log_manager` runs **automatically on ingest** (no manual learn step) and owns three jobs: (a) **merge similar experiences** into canonical records via a **config-normalized action key** = `(check, violation_class, strategy, normalized-config-signature)` — same knob with value within tolerance collapses; M2/M3 antenna and timing tiers stay distinct; (b) drive the **adaptive iteration budget**; (c) enforce the **DB-size policy**. Raw Tier-1 stays lossless; merging happens in the derived layer. |
| D12 | Adaptive iteration budget | `fix_signoff.sh` replaces the fixed `MAX_ITERS=3` with: **base 3; escalate while an iteration improves the count (but hasn't cleared) up to a hard cap of 8; stop early after 2 consecutive non-improving iterations.** Spends effort only where progress is real. |
| D13 | DB-size management | Always **bound the verbose detail blobs** (`rule_details_json` = top-N sample violations + a total count, never a full per-net dump). Keep all raw `fix_events` inline until a size threshold (row count / DB MB), then **auto-archive fully-merged old episodes** to `fix_events_archive` (recoverable; main/hot DB stays bounded). Merged canonical stats are always retained. Because the store is now **git-committed (D14)**, bounded blobs + the cold archive also keep the committed binary's churn reasonable. |
| D14 | Portability (shippable store) | The **complete knowledge store ships with the skill**: un-gitignore + git-track `knowledge/runs.sqlite`, `heuristics.json`, and `knowledge/fix_events_archive.sqlite`. A cloned/copied skill arrives **fully pre-trained** — the application layer reads `heuristics.json`; raw forensic detail + trajectories travel in the DBs. Learning is **path-independent** (recipes key on family/platform/check/violation_class/strategy; raw `project_path` is provenance only, harmless when stale). The design-dir `reports/fix_log.jsonl` is a transient write-ahead buffer, auto-ingested into the skill DB, safe to delete with the design. |

## 3. Source-verified facts this design rests on

Verified by the agent discussion team against the live skill in
`/proj/workarea/user5/agent-r2g/r2g-rtl2gds`. Load-bearing; revisit if these scripts change.

1. **`fix_signoff.sh` is the only scripted fix loop**, and it **already records the gold.**
   Its `for (it=1..MAX_ITERS)` loop (`scripts/flow/fix_signoff.sh`, default 3) calls
   `diagnose_signoff_fix.py --next` → `--apply <id>` → `run_orfs.sh` (optionally
   `FROM_STAGE`) → `run_drc.sh`/`run_lvs.sh` → extract → compare, and writes
   `reports/fix_log.jsonl` (one JSON line per iteration: `{check, iter, strategy, before,
   after, verdict, ts}`) flushed live, plus `reports/fix_summary.md`. **Nothing reads
   `fix_log.jsonl` back.** This is the primary capture source.
2. **Timing fixing is agent-driven with NO structured log.** `check_timing.py` writes
   `reports/timing_check.json` (tiers clean/minor/moderate/severe/unconstrained;
   `auto_fixable`; `suggested_clock_period = ceil((period+|WNS|+1.0)*2)/2`; numbered
   `options`), but the *fix* (edit SDC / `CORE_UTILIZATION`, re-run backend) is done by the
   agent per `SKILL.md` step 5b — and `timing_check.json` is **overwritten** on the next run.
   → timing transitions are lost unless we add a journal (D7).
3. **The Fmax deterioration model is the proven self-evolution template.**
   `scripts/reports/fmax_model.py` is a **pure, I/O-free** module: cold-start corpus
   defaults; threshold-gated activation (`N_MIN_FAMILY = 8`); `select_model()` returns
   `(None, provenance_str)` below threshold so callers never crash; persisted as a
   `slack_deterioration` sub-key inside each family entry of `knowledge/heuristics.json`;
   online self-correction on each `--verify`. `fmax_search.py` (orchestrator) supplies the
   I/O. **We replicate this shape exactly.**
4. **The learn loop already exists and is re-derivable.** `learn_heuristics.py` groups
   `runs` rows by `(design_family, platform)`, requires `MIN_SUCCESSFUL = 3`, computes
   per-family medians + `slack_deterioration`, and **re-writes** `heuristics.json` from
   scratch each run (so aggregation is naturally idempotent). `suggest_config.recommend()`
   applies the medians under hard safety clamps applied **last** (e.g.
   `PLACE_DENSITY_LB_ADDON >= 0.10`; `bus_heavy`/`macro_heavy`/`crypto` caps). We extend
   this with a third aggregation pass over `fix_events`.
5. **The signoff-fix strategy catalog is hardcoded, machine-applied at call time.**
   `diagnose_signoff_fix.py` holds `_drc_plan()`, `_lvs_plan()`, the antenna catalog, and
   `apply_edits()` (writes a marked `# >>> r2g signoff-fix (auto) >>>` block into
   `config.mk`). `analyze_execution.py` already returns a **ranked proposal list** (rule
   tables + BM25 over `failure-patterns.md` + `failure_candidates.json`) but never consults
   history for empirical clearance. These are our re-ranking hook points.
6. **Knowledge DB: schema-extensible, with dead data to repair.** Three tables
   (`runs`, `failure_events`, `config_lineage`); `knowledge_db.is_success` is the shared
   signoff-positive predicate; `_RUNS_ADDED_COLUMNS` is a forward-migration pattern
   (`ensure_schema` runs it). The live `runs.sqlite` has 750 runs but **747 have
   `orfs_status='partial'`** (dead-learning-loop residue) — `is_success` and the learner are
   effectively blind to the real corpus until repaired (D6).
7. **The failure→success gold is real but locked in batch jsonl, not the DB.**
   `design_cases/_batch/{recover_pass4,retry_pass3,retry_pass4,orfs_retry}.jsonl` record ~13
   route/place/cts recoveries; `antenna_fix_2026*.jsonl` record before→after antenna flips
   (eth_arb_mux 133→0, eth_demux 147→3, …); `beol_drc_2026*.jsonl` record ~30+ `clean_beol`
   flips. None of this is usefully in `runs.sqlite`. Backfill source for D6.
8. **DRC/LVS classification already produces the `violation_class` we key on.**
   `extract_drc.py` → per-category counts (dominant category, e.g. `*_ANTENNA`);
   `extract_lvs.py:classify_lvs_mismatch()` → `real_connectivity` / `symmetric_matcher` /
   `generic`. `run_lvs.sh` has the `LVS_CRASH_RETRIES` heisenbug-retry; `run_drc.sh` has the
   `clean_beol` (`DRC_BEOL_ONLY`/`DRC_BEOL_STRICT`) qualified-clean status. These define the
   violation-class taxonomy the fix recipes are bucketed by.
9. **Corpus populations (723 real projects).** ~600 fully clean (success); 41 never reached
   GDS (28 cheap RTL-`#include`/synth re-runs, ~5 PD-recovery, 9 intractable BOOMs);
   outstanding violations on GDS-reached designs: 4 timing (iccad2015 units), 1 real DRC fail
   (eth_demux) + 7 DRC-stuck, 2 real LVS defects (wb2axip_axi2axilite, wb2axip_axilsingle —
   the other ~17 LVS "fails" are `symmetric_matcher` tool noise).
10. **Tests are pure-discovery pytest with a seeded-DB fixture.** `tests/conftest.py` gives
    `tmp_knowledge_dir` (throwaway SQLite + `schema.sql` + `families.json`, runs
    `ensure_schema` so new migration columns are exercised automatically). Patterns to copy:
    `test_fmax_model.py` (pure model), `test_learn_heuristics.py` (seeded-DB learn). 357
    tests currently green.

## 4. Architecture

```
  OBSERVE / RECORD                 LEARN (re-derive, idempotent)        PROPOSE → REPAIR
  ────────────────                 ─────────────────────────────       ────────────────
  fix_signoff.sh ─┐                                              diagnose_signoff_fix.py --list
   fix_log.jsonl  │  ingest_run.py   learn_heuristics.py          ENUMERATE ALL applicable
  check_timing.py ┼► +fix_events  ─► +fix_trajectories         ─► solutions for this
   timing journal │  +run_violations  +fix_recipes                violation_class, priority-
  _batch/*.jsonl ─┘     │                  │                       ranked w/ evidence
   (backfill once)      ▼                  ▼                              │
              ╔═══════════════╗   ╔══════════════════╗                   ▼
     TIER 1   ║  fix_events   ║   ║ fix_trajectories ║  TIER 2   fix_signoff.sh / agent
     raw,     ║  every iter,  ║─► ║ per-episode path ║           try top candidate
     lossless,║  incl. fails, ║   ║ → outcome+verdict║           → no-improve? fall through
     keep ALL ║  per fam/plat ║   ║ resolved/abandon ║                  │
              ╚═══════╤═══════╝   ╚════════╤═════════╝                  │  NEW record
                      │                    ▼                           │  (outcome+verdict)
                      │            ╔══════════════════╗                │
                      │   TIER 3   ║   fix_recipes    ║                │
                      │            ║  per fam/plat/   ║                │
                      │            ║  check/viol_class║                │
                      │            ║  → strategy stats║                │
                      │            ║ (attempts/wins/  ║                │
                      │            ║  FAILURES/Δviol) ║                │
                      │            ╚══════════════════╝                │
                      └────────────◄── new fix_events written back ────┘
   build_lineage_view.py ◄── fix-effectiveness projection (per fam/plat/viol_class)
   eval_heuristics.py    ◄── A/B arm: empirical-ranked vs static-catalog order
```

**The episode (the primitive).** A `fix_session_id` groups all iterations of one fixing
campaign for one design + one `check_type`. Within a session, iterations are
`tried strategy A (verdict) → B (verdict) → … → cleared|abandoned`. The session **resolves**
if any iteration's verdict is `cleared`; otherwise it is **abandoned** (still aggregated —
D5). All raw iterations are kept; resolution is a query (`fix_sessions_v`), never a delete.

**Why this honors the user's "delete on success, keep the experience" intent.** The *raw
trail* stays (auditable, re-aggregatable), but the **durable experience** lives in the Tier-2
aggregate, which keeps both the **winning strategy** *and* the **failure counts** of the
strategies that didn't work — so we never re-recommend a known-bad move, and never lose what
the episode taught. "Clean DB" is the `resolved=1` view, not destruction.

## 5. Data model (three tiers)

**The loop is read → propose → repair → record:** read the raw records (Tier 1) and the
per-episode trajectories (Tier 2), **propose all** candidate solutions from the aggregated
recipes (Tier 3), execute the repair, and write new raw records (outcomes + verdicts) back to
Tier 1 — which re-derives Tiers 2 and 3.

### 5.1 Tier-1 `fix_events` (append-only raw)

New table in `knowledge/schema.sql`; created via `ensure_schema` (no rebuild). One row per
fix **iteration**.

| Column | Meaning |
|--------|---------|
| `fix_event_id` INTEGER PK | autoincrement |
| `fix_session_id` TEXT | episode key = sha1(`project_path` + `check_type` + first `violation_class` + session-start ts); supplied by the fixer, stable across re-ingest |
| `project_path`, `design_name`, `design_family`, `platform` | identity (family/platform via the same resolver `learn_heuristics` uses) |
| `check_type` TEXT | `timing` \| `drc` \| `lvs` |
| `violation_class` TEXT | DRC: dominant category (`*_ANTENNA`, …); LVS: `real_connectivity`/`symmetric_matcher`/`generic`; timing: tier (`minor`/`moderate`/`severe`) |
| `iter` INTEGER | iteration index within the session |
| `strategy` TEXT | applied strategy id (`antenna_diode_repair`, `antenna_density_relief`, `period_relax`, `util_reduce`, `lvs_macro_cdl`, …) |
| `from_stage` TEXT | ORFS rerun-from stage (`route`/`floorplan`/…) |
| `before_count`, `after_count` REAL | total violation count; timing uses `|WNS|` (or TNS) as the metric |
| `before_categories_json`, `after_categories_json` TEXT | **full category vector** before/after (every DRC category & count, every LVS mismatch bucket, every timing endpoint group) — D9: keep ALL categories, not just the dominant one |
| `rule_details_json` TEXT | tool-level specifics where emitted: DRC rule names + sample coordinates (from the lyrdb), LVS unmatched-net / device-mismatch names (from the lvsdb), timing failing-path endpoints + slack. **Bounded: top-N sample entries + a total count — never a full per-net dump (D13).** |
| `before_status`, `after_status` TEXT | e.g. `violations`→`clean`, `fail`→`clean_beol`, tier→tier |
| `verdict` TEXT | `cleared` \| `win` (improved, not zero) \| `no_change` \| `regression` \| `inconclusive` |
| `config_delta_json` TEXT | the config.mk edit applied this iteration |
| `cumulative_config_json` TEXT | full applied-fix block snapshot at this iter (credit assignment for stacked fixes) |
| `env_flags_json` TEXT | active escape-hatches (`PLACE_FAST`, `ROUTE_FAST`, `SKIP_ANTENNA_REPAIR`, `MAX_REPAIR_ANTENNAS_ITER_*`, `LVS_CRASH_RETRIES`, …) |
| `tool_versions_json` TEXT | OpenROAD / KLayout / Yosys / ORFS commit — so a recipe is reproducible and tool-bumps are detectable |
| `stage_metrics_json` TEXT | per-stage slacks (floorplan/place/finish), cell_count, area, power, IR at this iter |
| `stacked` INTEGER | 1 if prior iterations' edits were still in effect (fix depended on a stack) |
| `elapsed_s` REAL, `ts` TEXT | cost + timestamp |
| `provenance` TEXT | `live` \| `backfill:<source-file>` |

**Lossless principle (D9).** The raw row is the *system of record* — capture everything the
tools emit, even fields no current query uses; aggregates are re-derivable, but un-captured
detail is gone forever. The `*_categories_json` / `rule_details_json` blobs mean any future
slice (per-rule, per-net, per-action, per-outcome, per-family, per-platform) is
reconstructable without a re-run.

**Idempotency:** `UNIQUE(fix_session_id, iter, strategy)` with `INSERT OR IGNORE`. Because
Tiers 2 and 3 are re-derived from scratch each learn, double-ingest is harmless.

### 5.2 Tier-2 `fix_trajectories` (per-episode path — “read the trajectory”)

One materialized row per `fix_session_id` — re-derivable from Tier-1, but promoted to a
first-class tier so the repair loop and reviewers can **read the fixing trajectory** before
proposing the next move.

| Column | Meaning |
|--------|---------|
| `fix_session_id` TEXT PK | the episode |
| `project_path`, `design_name`, `design_family`, `platform` | identity |
| `check_type`, `violation_class` | what was being fixed |
| `path_json` TEXT | ordered `[{iter, strategy, before, after, verdict}, …]` — the full trajectory |
| `n_iters` INTEGER | trajectory length |
| `outcome` TEXT | `resolved` \| `abandoned` (abandoned = no `cleared` iteration — still kept, D5) |
| `winning_strategy` TEXT | strategy of the clearing iteration (NULL if abandoned) |
| `winning_config_json` TEXT | cumulative config at clearance (credit assignment) |
| `failed_strategies_json` TEXT | strategies that did **not** clear — the negative signal |
| `initial_count`, `final_count` REAL, `total_elapsed_s` REAL | episode summary |

Re-derived by `learn_heuristics.py` by grouping Tier-1 rows per session. No deletion;
“resolved” is this `outcome` column.

### 5.3 Tier-3 `fix_recipes` (re-derivable aggregate)

Persisted as a sub-key under each family entry in `knowledge/heuristics.json` (mirrors
`slack_deterioration`), computed by `learn_heuristics.py`:

```json
"fix_recipes": {
  "<check_type>": {
    "<violation_class>": {
      "strategies": [
        {"strategy": "antenna_diode_repair",
         "attempts": 11, "successes": 9, "failures": 2,
         "clearance_rate": 0.818, "clearance_lb": 0.55,   // Wilson/Beta lower bound
         "median_reduction_pct": 0.97, "median_elapsed_s": 540,
         "last_win_session": "<sha1>"}
      ],
      "n_sessions": 7, "provenance": "family(n=7) | platform(n=..) | default-static"
    }
  }
}
```

Aggregated over **all** `fix_events` for the family (resolved **and** abandoned — D5), so
`failures` is honest. Smoothed clearance (`Beta(1,1)` → `(successes+1)/(attempts+2)`, plus a
Wilson lower bound) so one failure never permanently blacklists a strategy and untried
strategies stay explorable.

### 5.4 Per-run `run_violations` snapshot (D9 — the complete landscape)

Fix recipes only come from designs that *had* violations. But the user wants the database to
hold **all** violation categories per family/platform — including the profile of designs that
came out clean. So `ingest_run.py` also writes a lightweight **per-run** snapshot (one row per
ingested run, keyed `(run_id, design_family, platform)`): the full `drc.json` /
`lvs.json` / `timing_check.json` category vectors and final statuses — even when empty
(clean). This is what the **full-corpus enrichment** (D10, Phase F) populates broadly: every
re-run contributes its violation landscape, giving per-family/per-platform answers to "what
violation categories does this family tend to exhibit, and at what rate?" independent of
whether a fix was attempted. Cheap (one row/run), append-only, re-derivable into the lineage
view.

### 5.5 Autonomous fix-log manager + size management (D11–D13)

`fix_log_manager.py` is the autonomous plumbing around the learning loop (the *model*
logic stays in `fix_model.py`). It does three things:

1. **Merge similar experiences (D11).** `canonical_action_key(event)` normalizes an
   iteration into `(check_type, violation_class, strategy, config-signature)`, where the
   config-signature buckets each edited knob's numeric value to a tolerance (default ±15%).
   So `antenna_density_relief @ util=15` and `@ util=14` collapse to **one** canonical
   experience, while `util=15` vs `util=5` (beyond tolerance) stay distinct, and M2 vs M3
   antenna stay distinct (different `violation_class`). The recipe aggregation (§5.3) groups
   by this key, so near-duplicate retries don't inflate the counts and "similar experience"
   becomes one merged record with `occurrences` + aggregated before/after stats.
2. **Run automatically on ingest (D11).** `ingest_run.py` calls `fix_log_manager.manage()`
   at the end of ingest (env `R2G_FIX_AUTOLEARN=1`, default on): re-derive Tier-2/Tier-3
   (idempotent) + enforce the size policy. For large batch waves, set `R2G_FIX_AUTOLEARN=0`
   and run `manage()` once per wave (escape hatch for scale). This is the "more autonomous"
   manager — no manual `learn_heuristics.py` step required in the common path.
3. **Enforce the DB-size policy (D13).** `bound_rule_details(details, top_n=20)` caps the
   verbose blob at write time. `archive_merged(conn)` triggers only past a threshold
   (`FIX_EVENTS_MAX_ROWS`, `DB_MAX_MB`): it moves raw rows of **fully-merged, resolved/old**
   episodes (those already folded into a Tier-2 trajectory and beyond the most-recent K per
   canonical action) into `fix_events_archive` (identical columns), then `VACUUM`s. Raw is
   **archived, not deleted** (honors "keep all raw"); merged canonical stats are untouched
   because Tier-2/Tier-3 were derived before archival. A `manage()` size report
   (`rows`, `db_mb`, `archived`) is logged.

**Adaptive iteration budget (D12)** lives in `fix_signoff.sh`: the loop counter is no longer
a fixed 3. It runs at least `BASE_ITERS=3`; after each iteration, if the count **improved**
(dropped) but isn't 0, it grants another iteration up to `MAX_ITERS_CAP=8`; it **stops early**
after 2 consecutive iterations with no improvement (the existing `no_improvement` verdict).
Diverged/timed-out probes never extend the budget.

### 5.6 Portability — the skill ships pre-trained (D14)

The knowledge store is the skill's memory and must travel with it. Today `.gitignore`
(lines 242–243) excludes `runs.sqlite` + `heuristics.json`, and `design_cases/` (212), so a
git-shipped skill would arrive empty with nothing to regenerate from. We fix that:

- **Track the full store in git:** `knowledge/runs.sqlite` (runs + fix_events + the
  never-archived `fix_trajectories` + run_violations), `knowledge/heuristics.json` (the applied
  recipes/medians), and `knowledge/fix_events_archive.sqlite` (cold raw detail). A clone/copy is
  fully pre-trained.
- **Application needs only `heuristics.json`** (path-independent text): `suggest_config` and the
  new `diagnose`/`analyze` ranking read it, so even an empty DB applies the learned experience.
  The DBs add the ability to keep *learning* and to inspect raw detail.
- **Path-independence:** recipes/trajectories key on `(family, platform, check, violation_class,
  strategy)`. Raw `project_path` is provenance; stale paths after a ship are harmless.
- **Transient design-dir buffer:** `reports/fix_log.jsonl` lives in the design dir only until
  `manage()` ingests it into the skill DB (right after each fix run). After that the design — and
  its log — can be deleted or shipped away without losing experience.
- **Size discipline matters more now** (committed binary): D13's bounded blobs + cold archive keep
  `runs.sqlite` lean; commit the DB at learning milestones (post-campaign), not every micro-run.

## 6. Components

| Action | File | What changes |
|--------|------|--------------|
| **NEW** pure model | `r2g-rtl2gds/scripts/reports/fix_model.py` | `rank_strategies(recipe_entry, check, violation_class, period, static_order) -> [(strategy, score, evidence, rationale)]`. `N_MIN` gate (mirror `N_MIN_FAMILY=8`); below → cold-start = static catalog order; graceful `provenance` return; **negative learning** (down-rank high-failure, keep explorable); zero I/O. |
| **NEW** ingester | extend `knowledge/ingest_run.py` | read `reports/fix_log.jsonl` → `fix_events` rows (idempotent); **also** write the per-run `run_violations` snapshot (§5.3) for every run. Resolve family/platform like existing ingest. |
| **NEW** timing journal | extend `scripts/reports/check_timing.py` | `--journal <before.json> <after.json>` appends a `check=timing` line to `fix_log.jsonl` (called by the agent / a thin re-run wrapper after a timing fix). Auto-detect period/util delta. |
| **NEW** learn pass | extend `knowledge/learn_heuristics.py` | re-derive **Tier-2 `fix_trajectories`** (per-episode) and **Tier-3 `fix_recipes`** (aggregate in `heuristics.json`) from `fix_events`, both from scratch (idempotent). |
| **NEW** backfill | `knowledge/backfill_fix_events.py` | parse `design_cases/_batch/{recover,retry,antenna_fix,beol_drc}*.jsonl` + per-design `reports/fix_log.jsonl` → `fix_events` (provenance=`backfill:<file>`). |
| **NEW** DB repair | `knowledge/repair_run_status.py` (or `ingest_run.py --reconcile`) | re-derive `orfs_status` + signoff statuses for the 747 `partial` rows from each design's `reports/*.json`. Backs up `runs.sqlite` first; read-from-reports only; reversible. |
| **EDIT** application | `knowledge/diagnose_signoff_fix.py` | `_drc_plan`/`_lvs_plan` consult `fix_model.rank_strategies` to **enumerate ALL applicable solutions**, priority-ranked w/ evidence; `diagnose --list` returns the full set (D4), `--next` returns the top-unused for fall-through. **Safety clamps unchanged & absolute.** |
| **EDIT** application | `knowledge/analyze_execution.py` | rank backend-stage proposals by historical clearance after the existing rule/BM25 proposals. |
| **NEW** manager | `r2g-rtl2gds/knowledge/fix_log_manager.py` | autonomous merge (`canonical_action_key`, config-normalized), auto-run `manage()` (re-learn + size guard), `bound_rule_details`, `archive_merged`. (D11/D13) |
| **NEW** archive table | `knowledge/schema.sql` `fix_events_archive` | same columns as `fix_events`; holds raw rows evicted past the size threshold (recoverable). |
| **EDIT** loop | `scripts/flow/fix_signoff.sh` | emit richer `fix_log.jsonl` (add `violation_class`, `from_stage`, `cumulative_config`, `fix_session_id`, `elapsed`); fall through the ranked list on no-improvement; **adaptive budget** base 3 → cap 8, stop after 2 non-improving (D12). |
| **EDIT** observability | `scripts/reports/build_lineage_view.py` | add a read-only `fix_effectiveness` projection (per violation_class: strategy clearance rates + n + median reduction) → dashboard panel. |
| **EDIT** eval | `knowledge/eval_heuristics.py` | add an arm comparing **empirical-ranked vs static-catalog** fix ordering on payoff (violations cleared, honest wall-clock). |
| **EDIT** mine | `knowledge/mine_rules.py` | emit **evidence-backed, ranked** fix candidates into `failure_candidates.json` for human promotion into `failure-patterns.md` (stays human-curated). |
| **EDIT** docs | `SKILL.md`, `references/signoff-fixing.md`, `references/orfs-playbook.md`, `knowledge/README.md` | document the fix-learning step, schema, ranked-candidate flow, backfill. |

## 7. Data flow

1. **During fixing** — `fix_signoff.sh` (DRC/LVS) and the agent's timing fix (via
   `check_timing.py --journal`) append iterations to `reports/fix_log.jsonl`, with the
   `fix_session_id` minted at session start.
2. **Diagnose** — `diagnose_signoff_fix.py --next/--list` loads `heuristics.json.fix_recipes`,
   calls `fix_model.rank_strategies`, and returns the priority-ranked candidate(s); the loop
   tries the top and **falls through** on `no_change`/`regression` (each a new negative
   `fix_event`).
3. **Ingest** (step 10, per flow) — `ingest_run.py` reads `fix_log.jsonl` → `fix_events`
   (idempotent).
4. **Learn** — `learn_heuristics.py` re-derives `fix_recipes` from all `fix_events`.
5. **Observe** — `build_lineage_view.py` projects fix-effectiveness to the dashboard;
   `eval_heuristics.py` measures payoff of ranked vs static ordering.
6. **Backfill+repair** (one-time) — `backfill_fix_events.py` seeds history;
   `repair_run_status.py` revives the dead rows; re-learn.

## 8. Honesty & provenance labels

- Every ranked candidate carries `evidence: clearance=<lb>..<rate> (n=<sessions>), Δviol=<%>,
  ~cost=<s>` and `provenance: family(n) | platform(n) | default-static`.
- `cold-start` when below `N_MIN` (ranking == static catalog order, explicitly labeled).
- `backfill` rows are marked in `provenance` so a learned recipe discloses how much rests on
  historical (vs live) evidence.
- A violation-class with **only abandoned sessions** is reported as
  `no-known-fix (n abandoned)` rather than omitted (D5).

## 9. Testing strategy (TDD)

New tests in `r2g-rtl2gds/tests/` (flat, `test_*.py`, using `tmp_knowledge_dir`):

- `test_fix_model.py` — pure: cold-start fallback below `N_MIN`; ranking by smoothed
  clearance; **negative learning** (failed strategy down-ranked but not zeroed); untried
  strategy stays explorable; regression penalized harder than no_change; provenance strings.
- `test_ingest_fix_events.py` — `fix_log.jsonl` → `fix_events`; **idempotent re-ingest** (no
  dup via UNIQUE); timing-journal line ingested; `fix_sessions_v` resolves correctly.
- `test_learn_fix.py` — seeded `fix_events` → `fix_trajectories` + `fix_recipes`; **abandoned
  sessions counted** (survivorship); trajectory `outcome`/`winning_strategy` correctness;
  re-derive-from-scratch equality after double ingest; threshold gating.
- `test_backfill_fix_events.py` — fixtures of each `_batch/*.jsonl` shape → expected
  `fix_events`.
- `test_repair_run_status.py` — `partial` rows reconciled from report fixtures; backup made;
  `is_success` flips on correctly.
- Extend `test_diagnose_signoff_fix.py` / `test_analyze_execution.py` — ranked output shape;
  **safety clamps preserved** (PLACE_DENSITY_LB_ADDON ≥ 0.10 still wins over any recipe).
- `test_fix_log_manager.py` — `canonical_action_key` merges within-tolerance config variants
  but keeps M2/M3 antenna distinct; `bound_rule_details` caps at top-N + count; `manage()`
  re-learns idempotently; `archive_merged` moves only fully-merged old episodes and never
  loses a Tier-2 trajectory or a Tier-3 count.
- `test_fix_signoff_adaptive.py` — budget escalates while improving (up to cap 8), stops after
  2 non-improving iterations, never below base 3.
- Golden gate: full suite stays green (357 → +N). Run the existing byte-stable regression on
  at least one nangate45 design (aes_core/cordic) to confirm no behavior drift in unchanged
  paths.

## 10. Staged campaign (D8)

- **Phase A — build mechanism (TDD):** schema + ingest + `fix_model` + learn pass +
  application re-ranking + observability/eval + docs. Suite green.
- **Phase B — backfill + repair:** run `backfill_fix_events.py` and `repair_run_status.py`;
  re-learn; sanity-check `fix_recipes` populated and the dashboard fix-effectiveness panel
  renders.
- **Phase C — pilot one-of-each** (proves capture→learn→improved-suggestion end-to-end):
  - synth-`#include` re-run (e.g. `darkriscv_core`)
  - timing fix — `iccad2015_unit16_in1` (worst, WNS −4.51)
  - DRC fix — `verilog_ethernet_eth_demux` (real fail, 3 viol) or a DRC-stuck design
  - LVS triage — `wb2axip_axi2axilite` (`real_connectivity`, a genuine defect)
  After the pilot, confirm `fix_events` recorded the iterations and `diagnose --list` reflects
  the new evidence.
- **Phase D — checkpoint with user.** **Go/no-go on the Phase-F compute spend** + wave sizing.
  (Scope is decided — all RTL designs — so this is a launch gate, not a scope question.)
- **Phase E — fixing campaign:** 28 RTL re-runs + ~5 PD-recovery + remaining 4 timing / 1+7
  DRC / 2 LVS. **Skip the 9 intractable BOOMs.** Honor hard rules (FLOW_VARIANT isolation; no
  parallel LVS on >100K-cell designs). This is the learning-dense work — it generates the
  fix recipes.
- **Phase F — full-corpus enrichment (D10):** re-run **all RTL designs** through the
  now-instrumented flow in **concurrency-safe waves** (size-bucketed; serialize/≤2 LVS for
  >100K-cell; the 9 intractable BOOMs **time-boxed** → recorded as `abandoned` negative data,
  not unbounded compute), populating `fix_events` (where violations arise) and the
  `run_violations` snapshot (every run) per family/platform. Honest accounting: clean
  re-runs add **baselines + violation snapshots**, not recipes. Each wave ends with
  ingest → re-learn → lineage-view refresh so the database compounds as it runs. Cost is
  large and explicit — gated on the Phase-D go/no-go.

## 11. Risks & mitigations

| # | Risk | Mitigation |
|---|------|-----------|
| R1 | Historical `fix_log.jsonl` sparse → backfill leans on `_batch/*.jsonl` | parse both; accept partial coverage; label `provenance`; recipes disclose `n`. |
| R2 | Low-`n` noisy recipe recommends a bad strategy | `N_MIN` gate + smoothed/Wilson-LB ranking + **safety clamps absolute** + `eval_heuristics` measures real payoff before trust. |
| R3 | Credit misassignment in stacked fixes | record `cumulative_config_json` + `stacked` flag; aggregate winner with its stack; documented limitation (no per-knob attribution in v1). |
| R4 | DB repair mis-derives status | back up `runs.sqlite` first; read-from-reports only; reversible; validate against a known-good clean/fail subset before committing. |
| R5 | Timing journal easily forgotten (agent-driven) | `check_timing.py` auto-emits the journal when it detects a period/util delta between consecutive runs; `SKILL.md` hard instruction as backstop. |
| R6 | Re-ingest double-counts | `UNIQUE(fix_session_id, iter, strategy)` + Tier-2 re-derived from scratch. |
| R7 | Campaign concurrency hazards | reuse `FLOW_VARIANT` basename isolation; obey the >100K-cell single-LVS rule; size-adaptive parallelism. |
| R8 | Over-merging hides a real difference | config-normalized key keeps distinct knobs/values and distinct `violation_class` separate; tolerance is conservative (±15%) and tunable; raw is preserved so a finer re-aggregation is always possible. |
| R9 | Adaptive budget runs away on a slowly-"improving" hard case | hard cap 8; "improvement" must be a real count drop; 2-strike early-stop; diverged/timeout never extends. Per-design worst case is bounded and logged. |
| R10 | Archival corrupts learning or loses raw | archive only **after** Tier-2/Tier-3 are derived; move (not delete) to `fix_events_archive`; archived rows excluded from re-learn but recoverable; `manage()` reports counts; a backup precedes the first archival run. |

## 12. Out of scope (v1) / follow-ups

- **Approach 3** (machine-writable strategy catalog `diagnose_signoff_fix.py` loads at
  runtime; auto-promotion into `failure-patterns.md`). v1 keeps the catalog code-defined and
  the references human-curated, fed by ranked candidates.
- **RTL/SDC-edit fix capture** beyond config/flow deltas — RTL changes are noted, not
  auto-diffed.
- **Cross-family transfer** of recipes; multi-clock / CDC / DFT fixing.
- Re-attempting the 9 intractable BOOM ChipTops.
