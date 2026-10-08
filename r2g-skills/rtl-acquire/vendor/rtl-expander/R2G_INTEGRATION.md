# R2G Integration Note

This directory vendors the complete `rtl-expander` package supplied from
`/home/yangao/yuanyue/rtl-expander` on 2026-08-20. The source directory was not
a Git worktree, so no upstream commit exists to record. Generated Python cache
files were excluded; source, tests, agents, and references were copied intact.
The copied upstream tree (relative-path ordered file SHA-256 list, before R2G
additions) has SHA-256
`00c7a994d74da39ded99da3de9e6610ed1130b80821cd442e4b3d9a4deff9a20`.

R2G keeps `rtl-acquire` as the public sub-skill boundary. The expander produces
certified RTL corpus snapshots; `../../scripts/acquire/import_expander_snapshot.py`
verifies and translates those snapshots into the existing candidate contract.
Do not couple downstream ORFS, signoff, or graph stages to expander frontier or
queue internals.

Formal Experiment-1 intake is deliberately two-level. Expander supplies the
broad certified discovery pool; R2G then performs the frozen platform
qualification and creates a digest-bound, diversity-aware selection manifest.
The selector balances certified functional ontology, repository, and mapped
size while enforcing the compilation-closure and repository-cap gates. High
predicted-cost and measured oversize candidates are retained in supplementary
tracks rather than deleted or counted as RTL failures.

Cold/Warm experiments use `scripts/scheduler_memory.py`. Its Warm artifact is
scheduler-only and intentionally excludes prior repositories, candidates,
exact queries, cursors, attempts, and provider state. Yield rows are reduced to
query-family aggregates so Warm cannot replay Cold's expanded query strings.
