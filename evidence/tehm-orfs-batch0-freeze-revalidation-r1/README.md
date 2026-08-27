# Batch-0 source-freeze revalidation (2026-08-27)

This is a provenance-only exact-toolchain preflight for the RTL Batch-0 lane.
The runner executed `freeze` followed by `prepare` against the packaged ORFS
tree. No ORFS EDA stage, observation receipt, staging import, canonical-memory
mutation, or promotion attempt was performed.

The freeze binds the source spec, all TEHM/runner source files, each of the
seven config/SDC/RTL input sets, the selected ORFS dependency surface, and the
packaged OpenROAD/Yosys fingerprint. The
updated runner replays those bindings before later phases and rechecks each
materialized pair's config/SDC/RTL input binding and timing contract. Targeted
smokes also show that dependency and post-prepare input drift are rejected.

The scratch campaign is disposable and intentionally remains under `/tmp`.
The machine-readable values are in `freeze_revalidation_report.json`.
