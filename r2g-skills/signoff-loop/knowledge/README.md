# signoff-loop Knowledge Store

This directory is the skill's cross-run memory. It is **not** a cache — it is
the input to `suggest_config.py` and `failure-patterns.md` review.

## Layout

| File | Producer | Consumer |
|---|---|---|
| `schema.sql` | hand-edited | `knowledge/knowledge_db.py` at `ensure_schema` time |
| `families.json` | hand-edited seed; append as new designs ship | `knowledge/knowledge_db.py::infer_family` |
| `knowledge.sqlite` | `knowledge/ingest_run.py` (one row per ingested run) | `learn_heuristics.py`, `mine_rules.py`, `query_knowledge.py` |
| `heuristics.json` | `knowledge/learn_heuristics.py` | `suggest_config.py`, agent, dashboard |
| `failure_candidates.json` | `knowledge/mine_rules.py` | human reviewer → `references/failure-patterns.md` |
| `fix_events_archive.sqlite` | `fix_log_manager.py` (cold archive of raw `fix_events`) | retained-only (learning never reads it; Tier-2 survives archival) |

The store (`knowledge.sqlite` + `heuristics.json`, plus `fix_events_archive.sqlite` once created)
is **tracked in git**, so the skill ships pre-trained with its accumulated experience.

## Loop

```
              (run the flow)
                   │
                   ▼
     reports/*.json, stage_log.jsonl, diagnosis.json
                   │
      ingest_run.py │
                   ▼
              knowledge.sqlite ──► learn_heuristics.py ──► heuristics.json
                       │                                     │
                       │                                     └──► suggest_config.py
                       │
                       └─────► mine_rules.py ──► failure_candidates.json
                                                    │
                                                    └──► (human review) ──► failure-patterns.md
```

**The loop is live (2026-06-04).** A run becomes a learnable "success" via the shared
`knowledge_db.is_success(row)` predicate: a strict 6-stage ORFS pass, OR a run that reached a
final signed-off layout — at least one *positive* clean signoff (LVS `clean` / `symmetric_matcher`,
DRC `clean` / `clean_beol`, or RCX `complete`) and no *failed* signoff. This admits the large
population of runs whose `stage_log.jsonl` is incomplete (so `orfs_status` stayed `partial`) but
which produced a clean GDS — without that, `heuristics.json` was empty (0/750 runs were `pass`).
The fix lives in the **learner**, not ingest, so `orfs_status` stays a faithful record of the
stage log. `learn_heuristics.py`, `monitor_health.py`, the dashboard health strip, and the payoff
harness all import this one predicate, so they can never disagree.

## Extended Pipeline (OpenSpace-Inspired)

Four modules extend the base pipeline with config evolution tracking,
health monitoring, semantic failure search, and automated fix proposals:

```
knowledge.sqlite ──► monitor_health.py ──► health alerts (degradation detection)
     │
     └──► config_lineage table (populated by ingest_run.py on config changes)
              │
              └──► analyze_execution.py ──► fix_proposals.json (review queue)
                        ▲
failure-patterns.md ──► search_failures.py (BM25 index)
failure_candidates.json ─┘
```

| File | Producer | Consumer |
|---|---|---|
| `config_lineage` table | `ingest_run.py` (on config diff between runs) | `analyze_execution.py`, agent |
| `monitor_health.py` | reads `knowledge.sqlite` | agent (degradation alerts) |
| `search_failures.py` | indexes `failure-patterns.md` + `failure_candidates.json`; `lessons_for_symptom()` parses `r2g-lesson` front-matter | `analyze_execution.py`; **`diagnose_signoff_fix.py` decision path** (surfaces the matching active prose lesson at fix time) |
| `symptom.py` | pure `{check,class,predicates}` → `symptom_id` | `ingest_run.py`, `learn_heuristics.py`, `diagnose_signoff_fix.py` (the universal repair-experience index; family-name is never a key) |
| `sync_lessons.py` | one-way prose → `lessons` table (front-matter + evidence backfill) | `fix_log_manager.manage()` post-ingest; dashboard/agent |
| `analyze_execution.py` | reads project artifacts + search results | agent (fix proposal review queue) |
| `build_lineage_view.py` | read-only (`mode=ro`) projection over `knowledge.sqlite` + `config_lineage` + `heuristics.json` | dashboard "Knowledge health" + "Tuning provenance" panels |
| `eval_set.json` + `eval_heuristics.py` | frozen eval set; `emit` writes paired naive/learned arms via `suggest_config --no-learned`, `summarize` → `eval_results.jsonl` / `eval_summary.json` | operator (payoff A/B verdict) |

## Fix-Learning Loop (spec 2026-06-05)

Captures every DRC/LVS/timing fix iteration losslessly and replays it as evidence-ranked
strategy ordering on the next similar violation. Three lossless tiers + a per-run snapshot:

