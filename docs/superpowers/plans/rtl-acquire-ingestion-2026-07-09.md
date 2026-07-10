# Ingestion Plan — `nangate45-graph-expander` → r2g-skills 4th sub-skill (`rtl-acquire`)

**Date:** 2026-07-09  **Status:** PLAN (no code yet — "think how to ingest, don't code first")
**Source skill:** `/home/user5/nangate45-graph-expander` (~11.8K LOC Python, 41 scripts, 22
references, 6 tests; **not a git repo**; built for Codex/`$CODEX_HOME`, author paths `/home/yuany`).

## Decisions taken (user, 2026-07-09)

- **Ingestion shape = A** — a NEW 4th sub-skill under `r2g-skills/`, keeping its own
  `acquire → repair → validate → publish` pipeline as an **upstream corpus supplier**, under a
  documented scoped-reuse contract. (Not decompose-and-fold, not vendor-as-is.)
- **Converge onto existing r2g machinery:** (1) **env + toolchain**, (2) **ORFS invocation**,
  (4) **failure learning**.
- **Keep independent:** the net-new **acquire** front-end + **corpus-level dedup / quality / publish
  manifest** machinery.

### AMENDMENT (user, 2026-07-09, later same session) — 30pt format DROPPED

Reversing the earlier "keep 30pt independent" choice: the graph stage now **reuses def-graph's
`netlist_graph.py`** (emit `netlist_graph.pt`) instead of the 30pt/AutoGraph converter. Consequences:
- **BLOCKER-0 RESOLVED by deletion** — `verilog_to_30pt_graph.py`, the `30pt` base dataset, and the
  AutoGraph `mapping.txt` are all no longer needed. def-graph's converter is standalone-callable
  (`netlist_graph.py <yosys.v> <out.pt> [design]`), needs only a yosys netlist + per-platform std-cell
  liberty (no DEF / no P&R) — exactly what a synth-only expander produces.
- **Graph format is now a 5th convergence** — four of five subsystems converge (env, ORFS,
  failure-learning, graph); only **acquire + corpus dedup/quality/publish** stay skill-owned.
- **Drop the 30pt-specific validate machinery:** `mapping_change_sanity_check.py`,
  `repair_external_graph_mapping_drift.py`, `default_mapping.txt` — dead once 30pt is gone.
- **Publish reworked:** the merged manifest becomes a **corpus manifest over `netlist_graph.pt`**
  files (no base-30pt union). Dedup / quality-scoring / publish-gating logic is retained.

## What the source skill is (one paragraph)

A corpus-scale RTL **supply front-end + light expansion pipeline**: discover/search/clone RTL
(local `_downloads`, repo manifests, GitHub/GitLab keyword search) → screen (RAM/macro exclusion) →
synthesize to nangate45-mapped gate-level Verilog → convert to **`30pt`-compatible PyG graphs**
(`<design>_1_1_yosys.pt`, a *pre-layout synthesis-netlist* graph, sibling of def-graph's
`netlist_graph.pt`) → deterministic + LLM repair with a JSON failure KB → dedup / mapping-drift /
publish gating → refresh a **merged manifest**. It **never runs place/route or signoff**. Its
`execute/` stage delegates all real synth + graph work to two ORFS `flow/util/*` scripts.

## ✅ BLOCKER-0 — RESOLVED by the 30pt-drop amendment (was: missing 30pt engine)

> Superseded 2026-07-09: reusing def-graph's `netlist_graph.py` removes the dependency on the two
> missing ORFS util scripts + the 30pt dataset + mapping. The original analysis is kept below for the
> record; **no 30pt engine needs to be recovered.**

### (historical) ⛔ BLOCKER-0 — the 30pt engine is missing on this machine (gated everything)

`execute/` shells out to two scripts that are **absent everywhere on this box**, and there is no
`30pt` base dataset and no AutoGraph `mapping.txt` (only the bundled 3 KB `default_mapping.txt`):

| Missing artifact | Role | Mitigation |
| ---------------- | ---- | ---------- |
| `flow/util/expand_external_benchmark_dataset.py` | synth driver + dataset bookkeeping | **Neutralized by convergence #2** — `signoff-loop/run_orfs.sh` does synthesis. Reimplement its bookkeeping thinly. |
| `flow/util/verilog_to_30pt_graph.py` | **30pt graph converter** (the artifact the skill exists to make) | **Load-bearing + missing.** Must RECOVER (from the AutoGraph/ORFS fork it came from) or RECONSTRUCT from the 30pt schema + `default_mapping.txt`. Cannot substitute def-graph's converter — user chose to keep 30pt independent. |
| `$HOME/work/data/30pt` base dataset | merge target for the merged manifest; 30pt schema reference | Recover, or treat the expanded set as standalone (no base merge) for v1. |

