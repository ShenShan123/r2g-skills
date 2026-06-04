# Agent-with-OpenROAD — Project Guide

AI-driven open-source EDA flow: natural-language spec → GDSII via OpenROAD-flow-scripts
(ORFS), with full signoff (DRC, LVS, RCX). Implemented as the `r2g-rtl2gds` Claude Code
skill.

This file orients you to the repo. The skill itself documents *how* to run the flow,
debug failures, and tune for known design families. **Do not duplicate skill content
here.** When you fix a bug, update `r2g-rtl2gds/` (the skill) — not this file.

## Project Layout

```
r2g-rtl2gds/                  # The skill (everything to run a flow lives here)
  SKILL.md                      # Workflow, hard rules, env knobs (PLACE_FAST, ROUTE_FAST, …)
  scripts/flow/                 # Stage runners: run_orfs.sh, run_drc.sh, run_lvs.sh, run_rcx.sh, …
  scripts/extract/              # Parse tool output → JSON: extract_ppa, extract_drc, extract_lvs, …
    techlib/                      # Shared per-platform tech layer: profile, resolve, def_parse, lef, liberty, cell_types
    labels/                       # Dataset label extractors (congestion, wirelength, timing, irdrop) + stats roller
    features/                     # Dataset feature extractors (graph nodes/edges/metadata) + stats roller
  scripts/project/              # init_project, normalize_spec, validate_config
  scripts/reports/              # check_timing, build_diagnosis, build_run_history, …
  scripts/dashboard/            # render_gds_preview, generate/serve dashboard
  knowledge/                    # Self-contained knowledge store (SQLite + Python)
  references/                   # Detailed docs (see "Where to find X" below)
  assets/                       # Templates, examples, bundled platform extras (e.g. nangate45 LVS rule)
  tests/                        # pytest suite
tools/                          # Repo-level operator tooling + installers
design_cases/                   # All design runs (gitignored)
  <design-name>/                  # One project per directory
    labels/                         # Per-cell/per-net dataset label CSVs (run_labels.sh) + reports/labels_stats.json
    features/                       # Per-node/per-edge/metadata feature CSVs (run_features.sh) + reports/features_stats.json
  _batch/                         # Batch results, jsonl, per-design logs
  _dashboard/                     # Auto-generated HTML dashboard
```

## Toolchain (autodetected by the skill)

`scripts/flow/_env.sh` autodetects ORFS + tool paths. You don't need to source anything
manually. Override any value via `$R2G_ENV_FILE`, `references/env.local.sh`, or by
exporting `ORFS_ROOT`, `OPENROAD_EXE`, `YOSYS_EXE`, `KLAYOUT_CMD`, etc. before invoking
a script.

| Required | Optional |
|----------|----------|
| python3 (3.10+), yosys, openroad, ORFS checkout | iverilog/vvp, verilator, klayout, magic, netgen-lvs, opensta, sky130A PDK |

Verify with `bash r2g-rtl2gds/scripts/flow/check_env.sh`.

ORFS platforms shipped with this checkout: `nangate45` (default), `sky130hd`, `sky130hs`,
`asap7`, `gf180`, `ihp-sg13g2`. The nangate45 LVS rule (`FreePDK45.lylvs`) is bundled at
`r2g-rtl2gds/assets/platforms/nangate45/lvs/`; install once with
`tools/install_nangate45_lvs.sh`.

## Flow Execution Order (Strict)

The skill enforces this order. Don't skip a failed stage — diagnose first via
`r2g-rtl2gds/references/failure-patterns.md`.

1. **Spec** — `scripts/project/normalize_spec.py` → `input/normalized-spec.yaml`
2. **RTL** — author or generate `rtl/design.v`
3. **Testbench** — `tb/testbench.v`
4. **Lint** — `scripts/flow/run_lint.sh`
5. **Simulation** — `scripts/flow/run_sim.sh`
6. **Synthesis** — `scripts/flow/run_synth.sh`
7. **Backend** — `scripts/flow/run_orfs.sh <project-dir> [platform]`
8. **PPA + Timing gate** — `scripts/extract/extract_ppa.py` then `scripts/reports/check_timing.py`
   (tiered WNS/TNS; auto-fix minor, stop and present options for moderate/severe)
9. **DRC** — `scripts/flow/run_drc.sh`
10. **LVS** — `scripts/flow/run_lvs.sh`
11. **RCX** — `scripts/flow/run_rcx.sh`
12. **Reports + dashboard** — `scripts/extract/extract_{drc,lvs,rcx}.py`,
    `scripts/reports/build_diagnosis.py`,
    `scripts/dashboard/generate_multi_project_dashboard.py`

