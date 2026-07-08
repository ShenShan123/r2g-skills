# Agent-with-OpenROAD — Project Guide

AI-driven open-source EDA flow: natural-language spec → GDSII via OpenROAD-flow-scripts
(ORFS), with full signoff (DRC, LVS, RCX). Implemented as the `r2g-skills` Claude Code skill
collection — **two sub-skills** (2026-07-07 split; see `docs/superpowers/plans/r2g-skills-split-2026-07-07.md`):
- **`signoff-loop`** — drives the flow RTL→GDS with full signoff *and* the self-improvement loop
  (the two memory DBs + `engineer_loop`) that eliminates DRC/LVS violations and closes timing at Fmax.
- **`def-graph`** — converts the clean, signed-off physical design (DEF/LEF/SPEF) into PyG graph
  datasets: five graph views (b–f), the tech-lib/LEF parser, and feature/label extraction.

**Two things make `signoff-loop` what it is** (everything else is plumbing):
1. **Two memory databases** (`knowledge.sqlite` = what *resulted*, `journal.sqlite` = what was
   *done*) record every run and learn repair recipes that transfer across designs/platforms.
2. **`engineer_loop`** — the autonomous driver that closes the loop unattended (flow → fix →
   learn → A/B-promote) and keeps the skill self-evolving.

See "The Closed Learning Loop" below — it is the heart of the repo. This file is *orientation*;
the skills document *how* to run/debug/tune. **Don't duplicate skill content or per-run results
here** — when you fix a bug, update the relevant sub-skill under `r2g-skills/` (`signoff-loop/` for
flow/signoff/learning, `def-graph/` for dataset construction), not this file. Prefer editing
existing `scripts/` over adding new ones; use the documented steps, not ad-hoc shell, in production.

## Project Layout

```
r2g-skills/                     # The skill collection — installs TWO Claude Code skills
  install.sh                      # Installs both sub-skills (symlinks each into .claude/skills/)
  signoff-loop/                 # SKILL 1 — RTL→GDS flow + signoff + the self-improvement loop
    SKILL.md                      # Workflow, hard rules, env knobs (PLACE_FAST, ROUTE_FAST, …)
    scripts/flow/                 # Stage runners: run_orfs.sh, run_drc/lvs/rcx.sh, fix_signoff.sh, _env.sh
      orfs_hooks/                   # ORFS stage-hook Tcl (POST_GLOBAL_PLACE_TCL, …)
    scripts/extract/              # Tool output → JSON: extract_ppa/drc/lvs/rcx/route + report_io, presynth
    scripts/project/              # init_project, normalize_spec, validate_config
    scripts/reports/              # check_timing, diagnose_signoff_fix, fmax_search, build_*
    scripts/loop/                 # engineer_loop.py — the autonomous campaign driver
    scripts/dashboard/            # render_gds_preview, generate/serve dashboard
    knowledge/                    # The two memory DBs + learn/ingest/A-B Python (self-contained)
    references/                   # Signoff detailed docs (see "Where to find X")
    assets/  tests/               # Templates + bundled platform rule decks; pytest suite
  def-graph/                    # SKILL 2 — graph dataset construction from signed-off DEF/LEF/SPEF
    SKILL.md                      # Labels → features → PyG graphs (b–f); torch-venv stage
    scripts/flow/                 # run_labels.sh, run_features.sh, run_graphs.sh, resolve_platform_paths.sh, _env.sh
    scripts/extract/techlib/      # Per-platform tech/LEF/liberty/DEF parser (shared by both stages)
    scripts/extract/{labels,features,graph}/  # Y labels, X features, the five graph topologies
    references/  tests/           # graph-dataset/feature/label docs; def-graph pytest suite
tools/                          # Repo-level operator tooling + installers
design_cases/                   # All design runs (gitignored); _batch/, _dashboard/
```

## Skill Deployment (must be a symlink, not a copy)

Claude Code loads each sub-skill from `.claude/skills/signoff-loop/` and `.claude/skills/def-graph/`
(gitignored), **not** the canonical `r2g-skills/` tree. Deploy both with
`bash r2g-skills/install.sh --project . --link` so each path is a **symlink**. A plain `cp` install
silently goes stale — the harness then loads an old `SKILL.md` while the canonical skill evolves. If
a session's loaded skill disagrees with `r2g-skills/<skill>/SKILL.md`, re-run with `--link --force`.
(Root cause of the 2026-06-08 stale-skill defect.)

## Toolchain (autodetected by the skill)

