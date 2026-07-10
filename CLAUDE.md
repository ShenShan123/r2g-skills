# Agent-with-OpenROAD — Project Guide

AI-driven open-source EDA flow: natural-language spec → GDSII via OpenROAD-flow-scripts
(ORFS), with full signoff (DRC, LVS, RCX), then a **training-ready graph dataset** for GNN
predictors. Implemented as the `r2g-skills` Claude Code skill collection — **four sub-skills**
(`signoff-loop` + `def-graph` from the 2026-07-07 split, see
`docs/superpowers/plans/r2g-skills-split-2026-07-07.md`; `eda-install` added 2026-07-08, see
`docs/superpowers/plans/r2g-skills-bootstrap-2026-07-08.md`; `rtl-acquire` ingested 2026-07-09, see
`docs/superpowers/plans/rtl-acquire-ingestion-2026-07-09.md`):
- **`eda-install`** — detects the machine and installs + verifies the toolchain the others run
  (ORFS + openroad/yosys, iverilog, klayout, magic/netgen, sky130A PDK, torch venv). One command,
  `bootstrap.sh` (detect → plan → install → pin `env.local.sh` → verify); no-sudo conda path by default.
- **`rtl-acquire`** — the RTL corpus supplier, UPSTREAM of the others: discovers/screens/acquires RTL
  at corpus scale (local trees, repo manifests, keyword search) and expands it **synth-only** into
  pre-layout `netlist_graph.pt` graphs with dedup, quality scoring, and publish gating. Owns
  acquire + corpus publish; BORROWS env (`_env.sh`), synth (`run_orfs.sh`, `ORFS_STAGES=synth`),
  the graph format (def-graph `netlist_graph.py`), and failure learning (`knowledge.sqlite`,
  runs stamped `flow_scope='synth_only'`; frontend classes land as `synth-frontend-*` events).
- **`signoff-loop`** — drives the flow RTL→GDS with full signoff *and* the self-improvement loop
  (the two memory DBs + `engineer_loop`) that eliminates DRC/LVS violations and closes timing at Fmax.
- **`def-graph`** — converts the clean, signed-off physical design (the ORFS `6_final.odb`/`.def`/
  `.spef` + platform liberty/LEF) into PyTorch-Geometric graph datasets: five graph views (b–f), the
  shared tech-lib/LEF/DEF parser, and feature (X) / label (Y) extraction — labels are congestion,
  wirelength, per-path timing slack, IR drop, and SPEF-derived RC parasitics (the last a `y[N,6]` node
  label + a separate `rc_edge_*` parasitic edge set, merged 2026-07-07).

**Each skill has ONE heart; everything else is plumbing** — read the two ⭐ sections below:
1. **`signoff-loop` → The Closed Learning Loop** — the two memory DBs (`knowledge.sqlite` = what
   *resulted*, `journal.sqlite` = what was *done*) + `engineer_loop`, the autonomous driver that closes
   the wheel unattended (flow → fix → learn → A/B-promote) and learns repair recipes that transfer
   across designs/platforms.
2. **`def-graph` → The Dataset-Construction Pipeline** — three composable stages (labels → features →
   graphs) keyed to the *same* DEF so X and Y join, whose failure mode is a plausible CSV with silently
   *wrong values*.

This file is *orientation*; the skills document *how* to run/debug/tune. **Don't duplicate skill content
or per-run results here** — when you fix a bug, update the relevant sub-skill under `r2g-skills/`
(`signoff-loop/` for flow/signoff/learning, `def-graph/` for dataset construction), not this file. Prefer
editing existing `scripts/` over adding new ones; use the documented steps, not ad-hoc shell, in production.

## Project Layout