**Open question O-1 (must answer before Phase 3):** where is the 30pt engine — the ORFS/AutoGraph
fork carrying `verilog_to_30pt_graph.py` + the `30pt` dataset? If unrecoverable, we reconstruct the
converter from the schema (bigger, but bounded — the mapping is bundled).

Present on this box: ORFS `/proj/workarea/user5/OpenROAD-flow-scripts`, openroad, yosys, `codex` CLI.

## Target: `r2g-skills/rtl-acquire/` (name provisional)

Fourth sibling to `eda-install`, `signoff-loop`, `def-graph`. **Heart = corpus acquisition/search**
(the genuinely net-new capability; r2g-skills has *zero* RTL discovery today — designs enter
one-at-a-time via `signoff-loop/scripts/project/init_project.py`). Positioned **upstream**: it feeds
a stream of screened RTL candidates that the other skills can consume.

Trigger description must disambiguate from `signoff-loop` (which also ingests RTL, but per-design for
signoff): rtl-acquire = *many designs, discovered/searched at corpus scale, synth-only → netlist
graphs*.

## Phased plan

### Phase 0 — Boundary contract + dependency recovery (no code)
- Answer **O-1** (30pt engine whereabouts). This gates Phase 3.
- Write the scoped-reuse contract (this doc) + a CLAUDE.md orientation stub: rtl-acquire OWNS
  acquire+30pt-expand+publish; BORROWS env (`_env.sh`), ORFS (`run_orfs.sh`), learning
  (`knowledge.sqlite`); its 30pt graph is a deliberate cousin of def-graph's `netlist_graph.pt`, not
  a duplicate to merge.

### Phase 1 — Vendor + portability scrub (mechanical, low risk)
- Copy `nangate45-graph-expander/` → `r2g-skills/rtl-acquire/` (into git-tracked tree).
- Scrub the **255** `/home/yuany` / `$CODEX_HOME` / `codex` / `OPENAI_API_KEY` occurrences.
  `portable_path()` defaults → r2g conventions.
- Make the **LLM-patch path optional/gated** (like def-graph's torch-venv SKIP): `codex` present here,
  but default OFF; OpenAI fallback needs a key → off by default; deterministic repair is first-line
  anyway (source skill already mandates this).
- Register in `r2g-skills/install.sh` (`SKILLS=(signoff-loop def-graph eda-install rtl-acquire)`),
  symlink-install. Get it loading + the 6 ported tests green under the new path.

### Phase 2 — Converge env + toolchain (decision #1)
- Replace `N45_GE_*` + `skill_env.py` + `setup_environment.py` with the shared **byte-identical
  `_env.sh`** (md5 `a5ac873e…`) + `references/env.local.sh` pin. Least-churn path: keep a thin
  `skill_env.py` that **delegates** to the shared env resolution rather than reimplements it.
- Map knobs: `N45_GE_FLOW_DIR`→`ORFS_ROOT/flow`, `N45_GE_PYTHON_BIN`→`R2G_GRAPH_PYTHON`; namespace the
  corpus roots as `R2G_ACQUIRE_{DOWNLOADS,WORKSPACE,OUT,BASE_DATASET,MAPPING,MERGED_MANIFEST}`.
- Extend `eda-install/scripts/setup/write_env_local.sh` to ALSO pin rtl-acquire's env (it already
  writes signoff-loop + def-graph); add the 30pt dataset / mapping / downloads root to
  `detect_env.sh` + `check_env.sh` (clean SKIP when the 30pt base is absent).

### Phase 3 — Converge ORFS invocation + graph (decisions #2 + amended #3)
- Route synthesis through `signoff-loop/scripts/flow/run_orfs.sh` (stop after synth) instead of
  `expand_external_benchmark_dataset.py`. Then call **def-graph's `netlist_graph.py`** on the produced
  yosys netlist → `netlist_graph.pt` (graph now converged, per the 30pt-drop amendment).
- **Split the (missing) bundled driver:** synth→`run_orfs.sh`, convert→`def-graph netlist_graph.py`,
  bookkeeping→a thin new `execute/` shim writing `_design_status/` + `index.csv`. No 30pt engine
  needed.
