---
name: def-graph
description: Convert clean, signed-off physical-design files (DEF/LEF/liberty/SPEF from an ORFS backend run) into training-ready PyTorch-Geometric graph datasets. Use when the user wants to build a graph dataset from placed-and-routed designs — the five graph views (b–f), the tech-lib/LEF/DEF parser, node/edge feature extraction, or per-cell/per-net labels (congestion, RC parasitics, wirelength, timing, IR drop). Companion to the signoff-loop skill, which produces the physical-design inputs this skill consumes.
metadata:
  requires:
    bins: [python3, openroad]
    optional_bins: [klayout]
    python:
      # The graph-assembly stage (run_graphs.sh) needs a venv with these; the
      # label/feature/techlib stages are pure-python and run without them.
      # Point R2G_GRAPH_PYTHON at that venv's bin/python. Install on /proj, never $HOME.
      graph_venv: [torch, torch_geometric, pandas]
    env:
      # Tool paths are autodetected by scripts/flow/_env.sh (shared resolver, same
      # contract as the signoff-loop skill). Override any single value in your shell,
      # in $R2G_ENV_FILE, or in references/env.local.sh.
      OPENROAD_EXE: "(autodetected) openroad binary — reads 6_final.odb for label extraction"
      ORFS_ROOT: "(autodetected) OpenROAD-flow-scripts checkout — resolves platform liberty/LEF"
      PDK_ROOT: "(autodetected) directory containing sky130A etc."
      R2G_GRAPH_PYTHON: "path to a python with torch + torch_geometric + pandas (graph stage only)"
      R2G_GRAPH_VARIANTS: "which of b..f to build (default bcdef)"
  warnings:
    - Operates on ALREADY signed-off backend outputs (6_final.def/.odb/.spef) — it does NOT run PnR.
    - The label/feature stages are fail-soft — a missing input degrades one column, never aborts.
    - The graph-assembly stage SKIPs cleanly (with an install HINT) when the torch venv is absent.
    - Platform-agnostic: liberty/LEF/supply-voltage are resolved from the ORFS platform config.
---
# def-graph Skill

Turn a completed, signed-off backend run into a **training-ready graph dataset**. This is the
X/Y dataset-construction half of the RTL→GDS→Graph pipeline: it reads the physical-design
artifacts a signoff flow produces (`6_final.def`, `6_final.odb`, optional `6_final.spef`, plus
the platform liberty/LEF) and emits per-node/per-edge **features (X)**, per-cell/per-net
**labels (Y)**, and five PyTorch-Geometric **graph topologies (b–f)**.

Produce the inputs with the **signoff-loop** skill (or any ORFS run that leaves a
`6_final.def`); this skill consumes them. It never runs place-and-route.

## Environment Setup

Every stage sources `scripts/flow/_env.sh` on entry, which autodetects ORFS + tool paths
(shared resolver, identical contract to the signoff-loop skill). Nothing to source manually.
Resolution order (first hit wins, per value): caller env → `$R2G_ENV_FILE` → in-skill
`references/env.local.sh` → `$ORFS_ROOT/env.sh` → `/opt/openroad_tools_env.sh` → autodetect.

The graph-assembly stage additionally needs a **torch venv** (torch + torch_geometric +
pandas). Point `R2G_GRAPH_PYTHON` at its `bin/python`; the label/feature/techlib stages do not
need it. Install on `/proj`, never `$HOME`:

```bash
python3 -m venv /proj/<you>/pyenvs/r2g-graph
/proj/<you>/pyenvs/r2g-graph/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
/proj/<you>/pyenvs/r2g-graph/bin/pip install torch_geometric pandas
```

## Workflow

The three stages compose. Each takes a `<project-dir>` that already holds a signed-off backend
run (a `6_final.def` reachable via ORFS results or `$R2G_DEF`) and `[platform]`.

### 1. Labels (Y) — `scripts/flow/run_labels.sh <project-dir> [platform]`