```
r2g-skills/                     # The skill collection — installs THREE Claude Code skills
  install.sh                      # Installs all three sub-skills (symlinks each into .claude/skills/)
  bootstrap.sh                    # Shim → eda-install/bootstrap.sh (documented one-command setup)
  eda-install/                  # SKILL 0 — detect + install + verify the EDA toolchain (no-sudo default)
    SKILL.md                      # detect → plan → install → pin env.local.sh → verify
    bootstrap.sh                  # The orchestrator (--dry-run plans; --yes installs)
    scripts/setup/                # detect_env.sh, write_env_local.sh (+ install_<tier>.sh)
    scripts/flow/                 # _env.sh (byte-identical copy), check_env.sh (comprehensive verifier)
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
    scripts/extract/techlib/      # Per-platform tech/LEF/liberty/DEF/SPEF parser (shared by both stages)
    scripts/extract/{labels,features,graph}/  # Y labels, X features, the five graph topologies (+ odb_to_def)
    references/  tests/           # graph-dataset/feature/label docs; def-graph pytest + corner-case suites
  rtl-acquire/                  # SKILL 3 — corpus-scale RTL acquisition → synth-only netlist graphs
    SKILL.md                      # acquire → expand → repair → validate → publish; scoped-reuse contract
    scripts/{acquire,execute,repair,validate,publish,report,hygiene,knowledge}/  # stage-grouped
    scripts/execute/expand_candidates.py  # per-candidate: run_orfs synth + netlist_graph.pt + ingest
    scripts/knowledge/project_frontend_diagnosis.py  # journal→knowledge projection + honesty --check
    scripts/skill_env.py  scripts/flow/_env.sh  # thin env delegate over the shared _env.sh
    references/  tests/           # policy JSONs, operation matrix, failure KB; pytest suite
tools/                          # Repo-level operator tooling + installers (incl. verify_graph_dataset.py)
design_cases/                   # All design runs + built datasets (gitignored); _batch/, _dashboard/
```

## Skill Deployment (must be a symlink, not a copy)

Claude Code loads each sub-skill from `.claude/skills/{eda-install,signoff-loop,def-graph,rtl-acquire}/`
(gitignored), **not** the canonical `r2g-skills/` tree. Deploy all four with
`bash r2g-skills/install.sh --project . --link` so each path is a **symlink**. A plain `cp` install
silently goes stale — the harness then loads an old `SKILL.md` while the canonical skill evolves. If
a session's loaded skill disagrees with `r2g-skills/<skill>/SKILL.md`, re-run with `--link --force`.
(Root cause of the 2026-06-08 stale-skill defect.)

## Toolchain (autodetected by the skill)

`<skill>/scripts/flow/_env.sh` autodetects ORFS + tool paths — nothing to source manually. All
**four** sub-skills ship a copy that is **byte-identical** (md5 `9fa599b7…`); keep them in sync when
editing any. Override via `$R2G_ENV_FILE`, `<skill>/references/env.local.sh`, or by exporting
`ORFS_ROOT`/`OPENROAD_EXE`/`YOSYS_EXE`/`KLAYOUT_CMD`/… **Required:** python3 (3.10+), yosys, openroad,
ORFS checkout. **Optional:** iverilog/vvp, verilator, klayout, magic, netgen-lvs, opensta, sky130A PDK;
a torch+torch_geometric+pandas venv for the `def-graph` PyG graph-assembly stage only (`R2G_GRAPH_PYTHON`;
`run_graphs.sh` SKIPs cleanly without it). Verify with `signoff-loop/scripts/flow/check_env.sh`.

**Provisioning is its own skill — `eda-install`** (`bash r2g-skills/bootstrap.sh` is a shim to
`eda-install/bootstrap.sh`): one command to *detect → plan → install → pin `env.local.sh` → verify*
the toolchain. `--dry-run` prints a per-tier plan and installs nothing; without root it auto-selects a
**no-sudo** path (pre-built conda `litex-hub` binaries + a torch venv on a big volume, never a full
`$HOME`). Detection (`eda-install/scripts/setup/detect_env.sh`) + the pin generator
(`eda-install/scripts/setup/write_env_local.sh`, which writes `env.local.sh` into signoff-loop AND
def-graph) are the honesty layer — a bootstrapped env is auto-found next session. Design + rationale:
`docs/superpowers/plans/r2g-skills-bootstrap-2026-07-08.md`.

