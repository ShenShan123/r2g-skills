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
`repair_run_status.py` prints a before/after `orfs_status` histogram; on the current corpus it is
largely a no-op (stage logs store integer exit codes and `is_success` already credits
signoff-positive partials).

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