## Hard Rules (skill-level)

- **Never run two configs with the same `DESIGN_NAME` and `FLOW_VARIANT` concurrently.**
  `run_orfs.sh` derives `FLOW_VARIANT` from the project dir basename — keep project names
  unique within a DESIGN_NAME.
- **Never set `PLACE_DENSITY_LB_ADDON` below 0.10.** Placer divergence is irrecoverable.
- **When one config in a design family crashes, apply the workaround to ALL configs of
  that family** before retrying — see `references/failure-patterns.md`.
- **For >100K-cell designs, never run multiple LVS jobs concurrently.** Each KLayout LVS
  process uses 3-5GB RAM; 2-3 in parallel cause 2-3× wall-time inflation.

## Where to Find X

| Question | File |
|----------|------|
| How does the skill run a flow? | `r2g-rtl2gds/SKILL.md` |
| Phase-by-phase workflow | `r2g-rtl2gds/references/workflow.md` |
| ORFS backend setup, env knobs, macro designs | `r2g-rtl2gds/references/orfs-playbook.md` |
| A specific failure / pitfall (DRC stuck, place_gp hang, CDL override, …) | `r2g-rtl2gds/references/failure-patterns.md` |
| Historical debug narratives + corpus results | `r2g-rtl2gds/references/lessons-learned.md` |
| How to read PPA / signoff JSON | `r2g-rtl2gds/references/ppa-report-guide.md` |
| Dataset label extraction (Y: congestion/wirelength/timing/irdrop) | `r2g-rtl2gds/references/label-extraction.md` |
| Dataset feature extraction (X: graph nodes/edges/metadata) | `r2g-rtl2gds/references/feature-extraction.md` |
| Per-platform tech handling (voltage, tap cells, cell-type ids, routing layers, liberty) | `r2g-rtl2gds/scripts/extract/techlib/` |
| Spec / config / SDC templates | `r2g-rtl2gds/references/spec-template.md`, `r2g-rtl2gds/assets/` |
| Validated config tuning per design family | `r2g-rtl2gds/references/lessons-learned.md` (corpus tables) |
| Platform extras (nangate45 LVS rule, etc.) | `r2g-rtl2gds/assets/platforms/<plat>/` |
| DRC/LVS violation fixing (antenna diode insertion, route/density, LVS triage) | `r2g-rtl2gds/references/signoff-fixing.md` |

## When You Fix a Bug

Skill scripts and references are the source of truth — not this file. The workflow is:

1. **Find the existing bucket** in `r2g-rtl2gds/references/failure-patterns.md` (one
   section per failure mode) or `lessons-learned.md` (historical debug narratives + corpus
   results). If your bug is a new sub-variant of an existing mode, append a section under
   it. Don't open a new top-level heading unless it's a genuinely new failure class.
2. **Update the offending script** in `r2g-rtl2gds/scripts/` to detect and either
   self-heal or emit a clear HINT message. Reference the failure-pattern file from the
   script comments.
3. **Re-validate** on the design that triggered the bug, ingest into the knowledge store
   (`knowledge/ingest_run.py`), and run `learn_heuristics.py` if a new family-level rule
   is implied.
4. **Commit with a clear "feat(skill):" or "fix(skill):" prefix.** The commit log is the
   long-term record.

This file (`CLAUDE.md`) only changes when the repo *layout* changes — not when a bug is
fixed inside the skill.

## Project Conventions

- Prefer editing existing scripts over creating new ones.
- All flow steps are in `r2g-rtl2gds/scripts/` — use them instead of ad-hoc shell
  commands. Inline shell is fine for quick exploration but should not replace a
  documented script in production runs.
- The skill supports single-clock flows including macro designs (`fakeram45`). Escalate
  to the user before attempting CDC, multi-clock, DFT, or signoff-quality closure.
- Dashboard is static HTML at `design_cases/_dashboard/index.html`, served via
  `scripts/dashboard/serve_multi_project_dashboard.py 8765`.
- Knowledge store (`r2g-rtl2gds/knowledge/`): the learn→suggest loop is **live** (per-family
  medians via the shared signoff-positive `knowledge_db.is_success`, applied by `suggest_config`
  under hard safety clamps). A read-only observability projection
  (`scripts/reports/build_lineage_view.py` → dashboard health + provenance panels) and a payoff
  A/B harness (`knowledge/eval_heuristics.py`) round it out. Config lineage is a loose
  single-parent diff chain, not a true version DAG. See `knowledge/README.md`.
- Batch results live under `design_cases/_batch/`; per-design logs under
  `design_cases/_batch/logs_*/`.