**This machine:** signoff tools (iverilog/vvp, magic, netgen) live in
`/proj/workarea/user5/miniconda3/envs/eda` (relocated 2026-07-09 from a now-deleted `~/miniconda3` to
free a full `$HOME`; the conda root is on `/proj` too); the sky130A PDK is staged at
`/proj/workarea/user5/sky130_pdk/share/pdk/sky130A`; all pinned in `references/env.local.sh` and green
in `check_env.sh` (enables real sky130 Magic DRC + Netgen LVS).
The graph-stage torch venv is at `/proj/workarea/user5/pyenvs/rtl2graph` (torch 2.12.1+cpu, PyG 2.8.0,
pandas, pytest) — point `R2G_GRAPH_PYTHON` at its `bin/python`. Install recipe in `README.md`.
**Never install large packages into `$HOME` (full) — use `/proj`.** Platforms in this checkout:
`asap7` (default), `nangate45`, `sky130hd`, `sky130hs`, `gf180`, `ihp-sg13g2`. The nangate45 LVS rule is
bundled (`tools/install_nangate45_lvs.sh`).

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

## The Closed Learning Loop  ⭐ (signoff-loop: memory databases + engineer_loop)

The skill *learns from every run*. **Full detail — schema, CLI, numbered invariants — lives in
`signoff-loop/knowledge/README.md`; the autonomous driver in `references/engineer-loop.md`.** Orientation:

### Two memory databases (distinct roles + distinct git status — never conflate)

- **`knowledge.sqlite` — what *resulted*; the durable knowledge+experience, TRACKED** (the committed
  binary IS the shipped, pre-trained store; so is `heuristics.json`). One `runs` row per flow
  (clean/failed/partial) + derived projections: `failure_events` (one per backend abort/diagnosis,
  signature-keyed e.g. `orfs-fail-place-DPL-0024`), `run_violations` (the DRC/LVS/timing **+ backend-abort**
  landscape of *every* run), `fix_events`+`fix_trajectories` (every fix attempt incl. *abandoned/failed*
  ones — negative learning), `symptoms`+`lessons` (repair experience keyed by a **symptom signature**
  `{check,class,predicates}`, NOT a family name — so a fix learned on nangate45 transfers to sky130hd),
  `config_lineage`. `learn_heuristics.py`+`mine_rules.py` roll these into `heuristics.json` (Tier-3 recipes).
- **`journal.sqlite` — what was *done*, and why; ALL detailed status+actions, GITIGNORED** (high-volume,
  machine-local, rotatable): `actions` (every `loop|agent|operator` action — `config_knob_delta`,
  `sdc_edit`, `stage_rerun`, `ab_launch`, `promote`/`demote`, with `parent_action_id` for stacked fixes),
  `log_summaries`, `tool_bugs`. The decision ledger; `ingest_run.py` back-fills each row's `run_id`.

