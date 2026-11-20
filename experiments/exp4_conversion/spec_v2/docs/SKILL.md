---
name: def-graph
description: Convert clean, signed-off physical-design files (DEF/LEF/liberty/SPEF from an ORFS backend run) into training-ready PyTorch-Geometric graph datasets. Use when the user wants to build a graph dataset from placed-and-routed designs — the five graph views (b–f), the tech-lib/LEF/DEF parser, node/edge feature extraction, or per-cell/per-net labels (congestion, RC parasitics, wirelength, timing, IR drop). Companion to the signoff-loop skill, which produces the physical-design inputs this skill consumes.
metadata:
  requires:
    bins: [python3, openroad]
    optional_bins: [klayout]
    python:
      # The graph-assembly stage ([R2G code reference removed]) needs a venv with these; the
      # label/feature/techlib stages are pure-python and run without them.
      # Point R2G_GRAPH_PYTHON at that venv's bin/python. Install on /proj, never $HOME.
      graph_venv: [torch, torch_geometric, pandas]
    env:
      # Tool paths are autodetected by [R2G code reference removed] (shared resolver, same
      # contract as the signoff-loop skill). Override any single value in your shell,
      # in $R2G_ENV_FILE, or in [R2G code reference removed].
      OPENROAD_EXE: "(autodetected) openroad binary — reads 6_final.odb for label extraction"
      ORFS_ROOT: "(autodetected) OpenROAD-flow-scripts checkout — resolves platform liberty/LEF"
      PDK_ROOT: "(autodetected) directory containing sky130A etc."
      R2G_GRAPH_PYTHON: "path to a python with torch + torch_geometric + pandas (graph stage only)"
      R2G_GRAPH_VARIANTS: "which of b..f to build (default bcdef)"
      R2G2_MAX_PATHS: "OpenSTA endpoint-path cap for the four-stage timing labels (default 10000)"
  warnings:
    - Operates on ALREADY signed-off backend outputs (6_final.def/.odb/.spef) — it does NOT run PnR.
    - The label/feature stages are fail-soft — a missing input degrades one column, never aborts.
    - The graph-assembly stage SKIPs cleanly (with an install HINT) when the torch venv is absent.
    - Platform-agnostic: liberty/LEF/supply-voltage are resolved from the ORFS platform config.
    - Two dataset products: the b–f views ([R2G code reference removed]) and the four-stage causal graphs
      ([R2G code reference removed], vendored R2G2.0 — see [R2G code reference removed] before editing it).
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

Every stage sources `[R2G code reference removed]` on entry, which autodetects ORFS + tool paths
(shared resolver, identical contract to the signoff-loop skill). Nothing to source manually.
Resolution order (first hit wins, per value): caller env → `$R2G_ENV_FILE` → in-skill
`[R2G code reference removed]` → `[R2G code reference removed]` → `[R2G code reference removed]` → autodetect.

The graph-assembly stage additionally needs a **torch venv** (torch + torch_geometric +
pandas). Point `R2G_GRAPH_PYTHON` at its `bin/python`; the label/feature/techlib stages do not
need it. Install on `/proj`, never `$HOME`:

```bash
[R2G command block removed]
```

## Workflow

The three stages compose. Each takes a `<project-dir>` that already holds a signed-off backend
run (a `6_final.def` reachable via ORFS results or `$R2G_DEF`) and `[platform]`.

