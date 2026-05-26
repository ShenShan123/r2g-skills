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

## Invariants

1. `ingest_run.py` only reads structured JSON artifacts; it never parses raw ORFS logs. If an artifact is missing, the corresponding column is NULL.
2. `heuristics.json` is **advisory**. `suggest_config.py` falls back to its hardcoded tables when no learned data is available for a family/platform.
3. `failure_candidates.json` is never auto-merged into `failure-patterns.md` — it is a human review queue.
4. The SQLite DB is append-only semantically: `run_id = sha1(project_path + ":" + ppa_json_mtime)`, so re-ingesting the same completed run is a no-op, while a new run iteration produces a new row.
5. `analyze_execution.py` NEVER auto-applies fixes — output is a review queue only.
6. `monitor_health.py` uses the same success criteria as `learn_heuristics.py`.
7. `search_failures.py` has zero external dependencies (BM25 is stdlib-only).
8. Config lineage rows are only created when the config diff is non-empty.