```
reports/fix_log.jsonl ─ ingest_run.py ─► fix_events (Tier-1, append-only raw)
                      └─ ingest_run.py ─► run_violations (snapshot for EVERY run)
                                              │
                       learn_heuristics.py ───┤ (idempotent full rebuild)
                                              ├─► fix_trajectories (Tier-2, per-episode)
                                              └─► heuristics.json:.fix_recipes (Tier-3)
                                                            │
                                                            └─► diagnose_signoff_fix.py (ranking)
```

| Table | Tier | Grain | Notes |
|---|---|---|---|
| `fix_events` | 1 | one row per fix iteration | append-only system of record; keyed `(fix_session_id, iter, strategy)`; carries before/after counts + category vectors, verdict, config delta + cumulative snapshot, env/tool versions, `provenance` (`live`/`backfill:<source>`) |
| `fix_trajectories` | 2 | one row per episode | `outcome` ∈ `resolved`/`abandoned`, `winning_strategy`, `failed_strategies_json`, ordered `path_json`. **Materialized** (idempotent rebuild) — **never archived**, so learning survives raw archival |
| `run_violations` | — | one row per run (incl. clean) | the full violation landscape: drc/lvs status + category/mismatch vectors, timing tier, WNS |
| `fix_events_archive` | 1 (cold) | same columns as `fix_events` | raw rows evicted past a size threshold by `fix_log_manager.archive_old_raw`/`manage`; written to the sidecar `fix_events_archive.sqlite` |

**heuristics.json `fix_recipes` sub-key** (Tier-3 aggregate, folded by `learn_heuristics.py`):

```jsonc
families[FAM]["platforms"][PLAT]["fix_recipes"][check][violation_class] = {
  "strategies": { "<sid>": {"attempts": N, "successes": S, "failures": F,
                            "median_reduction_pct": P /* optional */} },
  "n_sessions": N   // includes abandoned episodes — failures are counted
}
```

`fix_recipes` derive from Tier-2 `fix_trajectories` (NOT raw `fix_events`) — this is exactly
why archiving raw `fix_events` loses no learning signal. `diagnose_signoff_fix.py` reorders the
strategy catalog by `fix_model.py`'s Beta(1,1) clearance score `(successes+1)/(attempts+2)`:
untried → 0.5 prior, winners high, losers down-ranked but never zeroed/blacklisted. There is
**no hard gate** — all real-fix strategies stay proposed, priority-ordered. See
`references/signoff-fixing.md` ("Fix-Learning Loop").

**Ingest auto-learn.** After a CLI ingest, `ingest_run.py` auto-invokes `fix_log_manager.manage()`
(env `R2G_FIX_AUTOLEARN`, default on; failures warn but never break the ingest).

### Backfill & repair

```bash
# Mine historical batch logs into synthetic fix_events (provenance "backfill:<filestem>"; idempotent)
python3 knowledge/backfill_fix_events.py --batch-dir design_cases/_batch --db knowledge/knowledge.sqlite

# Reconcile orfs_status from per-project backend stage logs (backs up to <db>.bak first; idempotent)
python3 knowledge/repair_run_status.py --db knowledge/knowledge.sqlite
```

`backfill_fix_events.py` maps `antenna_fix_*`/`beol_drc_*` → `check=drc` and
`retry_pass*`/`recover_pass*`/`orfs_retry` → `check=orfs` (`violation_class` from the stage).
`repair_run_status.py` prints a before/after `orfs_status` histogram and reconciles the corpus.

> **2026-06-12 fix.** `repair_run_status.py` *used* to be "largely a no-op" — but that was the
> symptom of a bug, not correct behaviour. `run_orfs.sh` writes the **integer shell exit code**
> to `stage_log.jsonl` (`{"status": 0}`), while `ingest_run._derive_orfs_status` compared it to
> the strings `"pass"`/`"fail"`. `0 == "pass"` is always False, so every stage was skipped and
> **all 929 runs were classified `partial`** (0 `pass`, 0 `fail`) — which in turn suppressed the
> `orfs-fail-<stage>` `failure_events` (gated on `orfs_status=='fail'`), so backend aborts
> (PPL-0024, PDN-0185, placer SIGSEGV) left no learnable trace. The tests passed because their
> fixtures used the *string* form the writer never emits. Fix: `_norm_stage_status` accepts both
> int exit codes and strings; the repair then correctly reconciled the corpus to **872 pass / 40
> partial / 17 fail**. ORFS aborts now also record a `failure_events` row whose signature carries
> the tool's own error code (e.g. `orfs-fail-place-PPL-0024`) with the error line as `detail`.

> **2026-06-13 follow-on (commit pending).** The note above was only half-true: ORFS aborts
> record a `failure_events` row on the **live ingest path** (`ingest_run.py`), but
> `repair_run_status.py` reconciled `orfs_status` with a direct SQL `UPDATE` and never touched
> `failure_events`. So the 17 rows it flipped `partial→fail` stayed invisible in the table the
> learner / escalation / `search_failures` actually read — a dual-write consistency gap. Fix:
> `repair_run_status._reconcile_orfs_failure_event` now maintains the `orfs-fail-<stage>` event
> in lock-step with the reconciled status (idempotent; owns only `orfs-fail-%` signatures, leaving
> diagnosis events like `synthesis_errors` intact). It backfills already-flipped historical rows
> even when the status is unchanged, and works from the `runs` columns alone when the project dir
> is gone (bare `orfs-fail-<stage>`, null `detail` — honest: no flow.log to quote). Separately,
> `knowledge_db.connect` now arms `timeout=30s` + `PRAGMA busy_timeout=30000` (parity with
> `journal_db.connect`): the campaign runs a pool of `ingest_run` subprocesses against one DB and
> the driver swallows ingest errors, so an unguarded `database is locked` would silently drop a run.