`<skill>/scripts/flow/_env.sh` autodetects ORFS + tool paths — nothing to source manually (each
sub-skill ships its own copy; they are byte-identical). Override via `$R2G_ENV_FILE`,
`<skill>/references/env.local.sh`, or by exporting `ORFS_ROOT`/`OPENROAD_EXE`/`YOSYS_EXE`/
`KLAYOUT_CMD`/… **Required:** python3 (3.10+), yosys, openroad, ORFS checkout. **Optional:**
iverilog/vvp, verilator, klayout, magic, netgen-lvs, opensta, sky130A PDK; a
torch+torch_geometric+pandas venv for the `def-graph` PyG graph-dataset stage only
(`R2G_GRAPH_PYTHON`; `run_graphs.sh` skips cleanly without it). Verify with
`signoff-loop/scripts/flow/check_env.sh`.

**This machine:** signoff tools (iverilog/vvp, magic, netgen) live in `~/miniconda3/envs/eda`; the
sky130A PDK is staged at `/proj/workarea/user5/sky130_pdk/share/pdk/sky130A`; all pinned in
`references/env.local.sh` and green in `check_env.sh` (enables real sky130 Magic DRC + Netgen LVS).
The graph-stage torch venv is at `/proj/workarea/user5/pyenvs/rtl2graph` (torch 2.12.1+cpu,
PyG 2.8.0, pandas, pytest) — point `R2G_GRAPH_PYTHON` at its `bin/python`.
Install recipe in `README.md`. **Never install large packages into `$HOME` (full) — use `/proj`.**
Platforms in this checkout: `asap7` (default), `nangate45`, `sky130hd`, `sky130hs`, `gf180`,
`ihp-sg13g2`. The nangate45 LVS rule is bundled (`tools/install_nangate45_lvs.sh`).

## Hard Rules (skill-level)

- **Never run two configs with the same `DESIGN_NAME` + `FLOW_VARIANT` concurrently.** `run_orfs.sh`
  derives `FLOW_VARIANT` from the project-dir basename — keep names unique within a `DESIGN_NAME`.
- **Never set `PLACE_DENSITY_LB_ADDON` below 0.10.** Placer divergence is irrecoverable.
- **For >100K-cell designs, never run multiple LVS jobs concurrently** (3-5GB RAM each → 2-3× wall time).
- **Parallel ORFS:** when running flows concurrently, cap per-flow threads with `NUM_CORES` so
  `flows × NUM_CORES ≈ cores` — the default grabs `nproc` (96) per flow, so N flows oversubscribe N×
  and thrash. `run_orfs.sh` wraps each stage in `setsid timeout`, so killing a driver orphans the
  make/openroad tree — `kill -9 -<pgid>` the process group, not just the python.
- **Escalate to the user before attempting CDC, multi-clock, DFT, or signoff-quality closure.**
  Single-clock flows incl. macro designs (`fakeram45`) are supported; the rest is out of scope.
- **Don't skip a failed stage** — diagnose first via `references/failure-patterns.md`. The strict
  flow order (spec → … → RCX → reports) lives in `SKILL.md`; **ingest after every flow** (clean,
  failed, or partial) so the learning loop sees it.

## The Closed Learning Loop  ⭐ (memory databases + engineer_loop)

The skill *learns from every run*. **Detail — schema, CLI, the full numbered invariants — lives in
`knowledge/README.md`; the autonomous driver in `references/engineer-loop.md`.** This is orientation.

### The two memory databases (distinct roles + distinct git status — never conflate)

**Git status is part of the contract:** `knowledge.sqlite` is **tracked/committed** (the durable
knowledge+experience the skill ships pre-trained with — the binary is the committed store); so is
`heuristics.json`. `journal.sqlite` is **gitignored** (high-volume, machine-local, rotatable detail).
To *share/transfer* that experience across operators, `knowledge_sync.py` is an **on-demand** tool:
`export` writes a git-friendly NDJSON bundle, `merge` unions another operator's store additively
under an honesty-gate, `status` runs the honesty gates. That bundle (`knowledge/store/`) is
regenerable and **gitignored** (NOT committed by default — the tracked binary is the shipped store).
(The 2026-06-23 bundle-as-source-of-truth migration was reverted per operator preference; the sync
tooling stays available for ad-hoc cross-user sharing/merge.)

