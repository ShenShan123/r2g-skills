# RTL2Graph Integration + Correctness Audit — 2026-07-05

**Branch:** `feat/rtl2graph-integration` (worktree). **Commits:** `4d8e032`
(label fixes), `6b09000` (feature fixes), `69c10e2` (graph stage).
**Verification workspace (machine-local):** `/proj/workarea/user5/rtl2graph_verify/`.

## Task

Integrate the operator-provided `RTL2Graph/` pipeline (ODB→DEF, Verilog→PyG
netlist graph, feature/label CSVs, five graph-dataset topologies) into the
r2g-rtl2gds skill so every completed design can become a GNN training graph
autonomously — verifying RTL2Graph's correctness BEFORE touching skill code.

## Verification method (reusable)

Ground truth via `openroad -python` (OpenDB counts, ITerm directions, sampled
placements/connectivity), `report_wire_length -detailed_route` per net, and
`report_checks` per endpoint — on cordic (nangate45, ORFS leftovers) and
aes_core (sky130hd, fresh campaign run). Then equivalence testing: run the
RTL2Graph originals and the port on IDENTICAL inputs, diff tensors.

## Findings

**RTL2Graph's `feature_test_v3` + `label_test` are stale ancestors** of the
skill's `scripts/extract/{features,labels}` (the skill had already fixed the
sky130 liberty quote-bug, nangate-only `num_layer`, fakeram dead keys, and has
per-platform cell-type vocabulary in `techlib.cell_types`). They were NOT
ported; the skill's stages are the substrate.

**Five new defects** (1–4 also live in the skill's extractors — all fixed;
full detail in `failure-patterns.md` "Dataset-Extraction Silent-Value Defects"):

1. Timing labels lost on EVERY register — STA `get_full_name` (unescaped) vs
   odb `getName` (DEF-escaped) join miss. cordic: 0/56 registers labeled;
   aes_core sky130hd: 5/2476. Fixed → 56/56 and 2476/2476.
2. sky130 `RECT ( dx dy dx dy )` patch groups parsed as absolute route points →
   wirelength inflated ~100–400× on 1283/30k aes_core nets (1168 µm vs
   OpenROAD's 3.29 µm), congestion "utilization" up to 11×. nangate45 unaffected
   (no RECT in NETS) — which is why the techlib correspondence tests never saw it.
3. DEF PIN direction inverted in `num_drivers`/`num_sinks` (chip-perspective);
   every output-port net was 2-driver/0-sink. Also implemented the hardwired-0
   `connects_macro_flag` via new `techlib.liberty.macro_cell_keys`.
4. `sum_pin_cap_fF` summed the driver's `max_capacitance` (a drive LIMIT) into
   net load (62.54 fF vs true 3.19 fF) — new `get_pin_load_cap_fF`.
5. RTL2Graph c–f variants misaligned `edge_attr`/`edge_type`/`edge_y` with
   `edge_index` ([fwd|rev] concat vs pairwise attr repeat): 171/3001 sampled
   pin-edge attrs aligned on cordic. The port interleaves fwd/rev → 3001/3001.

**Verified-good:** base_garph Verilog parser exact vs OpenDB (cells, nets,
per-net connectivity); odb_to_def; b-variant assembly (node counts vs
independent recomputation, feature/label joins by name, edge-type closure);
name-escaping consistent across all CSVs (DEF-escaped everywhere).

**Also noted:** RTL2Graph's `base_graph` input was DEAD in single-case mode
(loaded, never used) — graphs are DEF-derived; the base_garph nangate45
hardcoded cell map assigned per-PROCESS dynamic ids off-platform (corpus
inconsistency) — replaced by the techlib vocabulary in the port.

## What shipped

- `scripts/extract/graph/`: `graph_lib.py` (consolidated core), `build_graphs.py`
  (variants b–f + manifest), `netlist_graph.py` (bipartite netlist graph,
  techlib vocab, names attached), `odb_to_def.py` (utility).
- `scripts/flow/run_graphs.sh` — stage 13d: staleness-aware auto-run of
  13b/13c, `R2G_GRAPH_PYTHON` torch-venv probe with fail-soft SKIP,
  `<project>/dataset/*.pt` + `reports/graph_dataset.json`.
- Tests: `test_graph_stage.py` (9), `test_feature_semantics_fixes.py` (6),
  RECT cases in `test_techlib_def_parse.py` (3). Suite: 964 passed / 16 skipped.
- Docs: `references/graph-dataset.md` (new), dated correction notes in
  `references/{feature,label}-extraction.md`, SKILL.md step 13d, CLAUDE.md row.

## Operator actions still open

- **Regenerate any pre-2026-07-05 label/feature CSVs before training** —
  timing/wirelength/congestion/num_drivers/sum_pin_cap values changed (were
  wrong). Same for the machine-local byte-baseline
  (`tools/regen_extract_baseline.sh`).
- The torch venv used here: `/proj/workarea/user5/pyenvs/rtl2graph` (torch
  2.12.1+cpu, PyG 2.8.0, pandas 3.0.3) — set `R2G_GRAPH_PYTHON` to its python.
- `RTL2Graph/` stays an untracked reference copy at the repo root (252 MB with
  sample outputs; not committed by design).
- Corpus-level batching (a `tools/run_graphs_batch.sh` + dataset aggregation
  across designs with real per-design `graph_id`s) is the natural next step.

## 2026-07-05 (second pass): sky130-focused verification round — 2 more silent-value defect chains (commits 0574308, f1302ee)

A same-day independent verification round (fresh audit of topology conversion,
techlib/LEF parsing, feature + label extraction on sky130) confirmed the
port's topology is exact (b-variant node/edge counts + spot connectivity +
netlist-graph counts re-derived independently; c-variant edge_attr alignment
4000/4000) and found two defects the first pass missed:

1. **sky130 quoted liberty pin attributes** (`0574308`): `direction : "input";`
   and `clock : "true";` never matched the unquoted-only regexes — every sky130
   std-cell pin lost direction (pin_type_id 95% collapsed to 14; num_drivers
   always the assume-1 fallback; 390 nets + 1,065 pin-cap sums provably wrong
   on aes_core). Supersedes the "equivalence-proven" caveat: the port faithfully
   reproduced features that were themselves degenerate on sky130.
2. **Interrupted-irdrop raw-CSV chain** (`f1302ee`): the 09:03 validation run's
   labels stage had been killed mid-irdrop; the RAW PDNSim dump sat at
   labels/ir_drop.csv and the shipped aes_core dataset carried y2 100% NaN
   under manifest status "ok". Four honesty gaps fixed: atomic publish
   (extract_irdrop.tcl), stats "invalid" status, manifest `label_health` +
   `ok_with_label_gaps`, completion-marker freshness in run_graphs.sh. Plus
   loud duplicate-key guards on every graph-side label/feature join.

**Supersedes above:** "Suite: 964 passed" → 983 (torch venv) / 968 (stdlib);
the "Regenerate pre-2026-07-05 CSVs" action now extends to the 2026-07-05
morning aes_core dataset itself (all-NaN y2 + collapsed pin_type_id) — the
corrected reference dataset lives at
`/proj/workarea/user5/rtl2graph_verify/aes_core_fixcheck/` until the corpus
regeneration runs. Detail: failure-patterns.md "Dataset-Extraction
Silent-Value Defects" #5/#6.