- Check: `netlist_graph.py` needs the per-platform `R2G_SC_LIB_FILES` std-cell vocab (nangate45
  liberty) — supplied by the shared env; confirm it builds `cell_type_id` from nangate45 std-cell
  liberty as it does inside def-graph's `run_graphs.sh`.
- **Honor Hard Rules at batch scale:** unique `DESIGN_NAME`+`FLOW_VARIANT` per candidate (the expander
  runs many designs — must derive a unique variant per design); cap `NUM_CORES` so
  `flows × NUM_CORES ≈ cores`; process-group kill on abort. These are already r2g invariants
  `run_orfs.sh` enforces — the win of converging.

### Phase 4 — Converge failure learning (decision #4)
- Route **synth-frontend** failures into `signoff-loop/knowledge/knowledge.sqlite`. Extend the symptom
  taxonomy `{check, class, predicates}` to model frontend classes (missing-include-dir,
  undefined-macro, RAM-inference, template-placeholder, macro-definition) currently living in
  `failure_families.json` / `failure_signatures.json`.
- Ingest each expansion round as `runs` + `failure_events` + `fix_events` (honesty invariant: **ingest
  after EVERY flow**). Deterministic auto-fixes + `exclude` decisions become `fix_trajectories`
  (negative learning).
- **Elegant mapping to the firewall:** keep the JSON casebook / diagnosis / LLM-patch queue as the
  **journal-side** (gitignored, high-volume, machine-local *hypotheses*); project distilled
  synth-frontend lessons into `knowledge.sqlite` **tables** — exactly the journal→knowledge promoter
  contract. `knowledge.sqlite` stays the single learner; the LLM-patch path is a hypothesis generator
  that never writes the learner directly.
- Verify: fast honesty check `count(runs where synth-fail) == count carrying a frontend
  failure_event`.

### Phase 5 — Keep independent (per user): acquire + corpus dedup/quality/publish
- Retain `acquire/` (discover/search/clone/screen), `report/` (repo + design quality scoring),
  `hygiene/` (dedup), and `publish/` **reworked** to a corpus manifest over `netlist_graph.pt` (no
  base-30pt union).
- **Delete the 30pt-specific validate machinery** (`mapping_change_sanity_check.py`,
  `repair_external_graph_mapping_drift.py`, `references/default_mapping.txt`) — dead once 30pt is gone.
  Keep dedup + `validate_publish_readiness.py` (retargeted at the netlist-graph corpus).

### Phase 6 — Docs + honesty + end-to-end verification (the r2g way)
- CLAUDE.md: add rtl-acquire to Project Layout, the skill list, a ⭐-style one-liner ("rtl-acquire →
  the RTL corpus supplier"), "Where to Find X" rows, Hard-Rules note (unique variant per batched
  design).
- Port the 6 tests + add convergence tests: env via `_env.sh`; frontend-failure→`knowledge.sqlite`
  ingest honesty; `run_orfs.sh` synth→`1_1_yosys`→30pt path.
- **E2E smoke:** one `discover → synth → 30pt-graph → ingest → publish` round on a small local RTL;
  assert the merged manifest refresh, a loadable `.pt`, and a frontend `failure_event` in
  `knowledge.sqlite`.

## Risks
1. ~~BLOCKER-0 (30pt engine missing)~~ **RESOLVED** by the 30pt-drop amendment (reuse def-graph
   `netlist_graph.py`). New minor risk: **publish/manifest rework** off `netlist_graph.pt` (drop
   base-30pt union) — bounded.
2. **`codex`/OpenAI LLM-patch path** — must be optional/gated; default OFF.
3. **nangate45-only scope** — v1 stays nangate45-scoped; platform generalization is a *separate later
   effort*, not part of ingestion (avoid scope creep).
4. **Batch-concurrency Hard Rules** — many designs at once; NUM_CORES cap + unique FLOW_VARIANT or it
   violates "never two configs same DESIGN_NAME+FLOW_VARIANT concurrently."

## Definition of done (honesty invariants, r2g style)
- rtl-acquire loads as a symlinked 4th skill; tests green; `check_env.sh`-style verify passes or SKIPs
  cleanly when the 30pt base is absent.
- A synth-frontend failure produces a `knowledge.sqlite` `failure_event` (no silent-lie: fail run with
  empty `failure_events` = loop bug).
- The 30pt `.pt` output loads and the merged manifest refresh runs under publish gating.
- CLAUDE.md + this plan reflect reality (per `feedback_update_plan_spec_docs`).

---

## IMPLEMENTED (2026-07-09, same day) — all phases landed in one pass

