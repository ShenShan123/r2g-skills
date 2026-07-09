# references

This directory stores stable policy inputs and human-maintained reference material for `nangate45-graph-expander`.

It is intentionally different from:

- `scripts/`
  - executable logic
- `tests/`
  - minimal regression protection for helper behavior
- runtime outputs under dataset roots
  - generated logs, indexes, stage traces, and expansion artifacts

## What Belongs Here

- curated failure playbooks
- repair policy defaults
- environment override templates
- stable taxonomies and scoring policies

## Current Files

- `env.local.sh.template`
  - machine-local environment override template
  - copy to `env.local.sh` for local use
  - do not commit machine-specific secrets or paths into scripts

- `failure_knowledge_base.md`
  - curated failure diagnosis and repair guidance
  - mixes hand-maintained core knowledge with refreshed recurring patterns

- `failure_strategy.json`
  - structured repair/exclude strategy policy used by auto-fix

- `repair_deny_policy.json`
  - negative-KB policy for "do not repair" / "do not publish" classes

- `failure_case_schema.md`
  - schema for structured symptom / diagnosis / repair / publish records
  - also covers the compact auto-generated diagnosis layer

- `candidate_policy.json`
  - machine-readable intake/discovery defaults

- `repair_policy.json`
  - machine-readable repair and retry controls

- `quality_policy.json`
  - machine-readable scoring/report controls

- `mutation_policy.json`
  - machine-readable controls for state-changing actions

- `publish_policy.json`
  - machine-readable validation/publish gate controls

- `failure_family_taxonomy.md`
  - normalized failure classes and the intended repair/exclude direction

- `design_quality_policy.md`
  - interpretation of design-level quality signals and keep/conditional/reject policy

- `architecture_layers.md`
  - layered architecture description for the framework

- `script_index.md`
  - exhaustive script inventory

- `operation_matrix.md`
  - quick task-to-script navigation with side effects and outputs

- `definition_of_done.md`
  - full round-completion checklist and status interpretation

- `llm_api_integration.md`
  - provider-agnostic contract for request / result / validation around external LLM repair execution

## What Should Not Go Here

- user-facing quick-start commands
  - keep those in `README.md` or `SKILL.md`
- runtime-generated per-round reports
  - keep those under dataset output roots
- unit tests or smoke tests
  - keep those in `tests/`
