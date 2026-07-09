# scripts

This directory contains executable entrypoints and helper scripts for `nangate45-graph-expander`.

## Layout Policy

The directory now uses a split layout:

- top-level `scripts/*.py`
  - only the three primary workflow drivers remain here:
    - `run_expansion_round.py`
    - `run_until_empty.py`
    - `search_and_expand_until_target.py`
- categorized subdirectories
  - hold the canonical implementations for acquire, repair, validate, publish, report, and hygiene tasks

Reason:

- orchestration entrypoints should remain obvious and stable
- everything else is clearer when grouped by responsibility
- categorized implementations reduce duplication and make ownership clearer

So the current policy is:

- keep only the three execute/orchestration entrypoints flat at the top level
- call categorized scripts directly for non-execute tasks
- keep reusable helper-only code in `scripts/common/`
- treat categorized paths as the canonical implementation locations

## Category Views

## Entry Tiering

Use the scripts with three tiers in mind:

- Primary entrypoints
  - user-facing workflow drivers that should remain stable in both path and calling contract
- Secondary entrypoints
  - directly runnable utilities that support a focused maintenance task
- Internal helpers
  - support modules or narrow tools that should not be the default user interface

Current intended tiering:

- Primary entrypoints
  - `run_expansion_round.py`
  - `run_until_empty.py`
  - `search_and_expand_until_target.py`
- Environment setup and diagnostics
  - `setup_environment.py`
- Secondary entrypoints
  - all categorized scripts under:
    - `scripts/acquire/`
    - `scripts/repair/`
    - `scripts/validate/`
    - `scripts/publish/`
    - `scripts/report/`
    - `scripts/hygiene/`

- Internal helpers
  - `scripts/skill_env.py`
  - `scripts/common/*.py`

### `scripts/execute/`

- `run_expansion_round.py`
- `run_until_empty.py`
- `search_and_expand_until_target.py`

These are the primary workflow drivers.

### `scripts/acquire/`

- `discover_download_candidates.py`
- `discover_repo_manifest_candidates.py`
- `clone_repo_manifest.py`
- `build_external_synth_variant_candidates.py`
- `refresh_downloads_scan_state.py`

These discover, screen, and prepare candidate sources.

These are the canonical implementation files for the acquire group.

### `scripts/repair/`

- `classify_failed_candidates.py`
- `auto_fix_failures.py`
- `extract_failure_kb_candidates.py`
- `refresh_failure_knowledge_base.py`
- `update_failure_strategy_scores.py`
- `auto_generate_signature_actions.py`
- `build_failure_casebook.py`
- `build_failure_diagnosis.py`
- `build_llm_repair_cases.py`
- `build_llm_patch_requests.py`
- `run_local_llm_patch_agent.py`
- `call_openai_llm_patch_api.py`
- `evaluate_llm_patch_results.py`
- `mine_llm_patch_rule_candidates.py`

These maintain the repair loop and policy learning layer.

This is the canonical implementation group for repair logic.

### `scripts/validate/`

- `check_mapped_netlist_duplicates.py`
- `audit_near_duplicates.py`
- `validate_publish_readiness.py`

These validate structure, duplication, and publication readiness.

These are the canonical implementation files for the validate group.

### `scripts/publish/`

- `build_publish_candidates.py`
- `refresh_expanded_raw_manifest.py`
- `rebuild_external_index_from_dirs.py`
- `build_synth_variant_dataset_manifest.py`
- `record_dataset_snapshot.py`

These decide what becomes publishable and refresh the merged dataset surfaces.

These are the canonical implementation files for the publish group.

### `scripts/report/`

- `score_download_repos.py`
- `score_design_quality.py`
- `summarize_external_index.py`
- `summarize_dataset_scale.py`

These produce decision signals and handoff reports.

These are the canonical implementation files for the report group.

### `scripts/hygiene/`

- `deduplicate_external_index.py`
- `deduplicate_external_by_canonical_source.py`
- `cleanup_rejected_download_repos.py`

These keep the dataset and repo state clean.

These are the canonical implementation files for the hygiene group.

### `scripts/common/`

- `skill_env.py`
- `io_utils.py`
- `state_utils.py`
- `manifest_utils.py`

These are shared helper modules.

Current extracted responsibilities include:

- environment/path override resolution
- common JSON loading
- common JSON writing
- common CSV row loading
- shared timestamp formatting
- process liveness checks
- status-log compaction
- design-stage index refresh
- file fingerprinting for tracked global artifacts
- run-manifest snapshot and writeback helpers

## Recent Additions

- `build_external_synth_variant_candidates.py`
  - supports `references/synth_variant_policy.json`
  - can emit multiple logic-equivalent synthesis candidates per base RTL source
- `build_llm_repair_cases.py`
  - builds a prompt-ready queue for long-tail frontend failures that deterministic auto-fix should not mutate blindly
  - now ranks cases using `failure_diagnosis.json`, especially `next_best_action`
- `record_dataset_snapshot.py`
  - records dataset fingerprints after a round and can call `dvc add` when DVC is installed and enabled by policy

## Recommended Evolution

Keep the three orchestration entrypoints stable at the top level.

If the script count keeps growing, the next clean step is:

1. keep only execute/orchestration entrypoints flat
2. keep category views in sync
3. move reusable helper-only modules into `scripts/common/` or a future `scripts/lib/`
4. keep `README.md` and `SKILL.md` references stable during that migration

For exhaustive inventory and task-oriented lookup, see:

- `../references/script_index.md`
- `../references/operation_matrix.md`