**Corollary — how the journal feeds learning WITHOUT breaching the firewall (ingest-time promotion):**
the journal is mined ONLY at **ingest time** (on the operator machine, where the journal is local),
by a **promoter** that projects its net-new evidence into committed `knowledge.sqlite` *tables* — the
same way ingest already projects other gitignored local run artifacts (`design_cases/`, `reports/*.json`)
into committed knowledge. Three rails make this safe: (1) every mined pattern is a *hypothesis* validated
against a knowledge-side *outcome* (`runs.outcome_score`, `ab_trials.verdict`, `fix_trajectories.outcome`)
— the journal is lossy/best-effort, so it supplies candidates, never verdicts; (2) distilled content lands
in knowledge **tables**, never directly in `heuristics.json`/`failure_candidates.json` (both are
full-rewritten from knowledge each `learn()`/`mine()` and would clobber it); new strategies pass the
`failure_candidates.json` human-review gate. (3) the existing learners (`learn_heuristics.py`,
`mine_rules.py`) and **every runtime/inference path read ONLY `knowledge.sqlite`** — never the journal —
so a fresh clone behaves identically off committed knowledge. The journal contributes *hypotheses*;
knowledge contributes *evidence* and stays the sole source of truth and only honesty-gate source.

- **`knowledge.sqlite` — what *resulted*; the knowledge + experience (tracked — the committed binary store; `knowledge_sync.py` exports/merges it on demand for cross-user sharing).** One `runs` row per flow (clean/failed/partial). Derived
  projections: `failure_events` (one per backend abort/diagnosis issue, signature-keyed e.g.
  `orfs-fail-place-DPL-0024`), `run_violations` (the DRC/LVS/timing **and backend-abort** landscape
  of *every* run), `fix_events`+`fix_trajectories` (every fix attempt — including *abandoned* and
  *failed* ones: negative learning), `symptoms`+`lessons` (repair experience keyed by a **symptom
  signature** — `{check,class,predicates}`, NOT a family name — so a fix learned on nangate45
  transfers to sky130hd; backend aborts key under `check=orfs_stage`), `config_lineage`.
  `learn_heuristics.py`+`mine_rules.py` roll these into `heuristics.json` (Tier-3 recipes).
- **`journal.sqlite` — what was *done*, and why; ALL detailed status + actions (gitignored).** `actions` (every `loop|agent|operator` action:
  `config_knob_delta`, `sdc_edit`, `stage_rerun`, `ab_launch`, `promote`/`demote`, with payload +
  `parent_action_id` for stacked fixes), `log_summaries`, `tool_bugs` (normalized tool-crash
  signatures, `symptom_id`-keyed). The decision ledger; `ingest_run.py` back-fills each row's `run_id`.

### `engineer_loop` — the autonomous driver (`scripts/loop/engineer_loop.py`)

Drives the whole wheel unattended: **pull design → flow → signoff → fix → ingest → learn →
recipe-diff → A/B arms → verdict → promote/demote.** Unknowns go to the `escalations` queue; the
loop NEVER blocks on them. Recipe lifecycle is **`shadow → candidate → promoted`** (or `→ demoted`),
gated by a variance-aware LCB over *k* repeats. Production buttons:
- `learn()` (every rebuild) enqueues new/changed recipes as `candidate` in `recipe_status` (Gate A).
- `ab-drain` plans + runs + judges the A/B arms for pending candidates (arm A control vs arm B forced
  recipe); `ab-enqueue` force-validates a grandfathered recipe. `--workers N` / `R2G_AB_WORKERS`
  runs arm flows concurrently (cap `NUM_CORES`, see Hard Rules).

### One turn of the wheel

1. Flow → extraction scripts emit `reports/*.json`.
2. **Ingest** (`knowledge/ingest_run.py`) writes the `runs` row + `failure_events` + `run_violations`
   + `fix_events`, and stamps `run_id` onto the journal.
3. **Learn** (`learn_heuristics.py`, `mine_rules.py`) → symptom-indexed `fix_recipes` in
   `heuristics.json`; genuinely new signatures land in `failure_candidates.json` (human review queue —
   never auto-merged into `failure-patterns.md`). `learn()` also enqueues A/B candidates (Gate A).
4. **Apply** on the next similar issue: `suggest_config.py` (per-family medians, hard-clamped) +
   `diagnose_signoff_fix.py` (symptom-keyed, evidence-ranked, cross-platform prior).
5. **Campaign**: `engineer_loop` runs this unattended with A/B-gated promotion + escalation (above).

