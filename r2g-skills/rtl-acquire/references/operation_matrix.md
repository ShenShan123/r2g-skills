# Operation Matrix

> **r2g ingestion note (2026-07-09):** this skill is now the `rtl-acquire`
> sub-skill of r2g-skills. Env vars are `R2G_ACQUIRE_*` (see
> `references/env.local.sh.template`); synthesis goes through
> `signoff-loop/scripts/flow/run_orfs.sh`; graphs are def-graph
> `netlist_graph.pt` (30pt retired). Removed scripts:
> `setup_environment.py` (use eda-install + `scripts/skill_env.py`),
> `validate/mapping_change_sanity_check.py`,
> `validate/repair_external_graph_mapping_drift.py` (mapping-drift machinery
> died with 30pt). Added: `execute/expand_candidates.py`,
> `execute/graph_stats.py`, `knowledge/project_frontend_diagnosis.py`.

| I want to... | Main script | Main prerequisites | Mutates global state? | Main outputs |
| --- | --- | --- | --- | --- |
| run one full expansion round | `scripts/run_expansion_round.py` | candidate CSV or `--discover`; ORFS flow usable | yes | status json/log, run manifest, refreshed indexes/manifests/reports |
| keep expanding until candidate queue is empty | `scripts/run_until_empty.py` | discoverable or prebuilt candidate source | yes | repeated round outputs, updated workspace ledgers |
| search repos until target dataset size is reached | `scripts/search_and_expand_until_target.py` | network/repo search inputs, target count | yes | discovered candidates, round outputs, refreshed merged manifest |
| discover candidate RTL from `_downloads` | `scripts/acquire/discover_download_candidates.py` | populated `_downloads` tree | yes | candidate CSV, `scan_state/downloads_scan_state.json` |
| discover candidates from a repo manifest | `scripts/acquire/discover_repo_manifest_candidates.py` | repo manifest CSV | no | candidate CSV |
| clone missing repos into `_downloads` | `scripts/acquire/clone_repo_manifest.py` | repo manifest CSV | yes | cloned repo trees under `_downloads` |
| build multi-strategy synth variants | `scripts/acquire/build_external_synth_variant_candidates.py` | external success index and synth variant policy | no | synth variant candidate CSV |
| classify current failures | `scripts/repair/classify_failed_candidates.py` | existing failure-producing round outputs | yes | failure ledgers, retry/exclude CSVs |
| auto-fix deterministic failures | `scripts/repair/auto_fix_failures.py` | classified failure ledgers present | yes | retry candidates, exclude updates, auto-fix plan |
| refresh failure KB from recurring patterns | `scripts/repair/refresh_failure_knowledge_base.py` | failure KB candidates or failure summaries | yes | updated `failure_knowledge_base.md` |
| update repair strategy scores | `scripts/repair/update_failure_strategy_scores.py` | repair action log / failure evidence present | yes | refreshed `failure_strategy.json` |
| generate structured failure casebook | `scripts/repair/build_failure_casebook.py` | failure ledgers and signature/family artifacts | yes | `failure_casebook.json/.jsonl/.md` |
| generate compact failure diagnosis records | `scripts/repair/build_failure_diagnosis.py` | failure casebook and repair action log | yes | `failure_diagnosis.json/.jsonl/.md` |
| export diagnosis-driven LLM repair queue | `scripts/repair/build_llm_repair_cases.py` | failure casebook, failure diagnosis, and LLM repair policy | yes | `llm_repair_cases.json/.jsonl/.md` |
| build action-specific LLM patch requests | `scripts/repair/build_llm_patch_requests.py` | `llm_repair_cases.json` present | yes | `llm_patch_requests.json/.jsonl/.md` |
| call OpenAI patch API for queued requests | `scripts/repair/call_openai_llm_patch_api.py` | `OPENAI_API_KEY`, `llm_patch_requests.jsonl` | yes | `llm_patch_results.jsonl` |
| run local agent patch executor for queued requests | `scripts/repair/run_local_llm_patch_agent.py` | local `codex` CLI, `llm_patch_requests.jsonl` | yes | `llm_patch_results.jsonl` |
| validate returned LLM patch results | `scripts/repair/evaluate_llm_patch_results.py` | `llm_patch_requests.json` and `llm_patch_results.jsonl` | yes | `llm_patch_result_evaluation.json/.md` |
| mine validated LLM patches into reusable rule candidates | `scripts/repair/mine_llm_patch_rule_candidates.py` | validated `llm_patch_results` and evaluation outputs | yes | `llm_patch_rule_candidates.json/.md`, refreshed KB candidate input |
| validate publish readiness | `scripts/validate/validate_publish_readiness.py` | refreshed indexes, quality artifacts, publish policy | yes | `publish_validation.json/.md` |
| build publish-eligible design list | `scripts/publish/build_publish_candidates.py` | external index and quality outputs | yes | `publish_eligible_designs.csv/.json/.md` |
| refresh merged manifest | `scripts/publish/refresh_expanded_raw_manifest.py` | publish-eligible CSV or existing source indexes | yes | merged manifest CSV/MD |
| rebuild external success index from dirs | `scripts/publish/rebuild_external_index_from_dirs.py` | external root with design directories | yes | refreshed `external_benchmarks.../index.csv` |
| score repo quality | `scripts/report/score_download_repos.py` | scan-state and discovery/repo context | yes | repo quality CSV/MD |
| score design quality | `scripts/report/score_design_quality.py` | external success index and design outputs | yes | design quality CSV/MD |
| summarize external index | `scripts/report/summarize_external_index.py` | external index present | yes | external summary MD |
| summarize dataset scale | `scripts/report/summarize_dataset_scale.py` | merged manifest and index surfaces | yes | scale report CSV/MD |

