# Architecture Layers

`nangate45-graph-expander` is organized as a layered execution system.

## L1 Resource Layer

Responsibilities:

- environment variables and local path overrides
- Python/toolchain selection
- `_downloads`, ORFS flow, dataset roots, mapping file

Main artifacts:

- `references/env.local.sh.template`
- `scripts/skill_env.py`

## L2 Atomic Capability Layer

Responsibilities:

- discover
- clone
- expand
- classify failure
- auto-fix
- score quality
- refresh manifest
- repair mapping drift

Representative scripts:

- `discover_download_candidates.py`
- `clone_repo_manifest.py`
- `auto_fix_failures.py`
- `score_download_repos.py`
- `score_design_quality.py`
- `refresh_expanded_raw_manifest.py`

## L3 Policy Layer

Responsibilities:

- decide what is allowed
- decide what is worth retrying
- decide what is eligible for publish
- define mutation and cleanup behavior

Machine-readable policy files:

- `candidate_policy.json`
- `repair_policy.json`
- `quality_policy.json`
- `mutation_policy.json`
- `publish_policy.json`

## L4 Task Layer

Responsibilities:

- run one expansion wave
- run until empty
- search until target
- validate publish readiness

Representative entrypoints:

- `run_expansion_round.py`
- `run_until_empty.py`
- `search_and_expand_until_target.py`
- `validate_publish_readiness.py`

## Notes

- Existing top-level entry commands remain stable.
- The layering is architectural, not a forced path reshuffle.
- This makes the skill easier to call from agents and easier to describe as a framework in a paper.
- In paper terms, the system now has both a production loop and an audit/publish gate loop.