> Per-wave campaign results and the dated Gate-A/Gate-B/route-relief narratives are **not** kept here
> (this file's "no per-run results" rule) — they live in `references/lessons-learned.md`,
> `references/failure-patterns.md`, and the session memory. Their durable lessons are the invariants below.

### Honesty invariants (violate one and the loop silently lies)

- **Ingest after EVERY flow** — clean, failed, or partial. A failed run never ingested teaches nothing.
- **`failure_events` is a derived projection of `runs.orfs_status`/`orfs_fail_stage`** — *every* writer
  of those columns (live ingest, `repair_run_status.py`) must maintain the event. `runs` showing
  failures with an empty `failure_events` = the learner is blind to the whole backend-failure class.
- **The A/B machinery must be EXECUTED + VERIFIED, not just shipped** (the Gate A lesson). For every
  run that **fails a stage** or **leaves signoff incomplete**: (1) confirm `learn()` enqueued a
  `candidate` in `recipe_status`; (2) actually run `engineer_loop ab-drain` (or `ab-enqueue`) so the
  arms run and a verdict is recorded; (3) verify `ab_trials` gained a row and the recipe transitioned
  `candidate → promoted`/`→ shadow`. **An empty `ab_trials` alongside `fail`/`partial` rows is the
  alarm** (the loop is inert and lying) — treat it exactly like an empty `heuristics.json`. Never
  declare the loop "live" on the strength of the machinery existing.
- **EXECUTED is still not enough — the two A/B arms must do DIFFERENT work** (2026-06-24 loop-closure
  audit). If `plan_arms_for_candidates` copies the subject's CLEAN `reports/` into an arm dir,
  `process_one` reads that stale verdict and `_mark_clean`s the arm BEFORE the fixer runs, so arm A
  (`R2G_FIX_EXCLUDE`) and arm B (`R2G_FIX_RANK_FIRST`) are byte-identical and every verdict is
  wall-clock NOISE → **no nangate45 recipe ever promoted** for 8 waves while `ab_trials` kept growing.
  Invariants: the arm copytree MUST exclude `reports/`; a signoff `ab_arm` MUST always reach
  `_run_fix`; the success-tie cost tiebreak MUST be variance-aware (a flat ±2% flips on jitter, and
  `se==0` is MAXIMAL confidence, not none). VERIFY a trial's `metrics_json` shows the arms genuinely
  diverging — not identical `is_success`+`outcome_score`+`fix_iters`. (CLOSED 2026-06-24) The former
  KNOWN GAP — `_symptom_check` exercised only **DRC/LVS + route** while **timing/place** routed to inert
  `--check both` — is now fixed: `_symptom_check` routes by **strategy** (`core_util_relief`→`place`
  apply-then-flow arm; `period_relax`/`utilization_reduce`/`backend_aware_synth_retune`→`timing`, fixed
  via `fix_signoff --check timing`), `_arm_metric(timing=True)` judges on `wns_ns`/`timing_tier` (a
  timing miss never aborts the flow, so `is_success` ties both arms), and `_ab_coverage_gap` skips
  genuinely non-divergent candidates (`lvs_resolve_unknown`, or ≥3 inconclusive trials) WITHOUT
  demoting. Two judge-integrity fixes landed with it: an `inconclusive` verdict NEVER demotes (it was
  reverting candidates to terminal `shadow`), and `recipe_status` is now a function of the FULL
  `ab_trials` corpus (`ab_runner.judge_recipe`, net wins>losses) instead of the LAST trial. Detail:
  `references/failure-patterns.md` ("Learning-Loop Closure Failures") +
  `docs/superpowers/plans/r2g-loop-closure-audit-2026-06-24.md`.
- **Concurrent ingests share one file.** `knowledge_db`/`journal_db.connect` arm a `busy_timeout` so a
  lock waits instead of erroring — never trust a swallowed ingest; confirm the row landed.
- **A design can have many runs.** Reconciliation/repair tools touch only the **latest-ingested row
  per project**; older rows are immutable history — an old `fail` and a new `pass` must coexist
  (re-deriving every row from the latest stage log clobbers that history; note ingest keys `run_id` on
  `project_path:ppa.json-mtime`, so regenerate `ppa.json` before re-ingesting a fixed design).
- **`heuristics.json` is advisory + safety-clamped**; the lineage/observability panels
  (`build_lineage_view.py`) are READ-ONLY projections, never auto-tuners.
- **A cross-operator `merge` is honesty-gated, never trusted.** Sharing the store across users
  (`knowledge_sync.py`, the git-friendly NDJSON bundle in `knowledge/store/`) is the newest surface
  where the loop could silently lie — importing someone's `runs` without their `failure_events`
  would make *your* store fail H3 — incl. the *inverse* H3 a merge can introduce (an `orfs-fail-%`
  event landing on a `partial` run via a run_id collision; a 5th gate now catches it). So `merge` is
  ADDITIVE (dedups by the portable `symptom_id` + per-table content keys; surrogate ids are
  re-assigned, never a merge key) and runs in ONE transaction ROLLED BACK if `honesty.run_all` fails
  post-merge (or the bundle has dangling FKs) — a dishonest merge is refused, not applied. The
  honesty gates now live in importable `knowledge/honesty.py` (run them over the **real** committed
  store in CI: `python3 knowledge/honesty.py --db knowledge/knowledge.sqlite`). The NDJSON bundle is
  an on-demand export (`knowledge/store/`, gitignored); when you DO export one to share, its
  `knowledge_sync.py status` drift check confirms it matches the DB so a stale bundle can't transfer
  wrong experience. Detail: `knowledge/README.md` ("Sharing the store across users", invariants 26-27).

**Fast honesty check:** `count(runs where orfs_status='fail')` must equal the count carrying an
`orfs-fail-%` `failure_event`; once the corpus has `fail`/`partial` rows, `ab_trials` must be
non-empty — AND, once trials exist, `promoted` must eventually grow **per-platform** (an
`ab_trials`-grows-but-`promoted`-flat-for-a-whole-platform state is the 2026-06-24 "arms are
identical" alarm — subtler than empty `ab_trials`, and the exact symptom that hid the loop being
inert for nangate45). The dashboard's **Knowledge Store Health** panel renders red when
`heuristics.json` is empty — that red is the alarm.

## Where to Find X

| Question                                                            | File                                              |
| ------------------------------------------------------------------- | ------------------------------------------------- |
| How does the flow run RTL→GDS?                                      | `r2g-skills/signoff-loop/SKILL.md`                |
| Memory DBs: schema, CLI, full invariants list                       | `r2g-skills/signoff-loop/knowledge/README.md`     |
| `engineer_loop`: autonomous campaign + escalation + provenance      | `r2g-skills/signoff-loop/references/engineer-loop.md`  |
| Fix-learning loop (record → learn → apply, symptom index)           | `r2g-skills/signoff-loop/references/signoff-fixing.md` |
| Phase-by-phase workflow                                             | `r2g-skills/signoff-loop/references/workflow.md`  |
| ORFS backend setup, env knobs, macro designs                        | `r2g-skills/signoff-loop/references/orfs-playbook.md`  |
| Fmax search (loose-first fastest period; place-proxy + deterioration model) | `r2g-skills/signoff-loop/references/orfs-playbook.md` ("Fmax Search") + `SKILL.md` step 5a |
| A specific failure/pitfall (DRC stuck, route congestion, CDL, …)    | `r2g-skills/signoff-loop/references/failure-patterns.md`  |
| Historical debug narratives + corpus results                        | `r2g-skills/signoff-loop/references/lessons-learned.md`   |
| How to read PPA / signoff JSON                                      | `r2g-skills/signoff-loop/references/ppa-report-guide.md`  |
| How does def-graph build the dataset?                               | `r2g-skills/def-graph/SKILL.md`                   |
| Dataset label/feature extraction (Y/X)                              | `r2g-skills/def-graph/references/{label,feature}-extraction.md`  |
| PyG graph datasets (b–f variants, netlist graph, torch venv)        | `r2g-skills/def-graph/references/graph-dataset.md`  |
| Per-platform tech handling (voltage, tap cells, layers, liberty)    | `r2g-skills/def-graph/scripts/extract/techlib/`   |
| Spec / config / SDC templates                                       | `r2g-skills/signoff-loop/references/spec-template.md`, `r2g-skills/signoff-loop/assets/`  |
| DRC/LVS/route fixing (antenna diode, density/route relief, LVS)     | `r2g-skills/signoff-loop/references/signoff-fixing.md`  |

## When You Fix a Bug

Skill scripts + references are the source of truth — not this file.

1. **Find the existing bucket** in `references/failure-patterns.md` (one section per failure mode) or
   `lessons-learned.md`. Append a sub-section to an existing mode; only open a new top-level heading
   for a genuinely new failure class.
2. **Update the offending script** in `scripts/` to detect + self-heal or emit a clear HINT;
   reference the failure-pattern file from the script comments.
3. **Re-validate** on the triggering design, **ingest** (`knowledge/ingest_run.py`), and re-run
   `learn_heuristics.py` if a new rule is implied.
4. **Commit** with a `feat(skill):`/`fix(skill):` prefix — the commit log is the long-term record.
5. **Verify both DBs reflect reality** per the honesty invariants above (run ingested; `failure_events`
   mirrors `orfs_status`; `fix_events`/`fix_trajectories` captured the attempt; heuristics re-derived;
   `actions`/`tool_bugs` journaled). A `fail` run with no `failure_event` is itself a loop bug — fix
   it, don't paper over it. The skill must keep evolving with each step on the issue trajectory.