> **2026-06-13 multi-run safety.** `repair()` re-derives each row's status from the project's
> *latest* `stage_log.jsonl`. That was fine when every project had one run, but once designs are
> **re-run after a fix** a project holds several runs (old fail + clean re-run) sharing one
> `project_path`, and only the newest stage_log survives on disk. Applying it to *every* row of the
> project clobbered the older runs — flipping a recorded failure to look like a pass and deleting its
> `failure_event` (caught when a backfill turned 7 honest sky130 fail rows into pass/partial). Fix:
> status re-derivation is now restricted to the **latest-ingested row per project**
> (`_latest_run_id_per_project`); older runs keep exactly what the live ingest recorded. Their
> `failure_events` are still completed **additively** (`_backfill_missing_orfs_event`): a bare
> `orfs-fail-<stage>` is added only when the row has none, so a detailed event written live (e.g.
> `orfs-fail-route-GRT-0116`) is never downgraded. Net effect on the production store: every one of
> the 24 backend-fail rows now carries an orfs-fail event (was 8) with zero history rewritten.

## Fmax Search Extension (2026-06-04)

The knowledge store also feeds the loose-first **Fmax search** (`scripts/reports/fmax_search.py`
+ pure `fmax_model.py`; see `references/orfs-playbook.md`):

- **Three per-stage setup-slack columns** — `floorplan_setup_ws`, `place_setup_ws`,
  `finish_setup_ws` — populated by `ingest_run.py` from `summary.timing_staged` (and `--backfill`
  reads them straight from preserved `backend/RUN_*/logs/` for historical runs, filtering the
  `1e+39` unconstrained sentinel).
- **`clock_period_ns` now comes from the SDC** (`set clk_period` in `constraints/constraint.sdc`),
  not `config.mk` — it was NULL for all 750 runs before this.
- **`learn_heuristics.py` emits two per-family/platform aggregates** over signoff-positive runs:
  `closing_period` (`period − finish_ws`; seeds the search) and `slack_deterioration` — the p90
  per-stage erosion `d_fp_pl` (floorplan→place, dominant) and `d_pl_fin` (place→finish, ≈ neutral),
  in both ns and pct-of-period. `query_knowledge.get_closing_period` / `get_deterioration` expose
  them; `fmax_model.select_model` gates on `n ≥ 8` (else cold-start defaults).
- **Online self-correction:** `fmax_search.py --verify` appends a verified `(floorplan, place,
  finish)` triple (tagged `eval_arm='fmax_verify'`, signoff-positive so it is learnable) so the
  deterioration model tightens as verify data accrues.

## Invariants

1. `ingest_run.py` only reads structured JSON artifacts; it never parses raw ORFS logs. If an artifact is missing, the corresponding column is NULL.
2. `heuristics.json` is **advisory**. `suggest_config.py` falls back to its hardcoded tables when no learned data is available for a family/platform.
3. `failure_candidates.json` is never auto-merged into `failure-patterns.md` — it is a human review queue.
4. The SQLite DB is append-only semantically: `run_id = sha1(project_path + ":" + ppa_json_mtime)`, so re-ingesting the same completed run is a no-op, while a new run iteration produces a new row.
5. `analyze_execution.py` NEVER auto-applies fixes — output is a review queue only.
6. All success judgements share ONE predicate, `knowledge_db.is_success` — `learn_heuristics.py`,
   `monitor_health.py`, the dashboard health strip, and `eval_heuristics.py` import it, so they
   cannot drift. "Success" = strict 6-stage pass OR signoff-positive (≥1 positive clean signal, no
   failed signoff); absence of all signoff data is NOT a success.
7. `search_failures.py` has zero external dependencies (BM25 is stdlib-only).
8. Config lineage rows are only created when the config diff is non-empty.
9. `build_lineage_view.py` opens the DB **read-only** and is strictly descriptive — it is **never**
   wired into `suggest_config` as an auto-tuner. Config lineage is a loose single-parent diff
   chain, not a true DAG.
10. `suggest_config` applies the hard `PLACE_DENSITY_LB_ADDON ≥ 0.10` floor and the design-type
    clamps (bus_heavy CORE_UTILIZATION→15, etc.) as a post-filter over any learned median — safety
    rails beat empirical medians.