## Common Command Templates

Use these as starting points. Adjust paths and policy flags for the current round.

### Run One Full Round

```bash
/path/to/python $CODEX_HOME/skills/nangate45-graph-expander/scripts/run_expansion_round.py \
  --discover \
  --discovered-out $HOME/work/data/nangate45_graph_expansion_workspace/candidates/downloads_discovered_candidates.csv \
  --run-retry
```

### Discover Candidates From `_downloads`

```bash
/path/to/python $CODEX_HOME/skills/nangate45-graph-expander/scripts/acquire/discover_download_candidates.py \
  --downloads-root $HOME/work/_downloads \
  --out-csv $HOME/work/data/nangate45_graph_expansion_workspace/candidates/downloads_discovered_candidates.csv \
  --scan-state-json $HOME/work/data/nangate45_graph_expansion_workspace/scan_state/downloads_scan_state.json
```

### Discover From A Repo Manifest

```bash
/path/to/python $CODEX_HOME/skills/nangate45-graph-expander/scripts/acquire/discover_download_candidates.py \
  --downloads-root $HOME/work/_downloads \
  --repo-manifest-csv /path/to/repo_manifest.csv \
  --out-csv $HOME/work/data/nangate45_graph_expansion_workspace/candidates/repo_manifest_discovered_candidates.csv
```

### Clone Missing Repos

```bash
/path/to/python $CODEX_HOME/skills/nangate45-graph-expander/scripts/acquire/clone_repo_manifest.py \
  --repo-manifest-csv /path/to/repo_manifest.csv \
  --downloads-root $HOME/work/_downloads
```

### Build Synth Variants

```bash
/path/to/python $CODEX_HOME/skills/nangate45-graph-expander/scripts/acquire/build_external_synth_variant_candidates.py \
  --variant-policy-json $CODEX_HOME/skills/nangate45-graph-expander/references/synth_variant_policy.json
```

### Validate Publish Readiness

```bash
/path/to/python $CODEX_HOME/skills/nangate45-graph-expander/scripts/validate/validate_publish_readiness.py \
  --publish-policy-json $CODEX_HOME/skills/nangate45-graph-expander/references/publish_policy.json
```

### Build Publish-Eligible Set

```bash
/path/to/python $CODEX_HOME/skills/nangate45-graph-expander/scripts/publish/build_publish_candidates.py \
  --publish-policy-json $CODEX_HOME/skills/nangate45-graph-expander/references/publish_policy.json
```

### Refresh The Merged Manifest

```bash
/path/to/python $CODEX_HOME/skills/nangate45-graph-expander/scripts/publish/refresh_expanded_raw_manifest.py \
  --use-publish-eligible \
  --publish-eligible-csv $HOME/work/data/nangate45_graph_expansion_workspace/manifests/publish_eligible_designs.csv
```

### Run Mapping-Change Sanity Check

```bash
  --mapping $HOME/work/AutoGraph/mapping.txt \
  --max-designs 12
```
