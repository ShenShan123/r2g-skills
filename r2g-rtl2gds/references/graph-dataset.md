# PyG Graph Datasets (run_graphs.sh — SKILL.md step 13d)

Turns a completed backend run into training-ready PyTorch-Geometric graphs by
joining the feature stage (X, step 13c) with the label stage (Y, step 13b).
Integrated 2026-07-05 from the external RTL2Graph pipeline after a full
correctness audit (see "Provenance + audit" below).

## Running

```bash
# per design (runs 13b/13c first when CSVs are missing or older than the DEF)
R2G_GRAPH_PYTHON=/proj/<you>/pyenvs/r2g-graph/bin/python \
  scripts/flow/run_graphs.sh <project-dir> [platform]
```

Outputs in `<project-dir>/dataset/`:

| File | Content |
| --- | --- |
| `b_graph.pt` .. `f_graph.pt` | five graph topologies (below) |
| `netlist_graph.pt` | synthesis-netlist bipartite cell/net graph (pre-layout) |
| `graph_manifest.json` | per-variant node/edge counts + label-NaN fractions + per-label-file `label_health` (mirrored to `reports/graph_dataset.json`) |

**Check `status` + `label_health` before training on a manifest.** `status:
"ok_with_label_gaps"` means ≥1 label file couldn't join (missing/raw/mismatched
— its y slot is all-NaN); the per-file reason is in `label_health`. The stage
also refuses stale/half-finished inputs: features/labels freshness is judged by
their stage-completion markers (`reports/{features,labels}_stats.json`, written
last), not just an early CSV (2026-07-05 irdrop incident — see
failure-patterns.md "Dataset-Extraction Silent-Value Defects" #6).

Dependencies: torch + torch_geometric + pandas — the only stage needing them.
`run_graphs.sh` probes `R2G_GRAPH_PYTHON` (default `python3`) and SKIPs cleanly
with an install HINT when absent. Install the venv on /proj, never $HOME:

```bash
python3 -m venv /proj/<you>/pyenvs/r2g-graph
/proj/<you>/pyenvs/r2g-graph/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
/proj/<you>/pyenvs/r2g-graph/bin/pip install torch_geometric pandas
```

Knobs: `R2G_GRAPH_VARIANTS` (default `bcdef`), `GRAPH_TIMEOUT` (s, default 2400),
`R2G_DEF` (pin a specific DEF).

## The five topologies (node counts: cordic nangate45 / aes_core sky130hd)

| Variant | Nodes | Folded into edges | Sizes |
| --- | --- | --- | --- |
| b | gate, net, iopin, pin | — (gate-pin, pin-net, iopin-net edges) | 7.9k / 155k |
| c | gate, net, iopin | pins → gate-net edges (pin features on `edge_attr`) | 3.2k / 60k |
| d | gate, iopin, pin | nets → pin-clique edges (net features on `edge_attr`) | 6.2k / 125k |
| e | iopin, pin | gates AND nets → pin-clique edges | 4.8k / 95k |
| f | gate, iopin | nets → gate-clique edges | 1.6k / 30k |

Shared tensor schema (all variants):

- `x[N,10]`: `x0` node_type (0 gate / 1 net / 2 iopin / 3 pin), `x1` graph_id
  (0 unless `--graph-id` given), `x2..x9` per-type feature slots (zero-padded):
  gate = `cell_type_id, cell_area, cell_power, x_um, y_um, orientation_id,
  placement_status_id`; net = `net_type_id, fanout, pin_count, num_drivers,
  num_sinks, connects_macro_flag, num_layer, hpwl_um`; iopin = `pin_x_um,
  pin_y_um, nearest_tap_distance_um, pin_direction_id`; pin = `pin_type_id,
  sum_pin_cap_fF`.
- `y[N,5]`: `y0` node_type, `y1` congestion (gate), `y2` IR drop (gate),
  `y3` timing (pin; the owning cell's log1p path delay), `y4` wirelength (net;
  log1p um). NaN where a label doesn't apply or didn't join.
- Variants with folded entities carry that entity's features/labels on
  `edge_attr[E,8]` / `edge_y[E,5]`, with `edge_type` distinguishing families.
  Edge columns are INTERLEAVED `[fwd0, rev0, fwd1, rev1, ...]` so the
  pairwise-repeated attr rows align (see audit note 5).
- `node_name[N]` joins any node back to the DEF-escaped names in the CSVs;
  `global_feat[15]` is metadata.csv; `x_schema`/`y_schema`/`edge_schema` are
  attached to every Data object.

Semantics to know before training:

- **The clock tree is not in the graph.** Only `net_type_id == 0` (signal) nets
  survive; power/ground/clock/reset/scan nets, FILL/TAP cells, and gates with no
  signal pins are filtered.
- Pin positions approximate to the owning instance origin (pin-geometry offsets
  are not computed), so `pin_x_std_um`-style stats and `hpwl_um` are
  origin-based approximations.
- `netlist_graph.pt` (from `1_2_yosys.v`) shares the feature stage's
  deterministic per-platform `cell_type_id` vocabulary
  (`techlib.cell_types.resolve_cell_type_map`), so its ids agree with
  `nodes_gate.csv` across a platform corpus. Its names are Verilog-unescaped —
  strip backslashes on both sides to join against DEF-side names.

## Batch

Loop `run_graphs.sh` over completed projects (same pattern as
`tools/run_labels_batch.sh`); the stage is idempotent and staleness-aware, so
re-running after a new backend RUN refreshes everything the DEF invalidated.

## Provenance + audit (2026-07-05)

Ported from the operator-provided `RTL2Graph/` pipeline (odb2def, base_garph,
feature_test_v3, label_test, last_graph). The audit against OpenDB/OpenROAD
ground truth (cordic nangate45 + aes_core sky130hd) found:

1. RTL2Graph's `feature_test_v3`/`label_test` are STALE ANCESTORS of this
   skill's extract stages — they still carried the sky130 liberty quote-bug
   (all areas/powers 0, every cell UNKNOWN), the nangate-only `num_layer`
   regex, and the dead fakeram keys the skill had already fixed. The skill's
   stages are the substrate; nothing was ported from those two packages.
2. Five NEW defects found and fixed (four in the skill's own extractors — see
   failure-patterns.md "Dataset-Extraction Silent-Value Defects"; the fifth in
   RTL2Graph's assembly, fixed in this port): register-losing timing join,
   sky130 RECT phantom route points, DEF PIN direction inversion, driver
   max_capacitance in pin-cap sums, and edge_attr/edge_type/edge_y misalignment
   with edge_index in variants c..f (originals aligned 171/3001 sampled pin
   edges on cordic; this port 3001/3001).
3. Equivalence: on identical inputs the port reproduces the originals' node
   tensors and edge sets EXACTLY for all five variants (the only intentional
   divergence is the edge-attr alignment fix).
4. `base_garph`'s Verilog parser was verified exact on real netlists (cells,
   nets, per-net connectivity vs OpenDB). Its hardcoded nangate45 cell map and
   per-process dynamic ids were replaced by the techlib vocabulary.
5. RTL2Graph's `base_graph` input to `last_graph` was dead in single-case mode
   (loaded into config, never used) — the graphs are DEF-derived; the netlist
   graph is provided separately here.

A second audit pass (2026-07-05, sky130-focused) found two more silent-value
defects — sky130 quoted liberty pin attributes (95% of `pin_type_id` collapsed)
and the interrupted-irdrop raw-CSV chain (y2 100% NaN with manifest "ok") —
see failure-patterns.md "Dataset-Extraction Silent-Value Defects" #5/#6.
**Datasets built before BOTH fix waves must be regenerated** (features AND
labels AND graphs; the pre-fix aes_core dataset shipped y2 all-NaN and
collapsed pin types).

Tests: `tests/test_graph_stage.py` (synthetic fixtures; tensor tier skips
without torch). Verification workspace with the ground-truth scripts:
`/proj/workarea/user5/rtl2graph_verify/` (machine-local).