11. The payoff harness reports **wall-clock** cost (`cost_metric`): CPU-hours/peak-RAM are not
    captured by the flow's `stage_log.jsonl`, and it never fabricates CPU-hours (forward-compatible
    to `cpu_s` / `peak_rss_kb`). A `win` requires the learned arm to be a *usable* signed-off result
    that is also cheaper; cheaper-but-both-fail is `inconclusive`, never a win.
12. The fix-iteration verdict is normalized to the canonical set
    `cleared|win|no_change|regression|inconclusive` by the **ingester**; the shell's legacy
    strings never reach the learning tiers.
13. `run_violations` is written for **every** ingested run (clean or not) — the complete
    violation landscape, independent of whether any fix was attempted.
14. Tier-2 `fix_trajectories` is **never archived**, and Tier-3 `fix_recipes` derive from it (not
    from raw `fix_events`), so archiving raw `fix_events` into `fix_events_archive.sqlite` loses no
    learning signal. Abandoned episodes and failed strategies ARE counted (negative learning).
15. Fix-strategy ranking has **no hard gate**: `diagnose_signoff_fix.py` only reorders the existing
    real-fix catalog by clearance score; it never edits `PLACE_DENSITY_LB_ADDON` or adds strategies.
    `mine_rules.py`'s `fix_candidates` (≥3 resolved episodes) is a human-review queue —
    `failure-patterns.md` is never auto-written.
16. **Only `promoted` recipes affect live strategy ranking** (`filter_promoted` in
    `diagnose_signoff_fix.py`). An absent `recipe_status` row = grandfathered `promoted`.
    Shadow and candidate recipes are logged but inert in arm-A and live runs.
17. **Journal archival loses no conclusions.** `knowledge/journal.sqlite` (gitignored,
    high-volume evidence) is physically separate from `knowledge.sqlite` /
    `heuristics.json` (git-tracked conclusions). Archiving or rotating the journal DB
    never removes a recipe or trajectory from the knowledge DB.
18. **The provenance chain is queryable end-to-end** via `knowledge/trace_provenance.py`:
    `solution` traces a recipe back through A/B trials, fix episodes, journal actions, and
    designs; `bug` lists every known solution for a symptom with lifecycle status and
    evidence strength.
19. **`outcome_score` (Win 1) is additive, advisory, and a PURE function of one run's OWN
    artifacts.** It is a continuous `[0,1]` dense reward (`w_stage·stage_progress + w_vrr·VRR`,
    `0.7/0.3`; NULL when the furthest stage is unknown; renormalized to stage-only when the run
    attempted no fix). **Gate vs. score:** `is_success` stays the *sole* authority for run
    classification (clean/fail) **and** for recipe promotion — `outcome_score` MAY order
    suggestions and break non-clean ties for *which fix to try next*, but it NEVER reclassifies a
    run and NEVER produces a `win` verdict / promotion on a non-clean run (a non-clean A/B arm
    stays `inconclusive`). It is computed only from the run's own `stage_log.jsonl` + its own
    `fix_log.jsonl` — **never** a SELECT against sibling rows (that shape was the 2026-06-13
    multi-run-clobber bug) — so re-ingest is idempotent. `repair_run_status.py` never touches it.
    The learner aggregates it into recipes as `mean_outcome_score`, a tiebreaker layered *under*
    the `fix_model` Beta prior (`rank_key = (success_rate_beta, mean_outcome_score)`); absent it
    ranks byte-identically. PPA-product term is DEFERRED (degenerate under singleton families).
20. **The A/B loop fires on the production path (Tier −1 Gate A + Gate B).** `learn_heuristics.learn()`
    enqueues new/changed recipes as `candidate` on every rebuild (not only inside
    `engineer_loop.run`), so a batch-driven campaign populates `recipe_status`; `engineer_loop.py
    ab-drain` then plans/runs/judges the arms. `diff_and_enqueue` is idempotent, so the loop's own
    enqueue composes safely. Grandfathered recipes are re-validated explicitly via `ab-enqueue`.
    **Subject selection must reach winning recipes (2026-06-16 Gate B):** `ab_runner.plan_trial`
    selects A/B subjects from `run_violations` (the POST-fix snapshot) FIRST, then falls back to the
    recipe's `heuristics.symptoms[sid].evidence_designs` (the PRE-fix exhibitors, resolved to on-disk
    project dirs). Without the fallback a *successfully-fixed* symptom (e.g. antenna) has no
    `run_violations` rows, so the very recipes that win could never be A/B'd — `plan_trial→None`.
    First live verdict: `density_relief` (sky130 metal/via spacing) `candidate → promoted` on a 2-win
    `ab_trials` drain. The Gate A signature is **`ab_trials=0` while `fail`/`partial` rows exist** —
    once the corpus has such rows, an empty `ab_trials` means the loop is inert and silently lying.
