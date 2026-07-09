# Script Index

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

This is the full script inventory for `nangate45-graph-expander`.

Use [operation_matrix.md](./operation_matrix.md) when you want task-oriented navigation.  
Use this file when you want the exhaustive index.

## Primary Workflow Drivers

- `scripts/run_expansion_round.py`
- `scripts/run_until_empty.py`
- `scripts/search_and_expand_until_target.py`

## Acquire

- `scripts/acquire/discover_download_candidates.py`
- `scripts/acquire/discover_repo_manifest_candidates.py`
- `scripts/acquire/clone_repo_manifest.py`
- `scripts/acquire/build_external_synth_variant_candidates.py`
- `scripts/acquire/refresh_downloads_scan_state.py`

## Repair

- `scripts/repair/classify_failed_candidates.py`
- `scripts/repair/auto_fix_failures.py`
- `scripts/repair/extract_failure_kb_candidates.py`
- `scripts/repair/refresh_failure_knowledge_base.py`
- `scripts/repair/update_failure_strategy_scores.py`
- `scripts/repair/auto_generate_signature_actions.py`
- `scripts/repair/build_failure_casebook.py`
- `scripts/repair/build_failure_diagnosis.py`
- `scripts/repair/build_llm_repair_cases.py`
- `scripts/repair/build_llm_patch_requests.py`
- `scripts/repair/call_openai_llm_patch_api.py`
- `scripts/repair/run_local_llm_patch_agent.py`
- `scripts/repair/evaluate_llm_patch_results.py`
- `scripts/repair/mine_llm_patch_rule_candidates.py`

## Validate

- `scripts/validate/check_mapped_netlist_duplicates.py`
- `scripts/validate/audit_near_duplicates.py`
- `scripts/validate/validate_publish_readiness.py`

## Publish

- `scripts/publish/build_publish_candidates.py`
- `scripts/publish/refresh_expanded_raw_manifest.py`
- `scripts/publish/rebuild_external_index_from_dirs.py`
- `scripts/publish/build_synth_variant_dataset_manifest.py`
- `scripts/publish/record_dataset_snapshot.py`

## Report

- `scripts/report/score_download_repos.py`
- `scripts/report/score_design_quality.py`
- `scripts/report/summarize_external_index.py`
- `scripts/report/summarize_dataset_scale.py`

## Hygiene

- `scripts/hygiene/deduplicate_external_index.py`
- `scripts/hygiene/deduplicate_external_by_canonical_source.py`
- `scripts/hygiene/cleanup_rejected_download_repos.py`

## Shared Helpers

- `scripts/skill_env.py`
- `scripts/common/io_utils.py`
- `scripts/common/state_utils.py`
- `scripts/common/manifest_utils.py`