**The firewall — the journal never lies into the learner.** Every runtime/inference path and both learners
read ONLY `knowledge.sqlite`, never the journal, so a fresh clone behaves identically off committed
knowledge. The journal contributes *hypotheses* only: it is mined ONLY at ingest time (where it's local)
by a promoter that projects net-new evidence into knowledge **tables** — each mined pattern is a hypothesis
validated against a knowledge-side *outcome* (`runs.outcome_score`, `ab_trials.verdict`,
`fix_trajectories.outcome`); distilled content lands in tables, never directly in
`heuristics.json`/`failure_candidates.json` (both full-rewritten from knowledge each `learn()`/`mine()`).
To share experience across operators, `knowledge_sync.py` exports a git-friendly NDJSON bundle
(`knowledge/store/`, regenerable + **gitignored** — the tracked binary is the shipped store) and `merge`s
another operator's store additively under an honesty gate.

### `engineer_loop` — the autonomous driver (`scripts/loop/engineer_loop.py`)

Drives the whole wheel unattended: **pull design → flow → signoff → fix → ingest → learn → recipe-diff →
A/B arms → verdict → promote/demote.** Unknowns go to the `escalations` queue; the loop NEVER blocks on
them. Recipe lifecycle **`shadow → candidate → promoted`** (or `→ demoted`), gated by a variance-aware LCB
over *k* repeats. Buttons: `learn()` (every rebuild) enqueues new/changed recipes as `candidate` (Gate A);
`ab-drain` plans+runs+judges the A/B arms (arm A control vs arm B forced recipe); `ab-enqueue`
force-validates a grandfathered recipe; `--workers N` / `R2G_AB_WORKERS` runs arms concurrently (cap
`NUM_CORES`, see Hard Rules).

### One turn of the wheel

1. Flow → extraction scripts emit `reports/*.json`.
2. **Ingest** (`knowledge/ingest_run.py`) writes `runs` + `failure_events` + `run_violations` +
   `fix_events`; stamps `run_id` onto the journal.
3. **Learn** (`learn_heuristics.py`, `mine_rules.py`) → symptom-indexed recipes in `heuristics.json`;
   genuinely new signatures land in `failure_candidates.json` (human-review queue, never auto-merged into
   `failure-patterns.md`). `learn()` also enqueues A/B candidates (Gate A).
4. **Apply** on the next similar issue: `suggest_config.py` (per-family medians, hard-clamped) +
   `diagnose_signoff_fix.py` (symptom-keyed, evidence-ranked, cross-platform prior).
5. **Campaign**: `engineer_loop` runs this unattended with A/B-gated promotion + escalation.

> Dated per-wave campaign narratives are NOT kept here (the "no per-run results" rule) — see
> `references/lessons-learned.md` + `failure-patterns.md`. Their durable lessons are the invariants below.

### Honesty invariants (violate one and the loop silently lies)

- **Ingest after EVERY flow** — clean, failed, or partial. A failed run never ingested teaches nothing.
- **`failure_events` mirrors `runs.orfs_status`/`orfs_fail_stage`** — every writer of those columns (live
  ingest, `repair_run_status.py`) must maintain the event. A `fail` run with empty `failure_events` = the
  learner is blind to the whole backend-failure class.
- **The A/B machinery must be EXECUTED + VERIFIED, not just shipped** (Gate A). For every run that fails a
  stage or leaves signoff incomplete: confirm `learn()` enqueued a `candidate` in `recipe_status`; actually
  run `ab-drain` (or `ab-enqueue`); verify `ab_trials` gained a row and the recipe transitioned. **Empty
  `ab_trials` alongside `fail`/`partial` rows is the alarm** — the loop is inert and lying; treat it like an
  empty `heuristics.json`.
- **EXECUTED is not enough — the two arms must do DIFFERENT work** (2026-06-24 audit). Arms must not inherit
  a clean `reports/` (the copytree excludes `reports/`), a signoff `ab_arm` must always reach `_run_fix`, and
  the success-tie cost tiebreak must be variance-aware (`se==0` is MAXIMAL confidence). VERIFY a trial's
  `metrics_json` shows the arms diverging (not identical `is_success`+`outcome_score`+`fix_iters`);
  `_symptom_check` routes by strategy (place vs timing vs DRC/LVS/route); an `inconclusive` verdict NEVER
  demotes; `recipe_status` is a function of the FULL `ab_trials` corpus, not the last trial. Detail:
  `failure-patterns.md` ("Learning-Loop Closure Failures") + `docs/…/r2g-loop-closure-audit-2026-06-24.md`.
- **Concurrent ingests share one file** — `connect` arms a `busy_timeout` so a lock waits instead of
  erroring; confirm the row landed.
- **A design can have many runs.** Reconciliation/repair touches only the **latest-ingested row per
  project**; older rows are immutable history (an old `fail` and a new `pass` coexist). Ingest keys `run_id`
  on `project_path:ppa.json-mtime` — regenerate `ppa.json` before re-ingesting a fixed design.
- **`heuristics.json` is advisory + safety-clamped**; lineage/observability panels (`build_lineage_view.py`)
  are READ-ONLY projections, never auto-tuners.
- **A cross-operator `merge` is honesty-gated, never trusted** — ADDITIVE (dedups by portable `symptom_id`
  + per-table content keys; surrogate ids re-assigned, never a merge key), in ONE transaction ROLLED BACK
  if `honesty.run_all` fails post-merge or the bundle has dangling FKs (incl. the inverse-H3 gate: an
  `orfs-fail-%` event landing on a `partial` run via a run_id collision). Run the gates over the REAL
  committed store in CI: `python3 knowledge/honesty.py --db knowledge/knowledge.sqlite`. Detail:
  `knowledge/README.md` (invariants 26-27).

**Fast honesty check:** `count(runs where orfs_status='fail')` must equal the count carrying an
`orfs-fail-%` `failure_event`; once the corpus has `fail`/`partial` rows, `ab_trials` must be non-empty —
AND, once trials exist, `promoted` must eventually grow **per-platform** (an `ab_trials`-grows-but-
`promoted`-flat-for-a-whole-platform state is the 2026-06-24 arms-identical alarm — subtler than empty
`ab_trials`). The dashboard's **Knowledge Store Health** panel goes red when `heuristics.json` is empty —
that red is the alarm.

## The Dataset-Construction Pipeline  ⭐ (def-graph: ODB → PyG graph datasets)

The sibling skill turns a **signed-off** backend run into **training-ready PyG graph datasets** for GNN
predictors — autonomously, per design. It reads only physical-design artifacts (`6_final.odb`/`.def`,
optional `6_final.spef`, platform liberty/LEF; `odb_to_def.py` bridges ODB→DEF where needed) and **never
runs or fixes PnR** — produce those inputs with `signoff-loop` first. **Full detail lives in
`def-graph/SKILL.md` + `references/{label,feature}-extraction.md` + `graph-dataset.md`.** Orientation:

### Three composable stages (all keyed to the SAME `6_final.def`)

1. **Labels (Y)** — `run_labels.sh` → `labels/*.csv` + `reports/labels_stats.json`. Per-cell/per-net
   regression targets: **congestion** (dense placement util → pure-python scipy-equivalent radius-4
   gaussian → orientation-aware bbox mapping → 2-vector `label`/`label_raw`), **wirelength** (routed
   centerline length, `log1p` µm), **timing** (per-cell path delay `clk_period − worst_slack` over ALL
   STA paths, via OpenROAD), **IR drop** (per-gate, PDNSim), **RC parasitics** (from SPEF: ground cap,
   coupling cap, equivalent resistance).
2. **Features (X)** — `run_features.sh` → `features/*.csv` + `reports/features_stats.json`. Per-node
   (`nodes_{gate,net,iopin,pin}`), per-edge (`edges_{gate_pin,pin_net,iopin_net}`), and graph-level
   (`metadata.csv`) tables.
3. **Graphs** — `run_graphs.sh` → `dataset/{b..f}_graph.pt` + `netlist_graph.pt` + `graph_manifest.json`.
   Joins X+Y into the five PyG topologies. The **only** stage needing the torch venv; SKIPs cleanly with a
   HINT when absent (so a missing venv looks like success — verify the manifest `status`). Auto-runs stages
   1–2 when their CSVs are stale — freshness judged by the `reports/{features,labels}_stats.json`
   stage-completion markers (written LAST), **not** an early CSV (the 2026-07-05 irdrop half-finish incident).

### The data contract (never break this)

- X and Y read the SAME `6_final.def`, so rows **join on `graph_id`(=`DESIGN_NAME`) + `inst_name`/`net_name`**.
  Overriding the DEF is via the namespaced `R2G_DEF` ONLY — the bare ORFS `DEF_FILE` is intentionally NOT
  honored (an operator export would silently pin every batch design to one DEF).
- Tensor schema (uniform across views): `x[N,10]` (node_type, graph_id, 8 per-type feature slots),
  `y[N,6]` (node_type, congestion, IR drop, timing, wirelength, **RC ground cap `y5`**; NaN where a label
  doesn't apply). Folded entities carry features/labels on `edge_attr[E,8]`/`edge_y[E,6]`, INTERLEAVED
  `[fwd0,rev0,fwd1,rev1,…]` so pairwise-repeated attr rows align (do not "simplify" back to
  `[all-fwd|all-rev]` — audit bug #5). `edge_y[:,5]` stays all-NaN (ground cap is never an edge label).
- **RC parasitics are LABELS (Y), never features.** Ground cap is the `y5` node label; **coupling cap +
  equivalent resistance ride a SEPARATE parasitic edge set** (`rc_edge_index`/`rc_edge_type`/`rc_edge_y[E,3]`,
  0=coupling net-pair, 1=resistance intra-net pin-pair), present-but-empty where RC doesn't apply so the
  schema stays uniform. RC is populated only when a SPEF exists (RCX ran) → else `rc_health="no_rc_labels"`.
- `cell_type_id` + every `*_type_id` column are **categorical and per-platform** (stable within a platform,
  NOT comparable across them) — filter datasets by `platform`.

### The five views (b–f) — progressive folding of the b-view bipartite graph

| View | Nodes kept | Folded into (clique) edges |
| ---- | ---------- | -------------------------- |
| **b** | gate, net, iopin, pin | — (gate-pin, pin-net, iopin-net edges) |
| **c** | gate, net, iopin | pins → gate-net edges (pin features on `edge_attr`) |
| **d** | gate, iopin, pin | nets → pin-clique edges (net features on `edge_attr`) |
| **e** | iopin, pin | gates AND nets → pin-clique edges |
| **f** | gate, iopin | nets → gate-clique edges |

Node layout is **block-positional** (a fixed type-block order per view, mergesort within each block); every
`y`-slice and name lookup assumes that exact order, so changing a sort key or block order silently misaligns
labels with no error. The **clock tree is deliberately not in the graph** — only signal nets (`net_type_id==0`)
survive; power/ground/clock/reset/scan nets, FILL/TAP cells, and gates with no signal pin are filtered. Plus
`netlist_graph.pt` — the pre-layout synthesis-netlist bipartite cell/net graph (from `1_2_yosys.v`), sharing
the feature stage's per-platform `cell_type_id` vocabulary so ids agree across a platform corpus.

### The shared techlib parser (`scripts/extract/techlib/`) — both stages consume it

`profile.py` (per-platform supply voltage / tap patterns / cell-type strategy), `resolve.py` (liberty/LEF/tech
path resolution — same `KEY=VALUE` contract as `resolve_platform_paths.sh`, byte-for-byte), `def_parse.py`
(the single DEF/SDC parser; COMPONENTS order == `nodes_gate` row order), `lef.py` (routing-layer
names/pitch/direction + the RECT-patch-aware `route_segments`), `liberty.py` (cell/pin/net classifiers,
quote-tolerant), `cell_types.py` (`cell_type_id` map — runtime-built from **standard-cell** liberty per
platform, `UNKNOWN=N`, dedicated `MACRO=N+1`; the curated nangate45 map was retired 2026-07-06 and its
import shim deleted 2026-07-09). `R2G_LIB_FILES` (full) and `R2G_SC_LIB_FILES` (std-cell-only = `LIB_FILES` minus `ADDITIONAL_LIBS`)
must both be exported and stay consistent across the feature and netlist-graph stages, or macros collapse to
`UNKNOWN` / `connects_macro_flag` sticks at 0. **Fix a parse bug ONCE here, never inline in a worker copy** —
congestion + wirelength share `route_segments`; metadata + `nodes_pin` + `extract_rc` share the SPEF unit
scaling — so a worker-local patch fixes one consumer and silently leaves the other wrong.

### Honesty / verification invariants (violate one and the dataset silently lies)

- **Fail-soft is by design, NOT a pass.** Each stage's workers are independent — a missing input degrades
  ONE column and records a per-item status; it never aborts the others. ALWAYS check
  `reports/{labels,features}_stats.json` + the manifest's `status`/`label_health`/`rc_health` before
  training. A non-empty CSV does NOT mean correct values (`status:"ok_with_label_gaps"` ⇒ ≥1 label file
  couldn't join and its `y` slot is all-NaN). The `compute_{label,feature}_stats.py` gates classify
  `skipped`/`invalid`/`ok` and are the honesty firewall — an all-NaN or raw-schema CSV must read `invalid`,
  never `ok`; never relax `REQUIRED_COLS` to make a raw dump pass.
- **NEVER declare a regenerated corpus good without `tools/verify_graph_dataset.py`** (run with
  `$R2G_GRAPH_PYTHON`; `--batch <root>` exits non-zero on any failure). It re-derives every structural +
  label expectation from the CSVs with separate pandas code (not `graph_lib`), independently re-parses the
  SPEF + raw liberty/LEF/DEF, and recomputes congestion with an independent radius-4 gaussian. **Silent-value
  defects are invisible in the manifest's row counts** and have shipped repeatedly. Its checks span three
  dimensions — **topology** (all five views b–f), **feature statistics** (column re-derivation + stats-gate
  honesty + vocab coverage), and **labels ↔ sign-off reports** (DRC/LVS gate, `ppa.json` geometry,
  timing↔SDC, RC/`C_total` vs SPEF; opt-in `--signoff-recheck` re-runs PDNSim for the IR-drop label);
  detail + the group functions are in `def-graph/references/graph-dataset.md` ("Comprehensive verification").
  Its blind spot — code paths the real designs never exercise — is covered by the synthetic corner-case
  suites (`tests/fixtures/corner_synth.py` + `test_corner_case_{pipeline,units}.py`) plus the group-level
  clean+negative controls in `test_verify_comprehensive.py` (every new check proven to FAIL on a deliberate
  corruption). (Fixture gotcha: a fixture liberty MUST be one-attribute-per-line — the parser uses anchored
  `re.match`, so a crammed pin drops direction/clock/cap and the test passes vacuously.)
- **Regenerate stale corpora after any extractor fix** — features AND labels AND graphs. The graph stage's
  staleness marker protects a single design, not a whole corpus you edited the code under (RC labels in
  particular need a forced label rebuild to backfill).

### Silent-value defect catalog (each shipped once; the guard that now catches it)

This skill's failure mode is a plausible-looking CSV with **wrong values**. Full table:
`signoff-loop/references/failure-patterns.md` "Dataset-Extraction Silent-Value Defects". Landmark cases:
- **Transposed congestion vertical demand** keyed `(y,x)` → ~79.7% of labels wrong on every platform;
  guard: demand always keyed `(x_gcell, y_gcell)` + the verifier's independent demand/gaussian recompute.
- **All-NaN IR drop under a manifest `"ok"`** (interrupted PDNSim RAW dump left at the canonical path);
  guard: raw→side-path + atomic tmp→rename, and `compute_label_stats` reports `invalid` for a raw/NaN CSV.
- **Quoted-unit liberty defects (sky130)**: cap `"pf"` unparsed (pin caps 1000× too small) + quoted
  `direction`/`clock` collapsing 95% of pin types; guard: quote-tolerant regexes across `techlib.liberty`.
- **SPEF↔DEF name-escaping join** dropping 79–92% of hierarchical-net / double-bus-register RC labels (and
  the analogous STA-name miss that zeroed every bus-named register's timing); guard: `_deesc` de-escapes all
  but `[` `]`, and STA joins on a backslash-stripped canonical name.
- **`connects_macro_flag` / `sum_pin_cap_fF` / `num_drivers`-`num_sinks` / `tracks_per_layer` semantics**:
  macro flag stuck 0 (SC-libs ⊇ ADDITIONAL_LIBS), pin-cap inflated ~20× by a driver's `max_capacitance`,
  port direction read from the instance instead of the chip, and a pipe-joined string coercing
  `global_feat[12]` to 0 — all corrected 2026-07; pre-fix CSVs are wrong.
- **RECT patch metal misread as route points** inflating wirelength ~100–400×; guard: `route_segments`
  strips RECT patch groups → centerline length.
- **nangate45 curated cell map drifted 22 masters** onto `UNKNOWN`; guard: retired for the runtime std-cell
  map + `MACRO` id. **Verifier oracle radius mismatch** (r1 vs the r4 kernel) false-failing every build;
  guard: `dense_gaussian_r4`.

## Where to Find X

| Question                                                            | File                                              |
| ------------------------------------------------------------------- | ------------------------------------------------- |
| How do I install/verify the EDA toolchain (detect → install → pin)? | `r2g-skills/eda-install/SKILL.md` + `references/setup.md` |
| How does the flow run RTL→GDS?                                      | `r2g-skills/signoff-loop/SKILL.md`                |
| How do I grow the RTL corpus / expand netlist graphs at scale?      | `r2g-skills/rtl-acquire/SKILL.md`                 |
| rtl-acquire task → script lookup, candidate CSV schema, failure KB  | `r2g-skills/rtl-acquire/references/{operation_matrix,candidate_csv_schema,failure_knowledge_base}.md` |
| Memory DBs: schema, CLI, full invariants list                       | `r2g-skills/signoff-loop/knowledge/README.md`     |
| `engineer_loop`: autonomous campaign + escalation + provenance      | `r2g-skills/signoff-loop/references/engineer-loop.md`  |
| Fix-learning loop (record → learn → apply, symptom index)           | `r2g-skills/signoff-loop/references/signoff-fixing.md` |
| Phase-by-phase workflow                                             | `r2g-skills/signoff-loop/references/workflow.md`  |
| ORFS backend setup, env knobs, macro designs                        | `r2g-skills/signoff-loop/references/orfs-playbook.md`  |
| Fmax search (loose-first fastest period; place-proxy + deterioration model) | `r2g-skills/signoff-loop/references/orfs-playbook.md` ("Fmax Search") + `SKILL.md` step 5a |
| A specific flow/signoff failure (DRC stuck, route congestion, CDL, …) | `r2g-skills/signoff-loop/references/failure-patterns.md`  |
| Historical debug narratives + corpus results                        | `r2g-skills/signoff-loop/references/lessons-learned.md`   |
| How to read PPA / signoff JSON                                      | `r2g-skills/signoff-loop/references/ppa-report-guide.md`  |
| How does def-graph build the dataset (labels → features → graphs)?  | `r2g-skills/def-graph/SKILL.md`                   |
| Dataset label/feature extraction (Y/X CSV columns, units, joins)    | `r2g-skills/def-graph/references/{label,feature}-extraction.md`  |
| PyG graph datasets (b–f views, tensor schema, RC edges, netlist graph, torch venv) | `r2g-skills/def-graph/references/graph-dataset.md`  |
| Per-platform tech handling (voltage, tap cells, layers, liberty, SPEF) | `r2g-skills/def-graph/scripts/extract/techlib/`   |
| Verify a built graph dataset vs raw DEF/LEF/liberty ground truth     | `tools/verify_graph_dataset.py` (`--batch`)       |
| Dataset silent-value defects (transposed congestion, all-NaN IR, cap units, SPEF join) | `r2g-skills/signoff-loop/references/failure-patterns.md` ("Dataset-Extraction Silent-Value Defects") |
| Spec / config / SDC templates                                       | `r2g-skills/signoff-loop/references/spec-template.md`, `r2g-skills/signoff-loop/assets/`  |
| DRC/LVS/route fixing (antenna diode, density/route relief, LVS)     | `r2g-skills/signoff-loop/references/signoff-fixing.md`  |

## When You Fix a Bug

Skill scripts + references are the source of truth — not this file. Steps 1–2 and 4 are common; step 3 and
the verification in step 5 differ by which skill you touched.

1. **Find the existing bucket** — `references/failure-patterns.md` (one section per failure mode) or
   `lessons-learned.md` for signoff-loop; the `def-graph` extractor/graph fixes bucket under
   failure-patterns.md "Dataset-Extraction Silent-Value Defects". Append a sub-section to an existing mode;
   only open a new top-level heading for a genuinely new failure class.
2. **Update the offending script** in `scripts/` to detect + self-heal or emit a clear HINT; reference the
   failure-pattern file from the script comments.
3. **Re-validate on the triggering design, then close the honesty loop for that skill:**
   - **signoff-loop** — **ingest** (`knowledge/ingest_run.py`) and re-run `learn_heuristics.py` if a new
     rule is implied; drive the A/B arms (`engineer_loop ab-drain`) for any fail/partial run.
   - **def-graph** — **regenerate the affected `labels/`/`features/`/`dataset/` artifacts** and run
     `tools/verify_graph_dataset.py --batch` against ground truth + the `def-graph/tests` suite (incl. the
     corner-case pipeline). There is no knowledge-DB ingest on this side.
4. **Commit** with a `feat(skill):`/`fix(skill):` prefix — the commit log is the long-term record.
5. **Verify the skill reflects reality per its ⭐ section's invariants:**
   - **signoff-loop** — run ingested; `failure_events` mirrors `orfs_status`; `fix_events`/`fix_trajectories`
     captured the attempt; heuristics re-derived; `actions`/`tool_bugs` journaled. A `fail` run with no
     `failure_event` is itself a loop bug — fix it, don't paper over it.
   - **def-graph** — `reports/{labels,features}_stats.json` classify `ok`/`invalid`/`skipped` correctly;
     the manifest `status`/`label_health`/`rc_health` reflect any degraded column; `verify_graph_dataset.py`
     is green. A degraded column reported as `ok` is itself a dataset bug — fix the gate, don't paper over it.

   The skill must keep evolving with each step on the issue trajectory.