21. **r2g-bench (Win 3) is held out from learning, not from honesty.** Runs whose design is in
    `knowledge/eval/bench_set.json` are flagged `runs.is_bench=1` at ingest and EXCLUDED from the
    *learning read* only — `learn_heuristics._fetch_learnable_rows` (family medians + score
    tiebreaker) and the recipe-trajectory aggregation drop them. Their `failure_events`,
    `run_violations`, and `outcome_score` are STILL written: a bench `fail` run keeps its
    `orfs-fail-%` event and stays in the `fail`-rows == `orfs-fail`-events honesty count
    (invariant H3). `eval_heuristics.bench_score` reports per-checkpoint SR + mean/LCB
    `outcome_score` + stage-reach over `is_bench=1` runs — a NON-BLOCKING scoreboard, never a gate.
22. **Feature-keyed retrieval (Win 5) is predictive and fall-back-safe.** `presynth.py` emits a
    PRE-ROUTE feature vector (instance count, primary I/O, est logic depth, target util, clock
    period, routing layers) — available at SUGGESTION time, unlike the post-route `metadata.csv`
    outcomes. Stored as `runs.presynth_features_json` at ingest (NULL when absent). `suggest_config`
    z-score-normalizes the features (so instance-count magnitude doesn't dominate) and retrieves the
    k=5 nearest CLEAN, non-bench runs with `outcome_score ≥ median`, seeding the config from their
    median — REPLACING the `infer_family` prefix lookup (245/303 singletons). When no feature vector
    exists or the corpus is too small it FALLS BACK to family medians, and the design-type clamps +
    `PLACE_DENSITY_LB_ADDON ≥ 0.10` floor still apply afterward (safety rails beat retrieval). The
    corpus index is empty until the 5b backfill (`presynth.py` over historical synth dirs +
    re-ingest) runs — so the existing store's suggestions are unchanged until then.
23. **Backend aborts are first-class symptoms, not just `failure_events` (2026-06-17 route-relief).**
    A route-stage abort (`orfs_status='fail'` at `route`) is keyed under the symptom
    `check='orfs_stage', class=<fail_stage>` in `run_violations` (`ingest_run._write_run_violations`),
    NOT a bogus `timing` symptom and NOT only the `orfs-fail-route` `failure_events` signature. This
    `symptom_id` is identical to the one a route `fix_log.jsonl` row produces (`check=orfs_stage,
    violation_class=route` via `fix_signoff.sh --check route`), so the failing run and its
    `route_relief` recipe pool under one key and `ab_runner.plan_trial` can match them. The route A/B
    arms run through `engineer_loop._process_backend_ab_arm` (apply-then-flow: arm B applies
    `route_relief` then runs the flow ONCE; arm A is the control) because — unlike a signoff arm — the
    flow itself *fails*, so the `flow→signoff→fix` model does not apply. The symptom infrastructure
    reserved `orfs_stage` from the start (`symptom._PREDICATE_KEYS`); this invariant is what finally
    populates it. Same blind-spot class as Gate A/B: the machinery existed but a whole failure class
    (backend aborts) could never reach `recipe_status`/`ab_trials` until the symptom was assigned and
    the fix loop could log it. First route verdict: `route_relief` `candidate → promoted` on a
    `wb2axip_wbsafety` win (arm B routes at lower util; control times out).
24. The Fmax `slack_deterioration` model is **advisory and tiered**: below `n = 8` learned samples
    `fmax_model.select_model` falls back to corpus cold-start defaults, never an under-sampled
    median. Estimator is p90 (conservative); learned terms are clamped `≥ 0` (never predict negative
    erosion). The reported Fmax is a **proxy (UNVERIFIED)** unless `--verify` ran — post-place timing
    is optimistic vs signoff, so the number is always labelled, never presented as a closed result.
    (Fmax search: `scripts/reports/fmax_search.py` + pure `fmax_model.py`; the three per-stage
    `*_setup_ws` columns + `clock_period_ns` are written by `ingest_run.py` and the per-family
    `closing_period`/`slack_deterioration` aggregates by `learn_heuristics.py` — see the
    "Fmax Search Extension" section above and `references/orfs-playbook.md`.)
25. `ensure_schema` applies `schema.sql` then runs the column forward-migration
    (`_migrate_add_columns`) and the post-migration index loop (`_POST_MIGRATION_INDEXES`,
    which indexes only `fix_events`/`run_violations`/`fix_trajectories(symptom_id)` — never the
    `runs` table). The three Fmax slack columns (`floorplan_setup_ws`, `place_setup_ws`,
    `finish_setup_ws`, all `REAL`) live both in `schema.sql`'s `runs` CREATE TABLE and in
    `_ADDED_COLUMNS["runs"]`, so a fresh DB gets them from the DDL and a legacy DB gets them
    via the ALTER-TABLE migration.
26. **The store is shareable as a deterministic text bundle (`knowledge_sync.py`).** `export`
    serializes `knowledge.sqlite` to `knowledge/store/` (one NDJSON file per table) as a PURE
    function of DB content: rows sorted by natural key, JSON object keys sorted, machine-local
    AUTOINCREMENT surrogate ids DROPPED, NO wall-clock stamp in the manifest. So the same DB
    always yields a BYTE-IDENTICAL bundle (a no-op re-export is a 0-line git diff), and
    `export → import → re-export` reproduces the identical `manifest.digest` (lossless). The
    `digest` (sha256 over the per-table digests) is the store fingerprint the drift gate compares.
27. **Cross-operator `merge` is ADDITIVE and HONESTY-GATED.** A row is inserted only when its dedup
    key is absent locally. `symptom_id` is the genuinely machine-portable content key (so symptoms/
    recipe experience pool across operators); `run_id` embeds the absolute path + ppa mtime so it
    dedups only on an identical filesystem (cross-operator `runs` are normally ADDITIVE — safe under
    the gate); the three surrogate-id evidence tables (`failure_events`, `ab_trials`, `escalations`)
    dedup on FULL-ROW content (`DEDUP_FULL_ROW`) so a shared NULL never collapses two distinct rows
    and a surrogate `trial_id=1` never FUSES unrelated rows. A local row is NEVER overwritten (runs
    are immutable history; a `recipe_status` lifecycle disagreement is REPORTED, not flipped — and a
    recipe whose key is ABSENT locally is imported as inert `shadow` so an imported promote/demote
    re-validates via A's own A/B rather than silently taking effect; cross-machine `generation` is
    not comparable, so `meta.generation` merges as `max`). The whole merge runs in ONE transaction
    ROLLED BACK if the post-merge store fails any `honesty.run_all` gate (the FIVE gates: H3 parity,
    H3-coverage, **H3-inverse — no `orfs-fail-%` event on a non-`fail` run**, Gate-A, derivability)
    OR has a dangling foreign key — a merge that would make the store LIE is refused, not applied
    (same firewall philosophy as the loop's other honesty gates). `honesty.py` is the single home of
    those gates (imported by the merge, the CI runner `python3 knowledge/honesty.py --db …`, and
    `tests/test_honesty_invariants.py` — they cannot drift).
28. **An A/B trial is only honest if the two arms do DIFFERENT work** (2026-06-24 loop-closure audit).
    `plan_arms_for_candidates` copytrees each subject into an arm dir EXCLUDING `reports/` (as well as
    `backend/`+`*.gds`): a signoff arm's subject is a previously-FIXED clean project, so copying its
    clean `reports/drc.json` made `process_one` `_mark_clean` the arm BEFORE `_run_fix` ran → arm A
    (`R2G_FIX_EXCLUDE`) and arm B (`R2G_FIX_RANK_FIRST`) were byte-identical and the verdict was
    wall-clock noise → **no nangate45 recipe ever promoted** while `ab_trials` kept growing. A signoff
    `ab_arm` therefore ALWAYS reaches `_run_fix` (never short-circuits), the success-tie cost tiebreak
    in `ab_runner.judge_repeated` is variance-aware (combined-stderr; `se==0` is maximal confidence,
    not none), and an arm that produced no backend ESCALATES instead of ingesting a junk
    `orfs_status='unknown'` run row (which would clobber a prior real arm via `_arm_metric`'s
    latest-row query and fake a `loss`). **Honesty check beyond "`ab_trials` non-empty": `promoted`
    must eventually grow PER-PLATFORM**, and a trial's `metrics_json` arms must not be identical.
    (The former KNOWN GAP here — timing/place recipes routed to an inert DRC/LVS arm — was CLOSED
    2026-06-24: `_symptom_check` routes by STRATEGY; see CLAUDE.md and invariant 29's follow-through
    for DRC/LVS.) Detail: `references/failure-patterns.md` ("Learning-Loop Closure Failures").

29. **A signoff A/B arm is judged on ITS OWN symptom, with a recorded reason (judge v2, 2026-07-04).**
    The whole-run `is_success` metric ties both arms whenever an UNRELATED residual keeps the run
    non-clean — 193/228 live trials were inconclusive (antenna_diode_repair 0-decisive-in-93) with no
    recorded cause, and 38 candidates sat capped dead. `judge_finished_trials` now resolves the
    recipe's symptom to a target (`_symptom_target`): a DRC arm succeeds iff the TARGET violation
    class count reached 0 on a definitively-run DRC (`_drc_symptom_cleared`; clean/clean_beol always
    clears, stuck/unknown never does), an LVS arm iff `lvs_status='clean'` — the same
    metric-granularity rule the timing (`wns_ns`) and synth (stage-clearance) arms already used.
    Every trial's `metrics_json` carries `judge_version: 2`, a `reason` code
    (`ab_runner.judge_repeated_ex`: both_arms_never_succeed, success_tie_cost_within_noise, …) and the
    `target`, so an inconclusive corpus is QUERYABLE. `_ab_coverage_gap` counts ONLY v2 inconclusives
    toward the re-plan cap — pre-v2 verdicts were blind to the symptom under test and must not
    permanently bar a candidate (decisive verdicts count from any era). Non-divergent strategies
    (`recipe_lifecycle.NONDIVERGENT_STRATEGIES`) are refused at enqueue and legacy rows are healed to
    the NON-terminal bookkeeping status **`parked`** (`park_nondivergent`, called each drain) — parked
    ≠ demoted: it only means "the A/B harness cannot differentiate the arms".

30. **Negative experience is CONSUMED at apply time, and kept clean at write time (2026-07-04).**
    Storage without consumption re-tried the same dead fix on the same design up to 112 times.
    `diagnose_signoff_fix._annotate_live_gates` reads this project's `fix_events`: a strategy with
    ≥ `R2G_FIX_DEAD_AFTER` (default 2) terminal failures (`no_change`/`regression`) and ZERO clears on
    THIS design+check is `dead_here` and `_live_auto_strategy` skips it in blind live runs
    (`R2G_FIX_RETRY_DEAD=1` restores retry; `--rank-first` — the A/B arm-B path — bypasses ALL gates
    by design). The same gate skips `lifecycle_status='shadow'` (A/B-demoted) strategies, closing the
    leak where demotion only stripped the INDEXED path while catalog/pooled/fallback rankings could
    still auto-apply a demoted recipe. Write-side hygiene: an episode whose path never ran a real
    strategy is **`not_attempted`**, never `abandoned` (1957/2376 'abandoned' rows were none-only —
    not fix experience); `symptom.normalize_class` strips quoted/prose KLayout category text at every
    entry point (extract_drc, canonical_signature) and `_build_trajectory` re-keys legacy quoted-class
    signatures on rebuild, so the symptom index pools instead of fragmenting.

31. **The wheel's step-3 mining is automatic, and the store's plumbing cannot silently lie
    (2026-07-04 robustness).** `fix_log_manager.manage()` now runs `mine_rules.mine` after every
    learn (best-effort; `R2G_MINE_AUTORUN=0` opts out), so `failure_candidates.json` — the
    human-review queue — no longer goes stale for lack of a caller. Extract scripts write
    `reports/*.json` ATOMICALLY (`report_io.write_json_atomic`; a kill -9 mid-write can no longer
    leave a torn report that ingest misreads as a blank run). `knowledge_db.connect` arms WAL
    (parity with the journal) so an ingest burst cannot exceed the busy_timeout and get silently
    swallowed — and `engineer_loop._ingest` now WARNS loudly when an ingest returns no run_id.
    A corrupt `heuristics.json` degrades diagnosis to cold-start ranking (never a crash), and an
    unreadable recipe lifecycle fails CLOSED (cold-start) instead of granting unvalidated recipes
    promoted-equivalent trust.

32. **Timestamps are SYSTEM-LOCAL with a numeric offset; compare them with `julianday()`, never
    lexicographically (2026-07-04, operator request).** All writers (`_now()` helpers, ingest's
    `ingested_at`/`snapshot_ts`/`first_seen`, journal actions, ab_trials `ts`, lesson `synced_at`,
    the fix-log `ts` from `fix_signoff.sh`, heuristics `generated_at`) stamp
    `YYYY-MM-DDTHH:MM:SS±HH:MM` — matching the flow artifacts (`RUN_*` dirs were already local).
    Rows written before the switch carry UTC `…Z` stamps, which sort AHEAD of newer local stamps
    lexicographically by the UTC offset — so every load-bearing latest-row ordering
    (`_arm_metric`, ab_runner's subject ROW_NUMBERs, ingest's prior-row lookups,
    `_design_class_by_project`, `pending_candidates`) orders by `julianday(col)`, which parses
    both regimes and orders by REAL time. New readers must do the same. Tests:
    `tests/test_local_timestamps.py`.

## Sharing the store across users

`knowledge.sqlite` (git-tracked) ships the skill pre-trained, but a binary blob does not
3-way-merge, bloats history (a full rewrite per commit), and is unreviewable. `knowledge_sync.py`
adds a git-friendly, mergeable interchange so experience transfers between operators:

```bash
python3 knowledge/knowledge_sync.py export                 # DB  -> knowledge/store/ (commit this)
python3 knowledge/knowledge_sync.py import  --bundle knowledge/store --db NEW.sqlite   # rebuild
python3 knowledge/knowledge_sync.py merge   --bundle OTHER/store      # union another operator in
python3 knowledge/knowledge_sync.py merge   --from-db OTHER/knowledge.sqlite
python3 knowledge/knowledge_sync.py status                 # bundle<->DB drift + honesty gates
```

Workflow contract: the committed store is the binary `knowledge.sqlite` (commit it after
ingest/learn). When you produce a bundle to share, **re-run `export` after the latest `learn()`**
so it reflects the current DB (`status` confirms it matches). After any `merge`, run `learn()` +
`engineer_loop ab-drain` so imported recipes re-validate locally (the merge brings raw evidence —
runs, fix_events, fix_trajectories, ab_trials, symptoms — not a blessed lifecycle).

**The binary `knowledge.sqlite` is the tracked, committed store** (the skill ships pre-trained; a
fresh clone is immediately usable). `heuristics.json` stays tracked too. The `knowledge/store/`
NDJSON bundle is **gitignored and produced on demand** — it is the git-friendly interchange for
*sharing/merging* experience across operators (`export` to hand off a reviewable diff, `merge` to
fold another operator's store in under the honesty gate), NOT the committed format. `export` is a
faithful, lossless mirror (verified: `import` then re-`export` yields the identical `manifest.digest`),
so when you do share one, `status` confirms it matches the DB and `import` reproduces the EXACT store.
Commit workflow is unchanged: commit `knowledge.sqlite` after ingest/learn; the bundle is optional.

> NOTE (2026-06-23): a bundle-as-source-of-truth migration (gitignore the binary, rebuild via
> `import` on clone) was implemented then REVERTED per operator preference — the binary stays the
> committed store. The export/import/merge/status tooling and the honesty gates remain available.

## Engineer Loop (spec 2026-06-09)

The engineer loop (`scripts/loop/engineer_loop.py`) closes the observe→ingest→learn→fix cycle
autonomously. It introduces a two-database split and a recipe lifecycle enforced by the
`recipe_status` table.

### Two-database split

| Database | Tracked? | Tables | Role |
|---|---|---|---|
| `knowledge/journal.sqlite` | **gitignored** | `actions`, `log_summaries`, `tool_bugs` | Evidence: full flow telemetry — every command, tool-log digest, EDA-tool bug |
| `knowledge/knowledge.sqlite` + `heuristics.json` | **git-tracked** | existing + `recipe_status`, `ab_trials`, `escalations`, `meta` | Conclusions: recipes, trajectories, heuristics, A/B verdicts |

The two DBs link via shared keys: `symptom_id`, `run_id`, `fix_session_id`. Journal archival
never loses a conclusion — conclusions live only in the knowledge DB.

### New knowledge DB tables (added by engineer loop)

| Table | Key columns | Purpose |
|---|---|---|
| `recipe_status` | `symptom_id`, `design_class`, `platform`, `strategy` | Lifecycle state: `shadow` → `candidate` → `promoted` (or `demoted`) |
| `ab_trials` | `trial_id`, `recipe_key`, `arm_a_run_id`, `arm_b_run_id` | A/B verdict: `win` / `loss` / `inconclusive` with metrics |
| `escalations` | `escalation_id`, `design`, `run_id`, `reason`, `status` | Open items for the agent tier; `reason` ∈ `{unknown_symptom, catalog_exhausted, unseen_crash, repeated_regression}` |
| `meta` | `key`, `value` | Heuristics generation counter and loop bookkeeping |

### Recipe lifecycle

```
candidate  (authored here by Gate-A learner_diff / enqueue_candidate; enqueued for A/B)
  │  recipe_status = f(FULL ab_trials corpus)  —  ab_runner.judge_recipe (2026-06-24)
  │    net wins>losses → promoted ;  net losses>wins → shadow ;  inconclusive → unchanged
  ▼
promoted  (affects live ranking in diagnose_signoff_fix.py)   ⇄   shadow  (demoted; a later
                                                                     net-positive win can revive it)
# stage_shadow() is the agent-authoring entrypoint (decision 7); the demotion sink is `shadow`.
# An `inconclusive` NEVER demotes (it carries no information). auto_demote_on_regression() is a
# provided-but-not-auto-wired helper (the ingester emits no 'regression' verdict today).
```

Absent `recipe_status` row = grandfathered `promoted` (recipes validated before the loop
shipped). New and changed learned recipes enter as `candidate` via `diff_and_enqueue`.
Agent-authored strategies enter via `recipe_lifecycle.stage_shadow(...,
provenance='agent:<escalation_id>', ...)` and must win their A/B before promoting — no
special trust (decision 7 of the design spec).

### Journaling (loop + agent tier)

Discrete actions are journaled via the CLI (or `journal_db.append_action` directly):

```bash
python3 knowledge/journal_action.py action \
    --project <dir> --actor <loop|agent|operator> \
    --type <config_knob_delta|sdc_edit|stage_rerun|tool_invoke|escalate|ab_launch|promote|demote> \
    [--payload JSON] [--symptom <sid>] [--session <fix_session_id>] [--parent <action_id>]
```

Never breaks the caller (warns + exits 0). `R2G_JOURNAL=0` disables all journal writes.

**Decision-journaling is wired into the production loop (2026-06-17, Tiers A/B):** `fix_signoff.sh`
journals `config_knob_delta` (symptom-linked, parent-chained) + `stage_rerun`; `engineer_loop`
journals `ab_launch` (per arm); `ab_runner.record_trial` journals `promote`/`demote` (carrying
`trial_id`); `escalations.open_escalation` journals `escalate`. All best-effort, journal-only,
read by **no** learner — `knowledge.sqlite` stays the sole learner input and honesty-gate source.

**Advisory cross-DB check (NOT an honesty gate):** every `ab_trials` row with `verdict='win'`
*should* have a `promote` action carrying its `trial_id`. This is **forward-only** — trials
judged before the journaling was wired have no `promote` action and are reported
*journal-incomplete*, never as a loop-honesty failure (journal writes are best-effort/silenceable,
so a missing row can never fail a gate). The promotion honesty **gate** stays knowledge-side:
`recipe_status` rows with `ab_trial:%` provenance are consistent with `ab_trials` wins.
