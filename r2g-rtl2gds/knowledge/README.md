# r2g-rtl2gds Knowledge Store

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
20. **The A/B loop fires on the production path (Tier −1 Gate A).** `learn_heuristics.learn()`
    enqueues new/changed recipes as `candidate` on every rebuild (not only inside
    `engineer_loop.run`), so a batch-driven campaign populates `recipe_status`; `engineer_loop.py
    ab-drain` then plans/runs/judges the arms. `diff_and_enqueue` is idempotent, so the loop's own
    enqueue composes safely. Grandfathered recipes are re-validated explicitly via `ab-enqueue`.

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
shadow  (inert, outside live pool)
  │  A/B win
  ▼
candidate  (enqueued for A/B trial)
  │  win → promote
  │  loss / inconclusive → demote back to shadow
  ▼
promoted  (affects live ranking in diagnose_signoff_fix.py)
  │  2 consecutive regressions → auto-demote
  └──────────────────────────────────────────────►  shadow
```

Absent `recipe_status` row = grandfathered `promoted` (recipes validated before the loop
shipped). New and changed learned recipes enter as `candidate` via `diff_and_enqueue`.
Agent-authored strategies enter via `recipe_lifecycle.stage_shadow(...,
provenance='agent:<escalation_id>', ...)` and must win their A/B before promoting — no
special trust (decision 7 of the design spec).

### Journaling (agent tier)

The agent journals every discrete action via the CLI:

```bash
python3 knowledge/journal_action.py action \
    --project <dir> --actor agent \
    --type <config_knob_delta|sdc_edit|stage_rerun|tool_invoke|escalate|ab_launch|promote|demote> \
    [--payload JSON] [--symptom <sid>] [--session <fix_session_id>]
```

Never breaks the caller (warns + exits 0). `R2G_JOURNAL=0` disables all journal writes.