Status: **DONE** (commit: see `feat(skill): rtl-acquire` in git log). All six phases executed;
the E2E smoke (discover → run_orfs synth → netlist_graph.pt → knowledge ingest → classify →
frontend-diagnosis projection → dedup → quality → publish gate PASS → merged manifest) is green
on a 2-candidate corpus (1 clean, 1 deliberately broken).

Reality-check corrections to this plan discovered during implementation:

- **BLOCKER-0's premise was stale**: `expand_external_benchmark_dataset.py`,
  `verilog_to_30pt_graph.py`, and `inspect_30pt_schema.py` were NOT missing — the source skill
  bundles fallback copies under `resources/orfs_util/` (`skill_env._default_or_bundled_script`).
  Moot either way: the 30pt amendment stands; the bundled copies were never vendored.
- **Vendor exclusions**: `optional_tools/build/` (1215 files), the 13MB tarball, `__pycache__`,
  the author's `references/env.local.sh`, the byte-identical `scripts/execute/*.py` +
  `scripts/common/skill_env.py` duplicates (the common/ copy had a broken SKILL_DIR assumption).
- **ORFS netlist name drift**: current ORFS emits the mapped netlist as `1_2_yosys.v`
  (`1_1_yosys.v` exists only as `..._canonicalize.rtlil`); `expand_candidates.synthesize()`
  probes both names.
- **First-round chicken-and-egg**: `check_mapped_netlist_duplicates.py` and
  `summarize_dataset_scale.py` read the merged manifest, which is refreshed LATER in the round —
  both now fall back / SKIP cleanly when it doesn't exist yet.
- **`flow_scope` (new, beyond plan)**: a synth-only pass would have ingested as `partial`,
  flooding the A/B planner with runs that were never signoff subjects. Added `runs.flow_scope`
  ('full' default | 'synth_only'), derived from config.mk `export R2G_FLOW_SCOPE = synth_only`;
  `_derive_orfs_status` requires only synth for that scope (mirrors the `is_bench` pattern —
  learning-read filterable, failure_events always written). Migration is idempotent; committed
  store migrated (schema-only), honesty gates 5/5 green, signoff-loop suite 790/790.
- **Frontend classes ride the EXISTING ingest hook**: `reports/diagnosis.json` `issues[].kind`
  → failure_events, so `scripts/knowledge/project_frontend_diagnosis.py` projects the
  classifier's reason (`synth-frontend-<class>`) + the exclude decision (fix_events,
  `acquire_exclude`/`no_change` = negative learning) and re-ingests idempotently.
  Fast honesty check: `project_frontend_diagnosis.py --check <db>`.
- **Known footgun (future work)**: `repair/refresh_failure_knowledge_base.py` full-rewrites the
  curated corpus section of `references/failure_knowledge_base.md` from the LOCAL workspace —
  a small fresh corpus wipes the shipped corpus knowledge (restored from source after the smoke).
  Consider a merge-not-clobber refresh or moving the generated section out of the curated file.
- Phase 2 extras: eda-install `write_env_local.sh` now targets rtl-acquire too;
  `check_env.sh` gained a `[corpus expansion (rtl-acquire)]` section; repo `.gitignore` now
  excludes `r2g-skills/*/references/env.local.sh*` (machine pins).
- Tests: rtl-acquire 21 passed (incl. new `test_flow_scope_ingest.py` proving the Phase-4
  contract against the REAL ingest — no mocks); signoff-loop 790 passed; LLM path hard-gated
  behind `R2G_ACQUIRE_ENABLE_LLM=1` (default OFF).

> **Post-ingestion residue sweep (2026-07-09, branch `chore/housecleaning-2026-07-09`):** a
> whole-repo dead-code audit deleted the last pre-ingestion carry-overs that 7dce0ed's
> policies+docs sweep missed — `repair/run_local_llm_patch_agent.py` (the local Codex-CLI
> executor incl. all CODEX_HOME machinery; the OpenAI API executor is now the only shipped LLM
> path), `hygiene/deduplicate_external_index.py` + `hygiene/deduplicate_external_by_canonical_source.py`
> (2026-04-07 quarantine one-offs, superseded by `check_mapped_netlist_duplicates.py`), and
> `publish/build_synth_variant_dataset_manifest.py` (keyed to the retired `*_1_1_yosys.pt`
> naming). Stale `nangate45-graph-expander` self-identifiers (run manifests' `workflow` field,
> discovery User-Agent, README/`common/__init__` headers) renamed to `rtl-acquire`. Suite still
> 21 passed.