Per-cell / per-net regression targets into `<project-dir>/labels/`, plus a per-design
`reports/labels_stats.json`:

- **congestion** (per-gate; dense placement utilization → pure-python gaussian smoothing →
  orientation-aware bbox mapping → 2-vector label),
- **wirelength** (per-net; routed-segment length, log1p µm),
- **timing** (per-cell path delay, via OpenROAD),
- **IR drop** (per-gate, via OpenROAD),
- **RC parasitics** (from `6_final.spef`, via `extract_rc.py`): ground cap
  (`net_ground_cap.csv`, per-net node label), coupling cap (`coupling_cap.csv`, net-pair edge
  label), equivalent resistance (`equiv_res.csv`, same-net pin-pair edge label), + `net_driver.csv`
  (places net↔net coupling edges on driver pins). No SPEF (RCX not run) → header-only RC CSVs.

Fail-soft: a missing input or per-label tool error is recorded in the stats file, not fatal.
Platform-agnostic — liberty/LEF/supply-voltage come from the ORFS platform config. Batch
backfill across completed designs: `tools/run_labels_batch.sh`. See
`references/label-extraction.md`.

### 2. Features (X) — `scripts/flow/run_features.sh <project-dir> [platform]`

Graph-feature CSVs into `<project-dir>/features/`: `metadata.csv` (graph-level),
`nodes_{gate,net,iopin,pin}.csv`, `edges_{gate_pin,pin_net,iopin_net}.csv`, plus a per-design
`reports/features_stats.json`. Reads the **same** `6_final.def` (+ optional `6_final.spef`) as
the labels, so feature rows join label rows on `graph_id`+`inst_name`/`net_name`. Fail-soft;
SPEF absence degrades cap/RC columns to 0. Batch backfill: `tools/run_features_batch.sh`. See
`references/feature-extraction.md`.

### 3. Graph datasets (b–f) — `scripts/flow/run_graphs.sh <project-dir> [platform]`

Assembles the five topologies into `<project-dir>/dataset/{b..f}_graph.pt`, plus the
synthesis-netlist bipartite graph (`netlist_graph.pt`) and a manifest
(`dataset/graph_manifest.json`, mirrored to `reports/graph_dataset.json`). Runs stages 1–2
automatically when their CSVs are missing or older than the DEF (freshness judged by the
`reports/{features,labels}_stats.json` stage-completion markers, not an early CSV).

```bash
R2G_GRAPH_PYTHON=/proj/<you>/pyenvs/r2g-graph/bin/python \
  scripts/flow/run_graphs.sh <project-dir> [platform]
```

Needs torch + torch_geometric + pandas; machines without them SKIP cleanly with a HINT.
`R2G_GRAPH_VARIANTS` selects variants (default `bcdef`); `GRAPH_TIMEOUT` (default 2400s);
`R2G_DEF` pins a specific DEF. See `references/graph-dataset.md`.

**Check `status` + `label_health` in the manifest before training.** `status:
"ok_with_label_gaps"` means ≥1 label file couldn't join (its y slot is all-NaN); the per-file
reason is in `label_health`.

## The five topologies (b–f)

| Variant | Nodes kept | Folded into edges |
|---------|-----------|-------------------|
| **b** | gate, net, iopin, pin | — (gate-pin, pin-net, iopin-net edges) |
| **c** | gate, net, iopin | pins → gate-net edges (pin features on `edge_attr`) |
| **d** | gate, iopin, pin | nets → pin-clique edges (net features on `edge_attr`) |
| **e** | iopin, pin | gates AND nets → pin-clique edges |
| **f** | gate, iopin | nets → gate-clique edges |

