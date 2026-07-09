# Definition of Done

Once a round is complete, the expected outputs are:

- `nangate45_graph_expander_status.json` ends in `completed` or `blocked_validation`
- `nangate45_graph_expander_status.log` contains a round summary
- external index refreshed:
  - `external_benchmarks_nangate45_expand/index.csv`
  - `external_benchmarks_nangate45_expand/index_success_only.csv`
- latest merged manifest refreshed when publish gate allows it
- failure/repair artifacts refreshed:
  - `failed_candidates_exclude.csv`
  - `failed_candidates_retry_candidates_autofix.csv` when repairable failures exist
  - `auto_fix_plan.json`
  - `repair_action_log.json`
  - `failure_signatures.json`
  - `failure_signature_actions.json`
  - `failure_families.json`
  - `failure_strategy.json`
- design trace artifacts written during batch execution:
  - `<out_root>/_design_status/<design>.json`
  - `<out_root>/_design_status/<design>.jsonl`
  - `<out_root>/_design_status/design_stage_index.json`
- quality/report artifacts refreshed:
  - `download_repo_quality.csv/.md`
  - `design_quality_scores.csv/.md`
  - `expanded_raw_graph_scale_report.csv/.md`
  - external summary
- publish artifacts refreshed:
  - `publish_validation.json/.md`
  - `publish_eligible_designs.csv/.json/.md`
- structured failure outputs refreshed:
  - `failure_casebook.json/.jsonl/.md`
  - `failure_diagnosis.json/.jsonl/.md`
  - `llm_repair_cases.json/.jsonl/.md` when enabled
  - `llm_patch_requests.json/.jsonl/.md` when generated
  - `llm_patch_result_evaluation.json/.md` after external API execution
- orchestration ledger refreshed:
  - `runs/run_manifest_latest.json`
  - `runs/dataset_snapshot_latest.json/.md` when enabled
- dedup artifacts refreshed when enabled:
  - `mapped_netlist_duplicate_report.csv`

Operational interpretation:

- `completed`
  - workflow ran to the expected end state and publish gate did not block manifest refresh
- `blocked_validation`
  - execution succeeded but publish gate prevented merged-manifest publication
- `failed`
  - one of the required workflow phases exited non-zero
