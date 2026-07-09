# Failure Case Schema

Structured failure records should separate:

- symptom
- diagnosis
- repair
- evidence
- publish consequence

Compact diagnosis artifacts can also be emitted automatically with:

- symptom
- likely cause
- attempted repair
- evidence
- next best action

Core fields:

- `design`
- `status`
- `failure_stage`
- `tool_name`
- `surface_signature`
- `semantic_signature`
- `failure_class`
- `symptom_pattern`
- `root_cause_hypothesis`
- `evidence_features`
- `repair_action_candidates`
- `repair_preconditions`
- `risk_level`
- `fidelity_risk`
- `post_repair_checks`
- `publish_policy`
- `fallback_if_failed`

This schema is implemented by:

- `scripts/repair/build_failure_casebook.py`

It is intended to support:

- repeatable diagnosis
- repair policy learning
- negative-KB enforcement
- publish-vs-debug-pool separation

Related script:

- `scripts/repair/build_failure_diagnosis.py`
