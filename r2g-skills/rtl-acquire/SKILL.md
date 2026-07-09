---
name: rtl-acquire
description: Discover, screen, and acquire RTL at corpus scale (local trees, repo manifests, keyword search) and expand it — synthesis-only, MANY designs per round — into pre-layout netlist_graph.pt PyG graphs with dedup, quality scoring, and publish gating. Use for growing a training corpus of netlist graphs from found RTL. NOT for taking one design to GDS/signoff (that is signoff-loop) and NOT for post-layout graph datasets from DEF/SPEF (that is def-graph).
---

# rtl-acquire — the RTL corpus supplier

Execute a staged, artifact-first corpus-expansion workflow for discovered RTL:
**acquire → expand (synth-only) → repair → validate → publish**. Prefer
deterministic scripts and policy files; treat the workspace ledgers and
manifests as the source of truth.

Positioned **upstream** of the other r2g-skills: it feeds a stream of screened,
synthesized, graph-converted designs. It **never runs place/route or signoff**
— hand a promising design to `signoff-loop` for that.

## The scoped-reuse contract (what this skill OWNS vs BORROWS)

OWNS (the heart — genuinely net-new for r2g):
- **acquire/** — discovery/search/clone/screen of candidate RTL at corpus scale
  (local `_downloads` trees, repo manifests, keyword search), RAM/macro
  exclusion, bundle-aware candidate CSVs, incremental scan ledgers.
- **corpus hygiene + publish** — rtl/netlist signature dedup, repo/design
  quality scoring, publish eligibility gating, the merged corpus manifest.
- **repair/** — deterministic frontend repair (include dirs, stubs, memory
  limits, template materialization) + the JSON failure casebook (journal-side).

BORROWS (converged onto sibling sub-skills — never reimplement these here):
- **env/toolchain** — the shared byte-identical `scripts/flow/_env.sh` +
  `references/env.local.sh` pin written by eda-install (`skill_env.py` is a
  thin delegate over it).
- **ORFS synthesis** — `signoff-loop/scripts/flow/run_orfs.sh` with
  `ORFS_STAGES=synth` (per-candidate project dirs; the r2g Hard Rules hold by
  construction).
- **graph format** — `def-graph/scripts/extract/graph/netlist_graph.py`
  produces every corpus graph (`netlist_graph.pt`); the legacy 30pt converter
  is retired (2026-07-09 amendment).
- **failure learning** — every flow ingests into signoff-loop's
  `knowledge/knowledge.sqlite` (`R2G_FLOW_SCOPE = synth_only`); frontend
  failure classes land as `synth-frontend-<class>` failure_events.

## Default Paths (all overridable, `R2G_ACQUIRE_*`)

- acquire root: `<repo>/design_cases/_rtl_acquire` (`R2G_ACQUIRE_ROOT`)
- downloads root: `$R2G_ACQUIRE_ROOT/_downloads` (`R2G_ACQUIRE_DOWNLOADS`)
- workspace: `$R2G_ACQUIRE_ROOT/workspace` (`R2G_ACQUIRE_WORKSPACE`)
  - `candidates/` `scan_state/` `failures/` `quality/` `audits/` `runs/`
    `manifests/` — the ledger surfaces
  - `synth_projects/<design>/` — per-candidate ORFS project dirs
    (constraints/config.mk + constraint.sdc + backend/ + reports/)
- corpus (success root): `$R2G_ACQUIRE_ROOT/corpus` (`R2G_ACQUIRE_OUT`) —
  per-design `netlist_graph.pt`, `mapped_netlist.v`, `cell_stats.json`,
  `design_meta.json`, plus `index.csv` + `_design_status/`
- ORFS seed corpus (optional): `$R2G_ACQUIRE_ROOT/orfs_seed_designs`
  (`R2G_ACQUIRE_SEED_ROOT`)
- merged manifest: `$R2G_ACQUIRE_ROOT/netlist_graph_corpus_manifest.csv`
  (`R2G_ACQUIRE_MERGED_MANIFEST`)

## Environment Resolution

Never hand-configure tools here. Resolution order (same as every r2g skill):
shell env > `$R2G_ENV_FILE` > `references/env.local.sh` (written by
eda-install's `write_env_local.sh`) > `_env.sh` autodetection. Verify with
`python3 scripts/skill_env.py` (prints every resolved root/tool) or the
comprehensive `signoff-loop/scripts/flow/check_env.sh`.

Knobs specific to this skill:

- `R2G_ACQUIRE_PLATFORM` — target ORFS platform (default `nangate45`; v1 scope)
- `R2G_ACQUIRE_SYNTH_TIMEOUT` — per-candidate synth timeout s (default 3600)
- `R2G_ACQUIRE_NUM_CORES` — cap ORFS `NUM_CORES` per flow (**Hard Rule**:
  concurrent flows × cores ≈ machine cores)
- `R2G_GRAPH_PYTHON` — torch venv for graph conversion + scale reports; when
  unset those stages **SKIP with a HINT** and designs record `graph_skipped`
  (never `success`)
- `R2G_KNOWLEDGE_DB` — override the knowledge DB (tests only; default is the
  committed signoff-loop store)
- `R2G_ACQUIRE_ENABLE_LLM=1` — opt-in for the LLM patch path (default OFF)

## Mandatory Stage Order

### 1. Acquire
Gather candidate RTL from `_downloads`, repo manifests, or prebuilt CSVs.
- `scripts/acquire/discover_download_candidates.py`
- `scripts/acquire/discover_repo_manifest_candidates.py`
- `scripts/acquire/clone_repo_manifest.py`
Outputs: candidate CSV + updated `scan_state/downloads_scan_state.json`.

### 2. Expand (synth-only, converged)
`scripts/execute/expand_candidates.py` per candidate: sanitize RTL (encoding,
helper modules, iscas89 dff) → write `synth_projects/<design>/` → synth via
**run_orfs.sh** (`ORFS_STAGES=synth`, FLOW_VARIANT = the unique candidate id)
→ sv2v/vhd2vl fallback + LEC-lite when needed → dedup by rtl/netlist
signature → convert via **def-graph netlist_graph.py** → `cell_stats.json`
(liberty-driven seq/comb split) → **ingest into knowledge.sqlite** (every
flow, pass or fail).
Outputs: corpus dir updates, refreshed `index.csv`, `_design_status/`.

### 3. Repair
- `scripts/repair/classify_failed_candidates.py` → retry vs exclude + class
- `scripts/repair/auto_fix_failures.py` (deterministic first-line)
- `scripts/knowledge/project_frontend_diagnosis.py` — projects the final class
  into each failed project's `reports/diagnosis.json` + `fix_log.jsonl` and
  re-ingests, so knowledge carries `synth-frontend-<class>` events and the
  exclude decision (negative learning)
- casebook/diagnosis/strategy refreshers (journal-side JSON)

### 4. Validate
- `scripts/validate/check_mapped_netlist_duplicates.py` (cross-corpus dedup)
- `scripts/validate/audit_near_duplicates.py`
- `scripts/validate/validate_publish_readiness.py` → `quality/publish_validation.json`

### 5. Publish
- `scripts/report/score_design_quality.py` + `score_download_repos.py`
- `scripts/publish/build_publish_candidates.py` → publish eligibility
- `scripts/publish/refresh_expanded_raw_manifest.py` → the merged
  netlist-graph corpus manifest (only when the validation gate passes)

### 6. Snapshot
- `scripts/publish/record_dataset_snapshot.py` → `runs/dataset_snapshot_latest.json`

### Running a full round
```bash
python3 scripts/run_expansion_round.py --discover --run-retry
# loops: scripts/run_until_empty.py, scripts/search_and_expand_until_target.py
```
Check `workspace/runs/run_manifest_latest.json` + `quality/publish_validation.json`;
a round is fully successful only when publish gating and the manifest refresh
completed as expected.

## Hard Rules

- **Never two concurrent candidates with the same DESIGN_NAME + FLOW_VARIANT.**
  The expand stage derives FLOW_VARIANT from the unique candidate id — keep
  candidate `design` values unique per round.
- **Cap concurrency**: `R2G_ACQUIRE_NUM_CORES` so flows × cores ≈ machine.
- **Ingest after EVERY flow** — pass or fail. A synth-fail run with no
  `failure_event` in knowledge.sqlite is a loop bug (check with
  `scripts/knowledge/project_frontend_diagnosis.py --check <db>`).
- **A SKIPped graph stage is not success** — `graph_skipped` designs are not
  publish-eligible; provision `R2G_GRAPH_PYTHON` and re-run.
- **Do not treat synthesis success as publish success** — publish gating +
  the merged-manifest refresh define publish.
- **Deterministic repair before LLM.** The LLM patch path is default OFF
  (`R2G_ACQUIRE_ENABLE_LLM=1` to opt in; OpenAI fallback additionally needs
  `OPENAI_API_KEY`). Do not route template-placeholder failures to LLM when
  deterministic `template_materialization` applies.
- **Never mutate the ORFS checkout or another skill's tree** — this skill
  writes only under its own roots + the knowledge DB via the ingest contract.
- v1 is **nangate45-scoped**; platform generalization is a separate effort.

## Definition Of Success (keep these separate)

- **execution success** — ORFS synth + graph conversion succeeded (`success`
  in `index.csv`, loadable `netlist_graph.pt`, `cells > 0`)
- **repair success** — a retry/deterministic fix made a failing design
  runnable (closes its fix trajectory as `cleared`)
- **validation success** — dedup/quality/publish checks passed
- **publish success** — design appears in `publish_eligible_designs` AND the
  merged manifest refresh included it
- **learning success** — the round's runs are in knowledge.sqlite
  (`flow_scope='synth_only'`), synth-fails carry `synth-frontend-*` events

## References

- task → script lookup + command templates: `references/operation_matrix.md`
- full script inventory: `references/script_index.md`
- candidate CSV schema: `references/candidate_csv_schema.md`
- failure KB + taxonomy: `references/failure_knowledge_base.md`,
  `references/failure_family_taxonomy.md`
- policy files (candidate/repair/quality/publish/…): `references/*.json`
- ingestion plan + convergence decisions:
  `docs/superpowers/plans/rtl-acquire-ingestion-2026-07-09.md`