Shared tensor schema: `x[N,10]` (node_type, graph_id, 8 per-type feature slots), `y[N,6]`
(node_type, congestion, IR drop, timing, wirelength, **RC ground cap**; NaN where a label doesn't
apply). Folded entities carry their features/labels on `edge_attr[E,8]` / `edge_y[E,5]`,
interleaved `[fwd0,rev0,fwd1,rev1,...]`. RC **coupling-cap + resistance** labels ride a *separate
parasitic edge set* (`rc_edge_index` / `rc_edge_type` / `rc_edge_y[E,3]`), distinct from the
physical-topology edges (present-but-empty where RC doesn't apply, so the schema is uniform). Full
schema + per-variant node/edge counts: `references/graph-dataset.md`.

## The tech-lib / LEF / DEF parser (`scripts/extract/techlib/`)

The shared per-platform tech layer every stage consumes:

- `profile.py` — supply voltage, tap patterns, cell-type strategy per ORFS platform.
- `resolve.py` — Python backend for `scripts/flow/resolve_platform_paths.sh` (same
  `KEY=VALUE` contract): resolves liberty/LEF/tech paths for a platform.
- `def_parse.py` — the single DEF/SDC parser (instances, nets, pins, routed segments).
- `lef.py` — routing-layer names, pitch/direction, the layer regex matcher.
- `liberty.py` — cell / pin / net classifiers (direction, clock, capacitance).
- `cell_types.py` — the `cell_type_id` map (curated for nangate45, runtime-built for all
  other platforms; MACRO gets a dedicated id).

## Resource Map

| Read this | When |
|-----------|------|
| `references/graph-dataset.md` | Building/reading the PyG b–f graphs, tensor schema, RC-parasitic label views, torch venv, provenance + audit notes. |
| `references/label-extraction.md` | Building the Y side (per-cell/per-net labels + stats). |
| `references/feature-extraction.md` | Building the X side (per-node/per-edge/metadata CSVs + stats). |
| `scripts/extract/techlib/` | Per-platform tech/LEF/liberty/DEF parsing internals. |
| `tools/verify_graph_dataset.py` (repo-level) | Independently verifying a built dataset against raw DEF/LEF/liberty/SPEF + OpenDB ground truth (`--batch` over many designs). Three named check groups — `topology_checks` (all 5 views b–f), `feature_stat_checks` (column re-derivation + stats-gate honesty + vocab), `signoff_report_checks` (DRC/LVS gate, ppa geometry, timing↔SDC, C_total/equiv_res vs SPEF; opt-in `--signoff-recheck` re-runs PDNSim for the IR-drop label). Run it after any corpus regeneration; see `references/graph-dataset.md` "Comprehensive verification". |

## Project Layout (the dataset dirs this skill writes)

```text
design_cases/<design-name>/
├── labels/     # Y: congestion / wirelength / timing / irdrop CSVs (run_labels.sh)
├── features/   # X: metadata.csv, nodes_{gate,net,iopin,pin}.csv, edges_*.csv (run_features.sh)
├── dataset/    # PyG graphs: {b..f}_graph.pt, netlist_graph.pt, graph_manifest.json (run_graphs.sh)
└── reports/
    ├── labels_stats.json      # per-design label stage stats
    ├── features_stats.json    # per-design feature stage stats
    └── graph_dataset.json     # mirror of dataset/graph_manifest.json
```

## Hard Rules

- This skill consumes **signed-off** backend output; it does not run or fix PnR. Produce the
  `6_final.def`/`.odb`/`.spef` with the **signoff-loop** skill first.
- Never regenerate a corpus and declare it good without running `tools/verify_graph_dataset.py`
  against raw DEF/LEF/liberty ground truth — silent value defects (transposed congestion
  demand, all-NaN IR-drop, quoted-unit cap scaling) have shipped before and are invisible in
  the manifest's row counts. See `references/graph-dataset.md` audit notes and
  the signoff-loop `references/failure-patterns.md` "Dataset-Extraction Silent-Value Defects".
- The label/feature stages are fail-soft by design: always check
  `reports/{labels,features}_stats.json` and the manifest `label_health` for degraded columns
  rather than assuming a non-empty CSV means correct values.
