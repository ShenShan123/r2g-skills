# Agent-with-OpenROAD — Project Guide

AI-driven open-source EDA flow: natural-language spec → GDSII via OpenROAD-flow-scripts
(ORFS), with full signoff (DRC, LVS, RCX). Implemented as the `r2g-rtl2gds` Claude Code
skill. The skill runs an as-human-engineering **closed learning loop** that records every run
into two memory databases (`knowledge.sqlite` = what resulted, `journal.sqlite` = what was
done), learns repair recipes from them, and applies the learning on the next similar issue —
so it keeps self-evolving. See "The Closed Learning Loop & Memory Databases" below.

This file orients you to the repo. The skill itself documents *how* to run the flow,
debug failures, and tune for known design families. **Do not duplicate skill content
here.** When you fix a bug, update `r2g-rtl2gds/` (the skill) — not this file.

## Project Layout

```
r2g-rtl2gds/                  # The skill (everything to run a flow lives here)
  SKILL.md                      # Workflow, hard rules, env knobs (PLACE_FAST, ROUTE_FAST, …)
  scripts/flow/                 # Stage runners: run_orfs.sh, run_drc.sh, run_lvs.sh, run_rcx.sh, …
    orfs_hooks/                   # ORFS stage-hook Tcl (e.g. buffer_port_feedthroughs.tcl via POST_GLOBAL_PLACE_TCL)
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

## Skill Deployment (must be a symlink, not a copy)

Claude Code loads the skill from `.claude/skills/r2g-rtl2gds/` (gitignored), **not** from
the canonical `r2g-rtl2gds/` source tree. Deploy it with `bash r2g-rtl2gds/install.sh --project . --link` so the deployed path is a **symlink** to the canonical tree. A plain
`cp` install silently goes stale — the harness then loads an old `SKILL.md` (wrong env
paths, missing failure buckets) while the canonical skill keeps evolving. If a session's
loaded skill disagrees with `r2g-rtl2gds/SKILL.md`, re-run the installer with `--link --force`. (Root-cause of the 2026-06-08 stale-skill defect.)

## Toolchain (autodetected by the skill)

`scripts/flow/_env.sh` autodetects ORFS + tool paths. You don't need to source anything
manually. Override any value via `$R2G_ENV_FILE`, `references/env.local.sh`, or by
exporting `ORFS_ROOT`, `OPENROAD_EXE`, `YOSYS_EXE`, `KLAYOUT_CMD`, etc. before invoking
a script.

| Required                                        | Optional                                                                  |
| ----------------------------------------------- | ------------------------------------------------------------------------- |
| python3 (3.10+), yosys, openroad, ORFS checkout | iverilog/vvp, verilator, klayout, magic, netgen-lvs, opensta, sky130A PDK |

Verify with `bash r2g-rtl2gds/scripts/flow/check_env.sh`.

**Installed signoff toolchain (2026-06-10, this machine).** `iverilog`/`vvp`, `magic`, and
`netgen` are installed in a user-level Miniconda env (`~/miniconda3/envs/eda`, litex-hub
channel — no sudo). The sky130A PDK (`open_pdks.sky130a`, ~8GB) is staged at
`/proj/workarea/user5/sky130_pdk/share/pdk/sky130A` — on the 50T `/proj` volume because
`/home` (100G) is full. All four are pinned for the skill in
`r2g-rtl2gds/references/env.local.sh` (`IVERILOG_EXE`/`VVP_EXE`/`MAGIC_EXE`/`NETGEN_EXE` +
`PDK_ROOT`); `check_env.sh` shows them green. This unblocks real sky130 Magic DRC + Netgen
LVS (the prior tooling gap that paused the sky130 campaign). Install recipe + gotchas
(conda `--override-channels` ToS, volare/SOCKS) are in `README.md`. **Do not install large
packages into `$HOME`** — it is full; use `/proj`. Validated at scale 2026-06-11: the first
50-design sky130hd wave closed at **50/50 signoff-clean** (Magic-extracted Netgen LVS
"Circuits match uniquely") after two skill-level LVS fixes — antenna-diode device
normalization and port-feedthrough buffering (see `r2g-rtl2gds/references/failure-patterns.md`,
"sky130 LVS" cause 5). Extended 2026-06-12 (waves 2–4, +86 designs → **136/136
signoff-clean**): one new skill-level fix — `tools/mk_sky130_project.py` is now IO-pad-aware,
reading the source DEF `PINS` count and enlarging the PDN-floor die only when pads exceed its
~718 capacity (PPL-0024 on pin-huge/cell-tiny designs like `verilog_ethernet_ip_demux`, 1523
pads → 560 µm die; a no-op for ≤718-pad designs, so the 134 other waves are byte-identical).
`extract_ppa.py` now emits `orfs_fail_stage` (the campaign driver already consumed it). See
`references/failure-patterns.md` "sky130 high-pin-count floorplan (PPL-0024)". Extended again
2026-06-13 (wave 5, +40 diverse → **176/450 done, 174 clean / 2 genuine route-congestion**):
every wave-5 failure was ONE bug — `mk_sky130_project.py` read top-level `ppa["cell_count"]`
(always null) instead of `geometry.instance_count`, so the die was *always* the 200 µm PDN
floor; cell-dense designs (~3100-3600 cells) over-packed it to ~100 % util and aborted at place
(DPL-0036) or route (GRT-0116/timeout). Fixed the read (+ `source_def_components()` DEF fallback);
small designs stay below the floor threshold (byte-identical), large ones now size by
`CORE_UTILIZATION` and close. Three more skill fixes in the same session, all with the knowledge
store's honesty as the throughline: `extract_ppa.detect_orfs_progress` now reads `stage_log.jsonl`
(was mis-attributing the fail stage from disk-ODB probing); `repair_run_status.py` now maintains
`failure_events` in lock-step with reconciled status **and** only touches the latest-ingested row
per project (a multi-run-clobber bug it briefly caused, then fixed — restored from its own `.bak`);
`knowledge_db.connect` arms `busy_timeout` (parity with `journal_db`) so pooled campaign ingests
can't silently drop a run. The 2 residuals (AES `aes_encipher_block`, DES `des_area`) are genuine
route congestion — crypto SPN logic on sky130hd's 5 routing layers; lowering `CORE_UTILIZATION`
≤ 8 is the lever but some may not close. See `references/failure-patterns.md` "sky130hd large-core
over-packs the PDN floor (DPL-0036)" and "sky130hd route-dense designs (crypto SPN)". 274 candidates
pending for future waves.

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

## The Closed Learning Loop & Memory Databases

The skill *learns from every run*. Two SQLite databases under `r2g-rtl2gds/knowledge/` are
its memory; they are committed to the repo so the learning compounds across sessions and
machines. **Detail (schema, CLI, invariants list) lives in `knowledge/README.md` — this is
the orientation.**

### The two databases (distinct roles — do not conflate)

- **`knowledge.sqlite` — what *resulted*.** One `runs` row per flow (clean, failed, or
  partial). Derived projections: `failure_events` (one per backend abort / diagnosis issue,
  signature-keyed e.g. `orfs-fail-place-DPL-0024`), `run_violations` (the full DRC/LVS/timing
  landscape of *every* run), `fix_events` + `fix_trajectories` (every fix attempt — including
  *abandoned* episodes and *failed* strategies, i.e. negative learning), `symptoms` + `lessons`
  (repair experience keyed by a **symptom signature**, not a family name, so a fix learned on
  nangate45 transfers to sky130hd), and `config_lineage` (per-design config diff chain).
  `learn_heuristics.py` + `mine_rules.py` roll these into `heuristics.json` (Tier-3 recipes).
- **`journal.sqlite` — what was *done*, and why.** `actions` (every `loop|agent|operator`
  action: `config_knob_delta`, `sdc_edit`, `stage_rerun`, `tool_invoke`, `escalate`,
  `ab_launch`, `promote`/`demote`, with payload + `parent_action_id` for stacked fixes),
  `log_summaries` (per-run tool-log digests), `tool_bugs` (normalized EDA tool-crash
  signatures, `symptom_id`-keyed so they join the knowledge store). It is the decision ledger;
  `ingest_run.py` back-fills each action/bug with its `run_id` at ingest time.

### One turn of the wheel

1. Run the flow → extraction scripts emit structured JSON (`reports/*.json`).
2. **Ingest** (`knowledge/ingest_run.py`, SKILL.md step 10) writes the `runs` row +
   `failure_events` + `run_violations` + `fix_events`, and stamps `run_id` onto the journal.
3. **Learn** (`learn_heuristics.py`, `mine_rules.py`) derives `fix_trajectories` → symptom-indexed
   `fix_recipes` in `heuristics.json`; genuinely new signatures land in `failure_candidates.json`
   (a human review queue — never auto-merged into `failure-patterns.md`).
4. **Apply** on the next similar issue: `suggest_config.py` (per-family medians, hard-clamped) and
   `diagnose_signoff_fix.py` (symptom-keyed, evidence-ranked strategy order + cross-platform prior).
5. **Campaign** (`scripts/loop/engineer_loop.py`) drives this unattended with A/B-gated recipe
   promotion (`shadow → candidate → promoted`) and escalation; see `references/engineer-loop.md`.

> **2026-06-16 (paper-absorption, branch `feat/paper-absorption`).** The A/B promotion machinery
> was correct but had **never fired in production**: it lived only inside `engineer_loop.run`
> (never the production driver), so `learn()` never enqueued candidates → `recipe_status` empty,
> `ab_trials=0` across 1267 runs (Tier −1 Gate A). Fixed: `learn()` now enqueues candidates on
> **every** rebuild and `engineer_loop ab-drain`/`ab-enqueue` drain the queue (0 → 7 candidates on
> the live corpus, honesty 69/69). Step 4 now also ranks on a dense, additive `runs.outcome_score`
> (stage-progress + VRR, Win 1) and can KNN-retrieve configs by a pre-route feature vector
> (`presynth.py`, Win 5) instead of family-name prefix. A held-out `is_bench` set (Win 3) is
> excluded from the learning read but NOT from the honesty count. New invariants 19–22 in
> `knowledge/README.md`; the A/B verdict is now a variance-aware LCB over k repeats (Win 2).

### Honesty invariants (violate one and the loop silently lies)

- **Ingest after EVERY flow** — clean, failed, or partial. A skipped ingest is invisible work;
  a failed run that is never ingested teaches nothing.
- **`failure_events` is a derived projection of `runs.orfs_status`/`orfs_fail_stage`.** *Every*
  writer that sets those columns must also maintain the event — the live ingest does, and
  `repair_run_status.py` must too. If `runs` shows failures but `failure_events` is empty, the
  learner is blind to the entire backend-failure class even though the corpus *looks* populated.
  (Root cause of two 2026-06-12/13 defects.)
- **Concurrent campaign ingests share one file.** `knowledge_db.connect` / `journal_db.connect`
  arm a `busy_timeout` so a lock waits instead of erroring; a pool driver that swallows ingest
  errors would otherwise drop runs silently. Never trust a swallowed ingest — confirm the row landed.
- **A design can have many runs** (re-run after a fix). Reconciliation/repair tools touch only the
  **latest-ingested row per project**; older rows are immutable history — an old `fail` and a new
  `pass` must coexist. Re-deriving every row from the latest stage log clobbers that history.
- **`heuristics.json` is advisory and safety-clamped**; the lineage / observability panels
  (`build_lineage_view.py`) are READ-ONLY projections, never auto-tuners.

A fast end-to-end honesty check: the count of `runs` with `orfs_status='fail'` should equal the
count of those carrying an `orfs-fail-%` `failure_event`; the dashboard's **Knowledge Store Health**
panel renders red when `heuristics.json` is empty (learning is inert) — that red is the alarm.

## Where to Find X

| Question                                                                                | File                                                                 |
| --------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| How does the skill run a flow?                                                          | `r2g-rtl2gds/SKILL.md`                                             |
| Closed learning loop, DB schema, store invariants                                       | `r2g-rtl2gds/knowledge/README.md`                                 |
| Autonomous campaign + escalation drain + provenance                                     | `r2g-rtl2gds/references/engineer-loop.md`                          |
| Fix-learning loop (record → learn → apply, symptom index)                               | `r2g-rtl2gds/references/signoff-fixing.md`                         |
| Phase-by-phase workflow                                                                 | `r2g-rtl2gds/references/workflow.md`                               |
| ORFS backend setup, env knobs, macro designs                                            | `r2g-rtl2gds/references/orfs-playbook.md`                          |
| A specific failure / pitfall (DRC stuck, place_gp hang, CDL override, …)               | `r2g-rtl2gds/references/failure-patterns.md`                       |
| Historical debug narratives + corpus results                                            | `r2g-rtl2gds/references/lessons-learned.md`                        |
| How to read PPA / signoff JSON                                                          | `r2g-rtl2gds/references/ppa-report-guide.md`                       |
| Dataset label extraction (Y: congestion/wirelength/timing/irdrop)                       | `r2g-rtl2gds/references/label-extraction.md`                       |
| Dataset feature extraction (X: graph nodes/edges/metadata)                              | `r2g-rtl2gds/references/feature-extraction.md`                     |
| Per-platform tech handling (voltage, tap cells, cell-type ids, routing layers, liberty) | `r2g-rtl2gds/scripts/extract/techlib/`                             |
| Spec / config / SDC templates                                                           | `r2g-rtl2gds/references/spec-template.md`, `r2g-rtl2gds/assets/` |
| Validated config tuning per design family                                               | `r2g-rtl2gds/references/lessons-learned.md` (corpus tables)        |
| Platform extras (nangate45 LVS rule, etc.)                                              | `r2g-rtl2gds/assets/platforms/<plat>/`                             |
| DRC/LVS violation fixing (antenna diode insertion, route/density, LVS triage)           | `r2g-rtl2gds/references/signoff-fixing.md`                         |

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
5. **Re-ingest and verify both databases reflect reality** (see "The Closed Learning Loop &
   Memory Databases"). After re-validating, confirm in `knowledge.sqlite`: the run ingested
   (`runs`), the failure recorded (`failure_events` mirrors `orfs_status` — every fail row has
   an `orfs-fail-%` event), the fix attempt captured (`fix_events`/`fix_trajectories`, including
   if it was abandoned), heuristics re-derived (`learn_heuristics.py`); and in `journal.sqlite`:
   the actions logged (`actions`) and any tool crash captured (`tool_bugs`). A mismatch (e.g. a
   `fail` run with no `failure_event`) is itself a bug in the loop — fix it, don't paper over it.
   The skill must keep evolving with each action on the issue-fixing trajectory.

## Project Conventions

- Prefer editing existing scripts over creating new ones.
- All flow steps are in `r2g-rtl2gds/scripts/` — use them instead of ad-hoc shell
  commands. Inline shell is fine for quick exploration but should not replace a
  documented script in production runs.
- The skill supports single-clock flows including macro designs (`fakeram45`). Escalate
  to the user before attempting CDC, multi-clock, DFT, or signoff-quality closure.
- Dashboard is static HTML at `design_cases/_dashboard/index.html`, served via
  `scripts/dashboard/serve_multi_project_dashboard.py 8765`.
- Knowledge store (`r2g-rtl2gds/knowledge/`): the learn→suggest loop is **live** — see "The
  Closed Learning Loop & Memory Databases" for the model and the honesty invariants. Implementation
  notes: per-family medians via the shared signoff-positive `knowledge_db.is_success`, applied by
  `suggest_config` under hard safety clamps; a read-only observability projection
  (`scripts/reports/build_lineage_view.py` → dashboard health + provenance panels); a payoff A/B
  harness (`knowledge/eval_heuristics.py`); config lineage is a loose single-parent diff chain, not
  a true version DAG. Full schema + CLI in `knowledge/README.md`.
- Batch results live under `design_cases/_batch/`; per-design logs under
  `design_cases/_batch/logs_*/`.
