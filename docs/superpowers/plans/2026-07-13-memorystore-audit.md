# MemoryStore & A/B evidence-chain audit — findings + fixes (2026-07-13)

Source: an external read-only audit report (`docs/superpowers/plans/07-13-report.html`, Chinese) that
walked a 10-step MemoryStore / A-B evidence-chain sweep. This doc records which findings were **real code
bugs** (fixed here, with tests), which were **state/operational** (no code change), and the commit that
closed them. The report's own source paths (`r2g-skills/shared/_env.sh`, `r2g-rtl2gds/**`) are stale — every
claim was re-verified against the current tree before acting.

## Verdict per finding

| # | Report finding | Verdict | Action |
|---|---|---|---|
| 1 | `analyze_execution` string-only status (int exit code ⇒ every run `partial`) | **Real** | Fix #43 — delegate to canonical `_derive_orfs_status` |
| 2 | `orfs` vs `orfs_stage` recipe key; `rank_proposals` never called | **Real** | Fix #43 — canonical key + fallback, wired into `analyze()` |
| 3 | grandfathered promoted (0 of 87) | OK | none |
| 4 | promoted recipes lack judge-v2 decisive; 3 predate 2026-07-10 semantics | **Operational** | provenance stamping (#45) makes future promotions verifiable; re-running A/B on the 3 density_relief keys is a campaign action, not a code edit |
| 5 | reconcile dry-run 0 flips (judge-only, no version check) | OK w/ caveat | addressed by #45 tool-version stamp |
| 6 | `check_db_integrity` WARN = missing `journal.sqlite` | **State** | journal is gitignored machine-local; present in this workspace, green here |
| 7 | all `ab_trials` arm run-ids NULL — no experiment-level dedup | **Real (Critical)** | Fix #45 — resolve + stamp run-ids + provenance_complete |
| 8 | 29 wins recorded `abandoned`; 18 cross-symptom groups merged | **Real** | Fix #44 — `improved` outcome + per-symptom trajectory split |
| 9 | 78.6% inconclusive A/B; recipe_status overcount vs heuristics | **State/observability** | reason-code governance is a P2 follow-up (not done here) |
| 10 | `tool_versions_json` 0% filled; rtl-acquire projection empty-set = vacuous pass | **Real** | Fix #45 (tool_versions writer) + Fix #46 (empty-set honesty) |

## Fixes shipped (all TDD; failure-patterns.md #43–#46)

- **#43 MemoryStore contract unification** (`analyze_execution.py`). `_derive_status` delegates to
  `ingest_run._derive_orfs_status` (one normalizer; int/bool/float + `flow_scope`). `rank_proposals` reads
  canonical `fix_recipes["orfs_stage"][stage]` (legacy `orfs` fallback); `analyze()` attaches
  `learned_stage_ranking` so the operator triage tool consumes the 91 stage recipes it previously ignored.
- **#44 trajectory honesty** (`learn_heuristics.py`, `schema.sql`, `knowledge_db.py`). New `improved`
  outcome for a partial `win` (winner preserved, strictly below `resolved`). `fix_trajectories` PK grows
  `symptom_id` so a symptom-shifting session splits per symptom; a legacy-PK copy is dropped for recreation
  (pure re-derivable projection, zero data loss). Rebuilt store: `improved:46` (all with a winner), 250
  sessions split, honesty 5/5.
- **#45 provenance** (`engineer_loop.py`, `ab_runner.py`, `ingest_run.py`, new `tool_versions.py`).
  A/B trials back-reference both arms' run-ids + stamp `provenance_complete` + `tool_versions`; `record_trial`
  warns on a decisive verdict lacking distinct run-ids. `fix_events.tool_versions_json` finally has a writer
  (cached, fail-safe collector; `R2G_TOOL_VERSIONS_JSON` override).
- **#46 rtl-acquire dual-memory honesty** (`project_frontend_diagnosis.py`). `check_honesty` reports COVERAGE
  and treats the empty `synth_only` projection as UNPROVEN (not a vacuous `0==0` pass); `--require-nonempty`
  makes empty a hard failure (exit 2).

## Not done here (deliberate)

- **Re-run A/B on the 3 pre-2026-07-10 `density_relief` promotions** (finding 4) — an operational campaign
  action (`engineer_loop ab-enqueue`), not a code change. The code now stamps provenance so the re-run is
  verifiable.
- **Inconclusive-A/B reason-code governance** (finding 9, P2) — a larger judge-policy change; deferred.
- **`journal.sqlite` recoverable export / CI fixture** (finding 6, P2) — the WARN is by-design (gitignored
  machine-local); present in this workspace.

## Validation

signoff-loop **843 passed / 2 skipped**, rtl-acquire **61 passed**, honesty gates **5/5 GREEN** on the
rebuilt committed store (heuristics generation 6036; 91 `orfs_stage` recipes intact).
