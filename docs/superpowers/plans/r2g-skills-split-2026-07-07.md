# Skill split: `r2g-rtl2gds` → `r2g-skills` (`signoff-loop` + `def-graph`)

**Date:** 2026-07-07  ·  **Branch:** `feat/skill-split-r2g-skills`  ·  **Worktree:** `/proj/workarea/user5/agent-r2g-skill-split`

## What & why

The single `r2g-rtl2gds` skill carried two distinct responsibilities. Split into a **skill
collection** `r2g-skills/` with two independently-triggered Claude Code skills:

- **`signoff-loop`** — RTL→GDS flow with full signoff (DRC/LVS/RCX) **and** the self-improvement
  observation→ingest→act loop (the two memory DBs + `engineer_loop`) that eliminates DRC/LVS
  violations and closes timing at the best Fmax.
- **`def-graph`** — graph dataset construction from the clean, signed-off physical design
  (DEF/LEF/SPEF): the five graph views (b–f), the tech-lib/LEF/DEF parser, and feature/label
  extraction (congestion, RC, wirelength, timing, IR drop).

`signoff-loop` **produces** the signed-off `6_final.def`/`.odb`/`.spef`; `def-graph` **consumes**
them. Neither imports the other.

## Boundary analysis (why the split is clean)

Verified before moving anything:

- **Python imports are clean in BOTH directions.** `scripts/extract/{techlib,labels,features,graph}`
  form a closed import cluster wired only via `__file__`-relative `sys.path` bootstrap; no signoff
  module imports them and they import no signoff module.
- **`report_io.py`** is imported only by the 7 signoff extractors → stays signoff.
- **`techlib/`** is imported only by labels/features/graph → moves to def-graph.
- **`presynth.py`** was *misfiled* under `features/`: stdlib-only, NOT part of `run_features.sh`'s
  worker loop, and feeds the signoff KNN config-suggester (`suggest_config`/`ingest_run`). Moved to
  `signoff-loop/scripts/extract/presynth.py`, which **dissolves the only producer/consumer data
  contract** (`reports/presynth_features.json`) across the boundary.
- **Shell coupling:** `_env.sh` is shared (each skill ships a byte-identical copy);
  `resolve_platform_paths.sh` is called only by graph runners → moves to def-graph.

## File mapping

| From `r2g-rtl2gds/…` | To |
|---|---|
| everything (baseline) | `r2g-skills/signoff-loop/` (git mv of the whole tree) |
| `scripts/extract/{techlib,labels,features,graph}/` | `r2g-skills/def-graph/scripts/extract/…` |
| `scripts/extract/features/presynth.py` | `r2g-skills/signoff-loop/scripts/extract/presynth.py` |
| `scripts/flow/{run_labels,run_features,run_graphs,resolve_platform_paths}.sh` | `r2g-skills/def-graph/scripts/flow/…` |
| `scripts/flow/_env.sh` | kept in signoff-loop; **copied** into def-graph |
| `references/{graph-dataset,feature-extraction,label-extraction}.md` | `r2g-skills/def-graph/references/…` |
| 18 graph tests + `fixtures/corner_synth.py` | `r2g-skills/def-graph/tests/…` |
| `install.sh` | `r2g-skills/install.sh` (installs BOTH sub-skills) |

## Path-depth fixes (the one real hazard)

Moving the skill one level deeper (`<repo>/r2g-rtl2gds` → `<repo>/r2g-skills/<skill>`) broke every
`__file__`-relative path that reached *outside* the skill:

- **`_env.sh`** ORFS-sibling autodetect: `$_R2G_SKILL_DIR/../..` → `../../..`.
- **Tests reaching repo-root `tools/` / `design_cases/`:** `parents[2]` → `parents[3]` in
  `test_check_ledger_signoff_backed`, `test_check_db_integrity`, `test_mk_sky130_project`,
  `test_setup_sizing`, `test_setup_platform_cli` (signoff) and `test_techlib_def_parse`,
  `test_techlib_crossplatform` (def-graph); `test_verify_graph_dataset_helpers` dirname×3 → ×4.
- **NOT changed:** `test_loop_fmax_drain.py`'s `parents[2]` is relative to `engineer_loop.__file__`
  (`scripts/loop/…`), which still resolves to the skill root — intra-skill math is depth-agnostic.

The worktree's gitignored `design_cases/` is absent, so several of these would have *silently
skipped* rather than errored — fixed anyway for correctness in a real checkout.

## Packaging & external refs

- `.claude-plugin/plugin.json`: `name: r2g-skills`, `skills: [r2g-skills/signoff-loop, r2g-skills/def-graph]`.
- `.gitignore`: knowledge-store paths `r2g-rtl2gds/knowledge/…` → `r2g-skills/signoff-loop/knowledge/…`.
- `tools/` (24 files): signoff callers → `r2g-skills/signoff-loop/…`; graph callers
  (`run_features_batch.sh`, `run_labels_batch.sh`, `regen_extract_baseline.sh`,
  `verify_graph_dataset.py`) → `r2g-skills/def-graph/…`; `backfill_presynth_features.py` also had its
  presynth path de-nested (no more `features/`).
- `CLAUDE.md` + `README.md`: structural sections (layout, deployment, "Where to Find X", install)
  rewritten for the two-skill world.
- Historical `docs/` narratives left as-is (record of what was true then).

## Superseded invariants

- Knowledge store now lives at **`r2g-skills/signoff-loop/knowledge/knowledge.sqlite`** (tracked,
  committed) — the honesty gate is `python3 r2g-skills/signoff-loop/knowledge/honesty.py --db
  r2g-skills/signoff-loop/knowledge/knowledge.sqlite`.
- Deployment is now **two symlinks**: `.claude/skills/signoff-loop` and `.claude/skills/def-graph`
  (`bash r2g-skills/install.sh --project . --link`).
- The committed `knowledge.sqlite` binary is **unchanged** (historical `runs.extra_config_json` /
  `lessons.source_doc` strings still say `r2g-rtl2gds` — immutable provenance, never rewritten).

## Verification

Baseline (whole suite in the fresh worktree, torch venv, pinned env): **1071 passed, 15 skipped**.
After split: **signoff-loop 790 passed / 1 skipped** + **def-graph 281 passed / 14 skipped** =
**1071 / 15** — behavior-preserving, zero regressions.

**Run the suites per-skill** (each skill is self-contained):
```bash
R2G_ENV_FILE=<env> <venv>/bin/python -m pytest r2g-skills/signoff-loop/tests -q
R2G_ENV_FILE=<env> <venv>/bin/python -m pytest r2g-skills/def-graph/tests   -q
```
A single `pytest r2g-skills/` process CANNOT collect both at once: both suites live in a directory
named `tests/`, so pytest maps both `conftest.py` to the module `tests.conftest` and aborts with
`ImportPathMismatchError` / "Plugin already registered" (a pytest limitation for two identically
named test packages, in every import-mode — not a regression). Run them as two invocations.
