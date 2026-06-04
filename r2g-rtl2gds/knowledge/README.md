# r2g-rtl2gds Knowledge Store

This directory is the skill's cross-run memory. It is **not** a cache — it is
the input to `suggest_config.py` and `failure-patterns.md` review.

## Layout

| File | Producer | Consumer |
|---|---|---|
| `schema.sql` | hand-edited | `knowledge/knowledge_db.py` at `ensure_schema` time |
| `families.json` | hand-edited seed; append as new designs ship | `knowledge/knowledge_db.py::infer_family` |
| `runs.sqlite` | `knowledge/ingest_run.py` (one row per ingested run) | `learn_heuristics.py`, `mine_rules.py`, `query_knowledge.py` |
| `heuristics.json` | `knowledge/learn_heuristics.py` | `suggest_config.py`, agent, dashboard |
| `failure_candidates.json` | `knowledge/mine_rules.py` | human reviewer → `references/failure-patterns.md` |

## Loop

```
              (run the flow)
                   │
                   ▼
     reports/*.json, stage_log.jsonl, diagnosis.json
                   │
      ingest_run.py │
                   ▼
              runs.sqlite ──► learn_heuristics.py ──► heuristics.json
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
runs.sqlite ──► monitor_health.py ──► health alerts (degradation detection)
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
| `monitor_health.py` | reads `runs.sqlite` | agent (degradation alerts) |
| `search_failures.py` | indexes `failure-patterns.md` + `failure_candidates.json` | `analyze_execution.py` |
| `analyze_execution.py` | reads project artifacts + search results | agent (fix proposal review queue) |
| `build_lineage_view.py` | read-only (`mode=ro`) projection over `runs.sqlite` + `config_lineage` + `heuristics.json` | dashboard "Knowledge health" + "Tuning provenance" panels |
| `eval_set.json` + `eval_heuristics.py` | frozen eval set; `emit` writes paired naive/learned arms via `suggest_config --no-learned`, `summarize` → `eval_results.jsonl` / `eval_summary.json` | operator (payoff A/B verdict) |

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