**Platform authority (failure-patterns.md #30):** *explicit `[platform]` arg > build provenance >
config.mk*. When the arg is omitted, each stage consults the discovered backend's `run-meta.json`
(via the shared `[R2G code reference removed]`) before trusting `constraints/config.mk` — a campaign
re-point (`[R2G code reference removed]`) rewrites config.mk for the whole corpus, and
`cell_type_id`/`*_type_id` vocabularies are per-platform, so keying an existing DEF to the re-pointed
platform is a silent-value defect. `[R2G code reference removed]` stamps the resolved platform into
`graph_manifest.json`; `[R2G code reference removed]` trusts manifest > `run-meta.json` > config.mk.

**Signoff gate (failure-patterns.md #34):** a `6_final.def` alone is NOT sign-off. Before
building, every stage runs the shared `[R2G code reference removed]` against
`reports/{drc,lvs,route}.json` + the DEF-run's `stage_log.jsonl`: required checks (fail-closed —
a MISSING report blocks) are drc ∈ {clean, clean_beol}, lvs ∈ {clean, skipped}, ORFS complete,
route/antenna residuals 0 when provable; timing is recorded but never blocks (negative slack is a
legitimate training label). `[R2G code reference removed]` — the dataset builder — **enforces** by default and
SKIPs a not-signed-off design; `[R2G code reference removed]`/`[R2G code reference removed]` default to **warn** (record +
proceed). Override with `R2G_SIGNOFF_GATE=strict|enforce|warn|off`; an explicit `R2G_DEF` override
downgrades to warn (a deliberate, recorded operator decision). The verdict always lands in
`reports/signoff_gate.json` and rides the manifest as `signoff_health` — the verifier fails a
dataset whose provenance is unrecorded or whose gate verdict is dirty.
ORFS completion requires a reconstructable SIX-STAGE lineage, not merely a clean `finish` row —
a repair/resume generation is attributed via `resume_meta.json`'s recorded `parent_lineage` or
sibling ledgers (the latter as the `orfs_lineage=reconstructed` caveat); an orphan repair-only
run blocks (round-2 pilot P0-4). A recorded lineage is DIGEST-VERIFIED at gate time
(RMD2-P0-02, failure-patterns #55): valid non-null sha256 per reused stage, canonical artifact
names (stage contract v2), a same-identity parent with a matching stage-manifest digest, an
acyclic chain, and preserved bytes re-hashed against the record — any failure is a hard
blocker, and the verdict's `lineage_root_digest` rides the manifest. **Strict tier** (`R2G_SIGNOFF_GATE=strict`, pilot P0-1): only
the exact verdict `pass` builds; `pass_with_caveats` blocks. Every manifest carries
`dataset_tier` — `strict_clean` iff the gate verdict was exactly `pass`, else `research` — and a
clean dataset index must filter on it. Each build also stamps a stable corpus-level `graph_id`
(sha1(platform:design) in [1, 2^24), `R2G_GRAPH_ID` overrides; pilot P0-5) into the manifest and
every x1 slot; `[R2G code reference removed]` enforces corpus-wide uniqueness and reports
`BLOCKED / not_applicable` (exit 3 single-case) for intentionally denied designs instead of
crashing (pilot H2).

### 1. Labels (Y) — `[R2G code reference removed]`

Per-cell / per-net regression targets into `<project-dir>/labels/`, plus a per-design
`reports/labels_stats.json`:

- **congestion** (per-gate; dense placement utilization → pure-python gaussian smoothing →
  orientation-aware bbox mapping → 2-vector label),
- **wirelength** (per-net; routed-segment length, log1p µm),
- **timing** (per-cell path delay, via OpenROAD),
- **IR drop** (per-gate, via OpenROAD),
- **RC parasitics** (from `6_final.spef`, via `[R2G code reference removed]`): ground cap
  (`net_ground_cap.csv`, per-net node label), coupling cap (`coupling_cap.csv`, net-pair edge
  label), equivalent resistance (`equiv_res.csv`, same-net pin-pair edge label), + `net_driver.csv`
  (places net↔net coupling edges on driver pins). No SPEF (RCX not run) → header-only RC CSVs.

Fail-soft: a missing input or per-label tool error is recorded in the stats file, not fatal.
Platform-agnostic — liberty/LEF/supply-voltage come from the ORFS platform config. Batch
backfill across completed designs: `[R2G code reference removed]`. See
`references/label-extraction.md`.

### 2. Features (X) — `[R2G code reference removed]`

Graph-feature CSVs into `<project-dir>/features/`: `metadata.csv` (graph-level),
`nodes_{gate,net,iopin,pin}.csv`, `edges_{gate_pin,pin_net,iopin_net}.csv`, plus a per-design
`reports/features_stats.json`. Reads the **same** `6_final.def` (+ optional `6_final.spef`) as
the labels, so feature rows join label rows on `graph_id`+`inst_name`/`net_name`. Fail-soft;
SPEF absence degrades cap/RC columns to 0. Batch backfill: `[R2G code reference removed]`. See
`references/feature-extraction.md`.

### 3. Graph datasets (b–f) — `[R2G code reference removed]`

Assembles the five topologies into `<project-dir>/dataset/{b..f}_graph.pt` — **heterogeneous
`HeteroData` by default** (`R2G_GRAPH_KIND=homo` for the legacy homogeneous format, `both` for
both) — plus the synthesis-netlist bipartite graph (`netlist_graph.pt`, always homogeneous) and a
manifest (`dataset/graph_manifest.json`, mirrored to `reports/graph_dataset.json`). Runs stages 1–2
automatically when their CSVs are missing or older than the DEF (freshness judged by the
`reports/{features,labels}_stats.json` stage-completion markers, not an early CSV).

```bash
[R2G command block removed]
```

Needs torch + torch_geometric + pandas; machines without them SKIP cleanly with a HINT.
A configured `R2G_GRAPH_PYTHON` that cannot start fails loud (exit 4), never a skip.
`R2G_GRAPH_VARIANTS` selects variants (default `bcdef`); `R2G_GRAPH_KIND` picks the output
(`hetero` default / `homo` / `both`); `GRAPH_TIMEOUT` (default 2400s);
`R2G_DEF`/`R2G_ODB`/`R2G_SPEF` pin the DEF/ODB/SPEF for **both** the feature and label stages
(so X and Y stay joined on the same DEF), enabling a backend-less reference-DEF build.
See `references/graph-dataset.md`.

**Check `status` + `label_health` + `signoff_health` in the manifest before training.** `status:
"ok_with_label_gaps"` means ≥1 label file couldn't join (its y slot is all-NaN); the per-file
reason is in `label_health`. `signoff_health.status` outside {pass, pass_with_caveats} means the
dataset was built past the signoff gate (warn/off mode) on a design that is not signed off — the
blockers list says why.

### 4. Four-stage causal dataset — `[R2G code reference removed]`

The **second dataset product**, built from the same backend run but answering a different question.
Stages 1–3 above fold one finished layout (`6_final.def`) into five views; this builder emits **four
`HeteroData` graphs per design** — `floorplan`, `placement`, `cts`, `route` — that share one
canonical post-synthesis topology and one set of post-route labels, and differ only in *what was
legally knowable before that stage ran*. It is the vendored, manually-verified **R2G2.0** pipeline
(`[R2G code reference removed]`, upstream contract in `[R2G code reference removed]`).

```bash
[R2G command block removed]
```

Needs a backend run with all four stage `.odb` (`2_floorplan`/`3_place`/`4_cts`/`5_route`) plus
`6_final.spef`; the runner picks the newest such run, passes the signoff gate, exports the stage
DEFs from ODB, then runs `01…05` and both upstream checkers. See
**`references/four-stage-dataset.md`**.

Key differences from b–f — do not "harmonize" them:

- **Topology comes from `1_2_yosys.v`, not the DEF.** The node set is identical in all four graphs;
  a gate the resizer deleted stays a node with `placement_valid=0`.
- **Labels are raw physical values** (no log/normalize twin), carried as per-type `y[N,2]` with an
  explicit boolean `y_valid_mask`. `04` refuses a graph where `isfinite(y) != y_valid_mask`.
- **Timing/RC relations are supervision only** — their `edge_attr` is `[E, 0]`; only the three
  logical incidence relations (plus `gate|congestion_geom|gate` at cts/route) are encoder input.
- **`5_route.def`/SPEF/STA are label-only.** Stage 02 forks one subprocess per prediction stage so a
  stage can only open its own DEF, and `[R2G code reference removed]` re-checks leakage independently.

**Both checkers must PASS, and a built `heterograph.pt` is not evidence on its own** —
`[R2G code reference removed]` (contract + leakage) and
`[R2G code reference removed]` (`structural_issues=0`).

**Editing `[R2G code reference removed]` requires reading `[R2G code reference removed]` first.** That tree is a
vendored upstream drop plus seventeen recorded deltas (D1–D17); a new drop is re-vendored and the deltas
re-applied, never hand-merged. `[R2G code reference removed]` is our glue and is edited normally.

### 5. Image (feature-map) dataset — `[R2G code reference removed]`

The GCell-raster companion of the graph datasets, CircuitNet style (2026-10-22). Same guards
as `[R2G code reference removed]` (newest run, platform provenance, sign-off gate `enforce`, labels re-made
unless their marker records THIS DEF's digest). Writes `images/{features,labels}.npz` (one
`[x][y]` array per channel), `images/image_meta.json`, colour previews `images/png/`, and
`reports/image_dataset.json` (`status: ok|invalid|skipped` + the verifier's result).

- **Grid** = the congestion label's own sidecar (`labels/congestion_grid.json`), so every channel
  is pixel-aligned with the congestion label by construction.
- **Features** from ONE pre-route DEF of the same run — `R2G_IMAGE_FEATURE_STAGE=place`
  (default, `3_place.odb`) | `cts` | `floorplan`, exported with `write_def`. The builder REFUSES
  a DEF with routed wires (leakage guard). Channels: `cell_density`, `macro_region`, `rudy`
  (net bbox spread of (w+h)/(w·h), sides floored at one GCell), `pin_rudy`, `pin_density`.
- **Labels**: `congestion` / `congestion_raw` (= `congestion_grid.csv`, the same values the graph
  label averages) and `ir_drop` (per-GCell worst of `ir_drop.csv`, mV).
- **Verification is mandatory**: `[R2G code reference removed]` re-parses DEF/LEF with
  its own readers and re-derives the occupancy arrays, area conservation, pin count, the RUDY
  integral, the exact label grids, the per-cell join with the graph label (`cell_congestion.csv`)
  and IR drop — 17 checks; any failure marks the dataset `invalid` (exit 6).
  `[R2G code reference removed]` holds hand-computed values on a non-square toy and one negative
  control per channel.

## The five topologies (b–f)

| Variant | Nodes kept | Folded into edges |
|---------|-----------|-------------------|
| **b** | gate, net, iopin, pin | — (gate-pin, pin-net, iopin-net edges) |
| **c** | gate, net, iopin | pins → gate-net edges (pin features on `edge_attr`) |
| **d** | gate, iopin, pin | nets → pin-clique edges (net features on `edge_attr`) |
| **e** | iopin, pin | gates AND nets → pin-clique edges |
| **f** | gate, iopin | nets → gate-clique edges |

**Default output is heterogeneous** (`HeteroData`): per-type node stores (`gate`/`net`/`iopin`/`pin`)
+ `(src_type, relation, dst_type)` edge stores (relation = the folded entity — b `connects`; c/d/e/f
from `edge_schema`; RC → `rc_coupling`/`rc_resistance`). It is a value-preserving re-view of the
homogeneous source of truth below (`graph_lib.homo_to_hetero`, exact inverse `hetero_to_homo`).

Homogeneous tensor schema (the source of truth; hetero stores re-view it — see
`references/graph-dataset.md` § Heterogeneous graphs): `x[N,10]` (node_type, graph_id, 8 per-type
feature slots), `y[N,6]` (node_type, congestion, IR drop, timing, wirelength, **RC ground cap**; NaN
where a label doesn't apply). Folded entities carry their features/labels on `edge_attr[E,8]` /
`edge_y[E,6]`, interleaved `[fwd0,rev0,fwd1,rev1,...]`. RC **coupling-cap + resistance** labels ride a
*separate parasitic edge set* (`rc_edge_index` / `rc_edge_type` / `rc_edge_y[E,3]`), distinct from the
physical-topology edges (present-but-empty where RC doesn't apply, so the schema is uniform). Every
label tensor has a **RAW twin** (`y_raw` / `edge_y_raw` / `rc_edge_y_raw`) carrying the raw physical
value (EDA-Schema convention) beside the normalized target, so a trainer picks either. Full
schema + per-variant node/edge counts: `references/graph-dataset.md`.

## The tech-lib / LEF / DEF parser (`[R2G code reference removed]`)

The shared per-platform tech layer every stage consumes:

- `[R2G code reference removed]` — supply voltage, tap patterns, cell-type strategy per ORFS platform.
- `[R2G code reference removed]` — Python backend for `[R2G code reference removed]` (same
  `KEY=VALUE` contract): resolves liberty/LEF/tech paths for a platform.
- `[R2G code reference removed]` — the single DEF/SDC parser (instances, nets, pins, routed segments).
- `[R2G code reference removed]` — routing-layer names, pitch/direction, the layer regex matcher.
- `[R2G code reference removed]` — cell / pin / net classifiers (direction, clock, capacitance).
- `[R2G code reference removed]` — the `cell_type_id` map (curated for nangate45, runtime-built for all
  other platforms; MACRO gets a dedicated id).

## Resource Map

| Read this | When |
|-----------|------|
| `references/graph-dataset.md` | Building/reading the PyG b–f graphs, tensor schema, RC-parasitic label views, torch venv, provenance + audit notes. |
| `references/four-stage-dataset.md` | Building/reading the four-stage causal HeteroData graphs (`[R2G code reference removed]`), the causal input whitelist, the ORFS adapters, and the two mandatory checkers. |
| `[R2G code reference removed]` | **Before editing anything under `[R2G code reference removed]`** — upstream provenance, SHA256s, and the seventeen local deltas (D1–D17) that must be re-applied on a new drop. |
| `references/label-extraction.md` | Building the Y side (per-cell/per-net labels + stats). |
| `references/feature-extraction.md` | Building the X side (per-node/per-edge/metadata CSVs + stats). |
| `[R2G code reference removed]` | Per-platform tech/LEF/liberty/DEF parsing internals. |
| `[R2G code reference removed]` (repo-level) | Independently verifying a built dataset against raw DEF/LEF/liberty/SPEF + OpenDB ground truth (`--batch` over many designs). Three named check groups — `topology_checks` (all 5 views b–f), `feature_stat_checks` (column re-derivation + stats-gate honesty + vocab), `signoff_report_checks` (DRC/LVS gate, ppa geometry, timing↔SDC, C_total/equiv_res vs SPEF; opt-in `--signoff-recheck` re-runs PDNSim for the IR-drop label). Run it after any corpus regeneration; see `references/graph-dataset.md` "Comprehensive verification". |

## Project Layout (the dataset dirs this skill writes)

```text
[R2G command block removed]
```

## Hard Rules

- This skill consumes **signed-off** backend output; it does not run or fix PnR. Produce the
  `6_final.def`/`.odb`/`.spef` with the **signoff-loop** skill first.
- Never regenerate a corpus and declare it good without running `[R2G code reference removed]`
  against raw DEF/LEF/liberty ground truth — silent value defects (transposed congestion
  demand, all-NaN IR-drop, quoted-unit cap scaling) have shipped before and are invisible in
  the manifest's row counts. See `references/graph-dataset.md` audit notes and
  the signoff-loop `references/failure-patterns.md` "Dataset-Extraction Silent-Value Defects".
- The label/feature stages are fail-soft by design: always check
  `reports/{labels,features}_stats.json` and the manifest `label_health` for degraded columns
  rather than assuming a non-empty CSV means correct values.
