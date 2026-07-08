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
| `graph_manifest.json` | per-variant node/edge counts + label-NaN fractions + per-label-file `label_health` + RC coverage (`rc_health` + per-variant `rc_edges`/`rc_coupling_edges`/`rc_resistance_edges`) (mirrored to `reports/graph_dataset.json`) |

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
- `y[N,6]`: `y0` node_type, `y1` congestion (gate), `y2` IR drop (gate),
  `y3` timing (pin; the owning cell's log1p path delay), `y4` wirelength (net;
  log1p um), `y5` RC ground cap (net; log1p fF — on the **net node** in b/c,
  **broadcast to the net's pin nodes** in d/e, dropped in f). NaN where a label
  doesn't apply or didn't join.
- Variants with folded entities carry that entity's features/labels on
  `edge_attr[E,8]` / `edge_y[E,6]`, with `edge_type` distinguishing families.
  Edge columns are INTERLEAVED `[fwd0, rev0, fwd1, rev1, ...]` so the
  pairwise-repeated attr rows align (see audit note 5). (`edge_y[:,5]` is always
  NaN — ground cap is a node label, never folded onto an edge.)

### RC parasitic edges (labels, separate from the physical topology)

Coupling capacitance and equivalent resistance are **edge labels over a parasitic
graph that is distinct from the physical-topology `edge_index`** — every `Data`
object carries its own `rc_edge_index[2,E_rc]` + `rc_edge_type[E_rc]` (0=coupling,
1=resistance) + `rc_edge_y[E_rc,3]` (`[type, coupling_cap_label, equiv_res_label]`,
off-type column NaN; labels are log1p). Attachment (`graph_lib.attach_rc_labels`,
per the endpoint-resolution rule — a net endpoint resolves to a net node if present,
else the net's driver pin, else dropped):

| view | ground cap `y5` | coupling (net-pair) | resistance (pin-pair, same net) |
| --- | --- | --- | --- |
| b | net node | net↔net | pin↔pin |
| c | net node | net↔net | — (no pin nodes) |
| d | pin nodes (broadcast) | driver-pin↔driver-pin | pin↔pin |
| e | pin nodes (broadcast) | driver-pin↔driver-pin | pin↔pin |
| f | — (dropped) | — | — |

`rc_edge_*` is present-but-empty (not absent) wherever RC doesn't apply, so the schema
is uniform across every view and design. See label-extraction.md "RC parasitic labels".
The manifest adds `rc_health` (per-design coverage) and per-variant
`rc_edges`/`rc_coupling_edges`/`rc_resistance_edges`. RC is populated only when a SPEF
exists (RCX ran); absent → RC slots empty, `rc_health.status = "no_rc_labels"`.
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

## Verifying a dataset (ground-truth harness)

`tools/verify_graph_dataset.py <case_dir>` (run with `$R2G_GRAPH_PYTHON`)
independently re-derives every structural + label expectation from the CSVs
(separate pandas code, not graph_lib) and diffs the shipped tensors: node
counts, b/c edge counts by row accounting, **d/e/f edge counts by the clique
formula** Σ C(k,2), c/f edge_attr == the folded entity's features, EXACT
expected-NaN counts + sampled values for every y slot, node_name order,
global_feat, netlist-graph counts vs an independent regex, manifest
consistency, and physical-range sanity. It also **independently re-parses the SPEF**
(`read_spef_truth`, separate from `techlib.spef`) to verify the **RC parasitic
labels**: ground-cap `y5` on net nodes == `log1p(SPEF ground)` (and its broadcast onto
d/e pin nodes), the coupling edge count == the SPEF's cross-net signal-net-pair count
with sampled labels == `log1p(SPEF coupling)`, resistance edges are intra-net with
non-negative labels, and the `rc_edge_y` type/column separation holds; a design with no
SPEF is asserted to have `rc_health="no_rc_labels"` + empty `rc_edge_*` everywhere.

The **congestion** recompute (2026-07-07) reproduces the 2-vector method exactly:
an independent radius-4 separable REFLECT Gaussian (`dense_gaussian_r4`) over the
dense util grid, averaged over each cell's orientation-aware bbox GCells
(`_lef_macro_sizes`), checking all three emitted columns
(`cell_congestion`/`label`/`label_raw`). The demand/util grid it smooths is still
re-derived from the raw DEF (`read_def_truth`), so a transpose/dbu/capacity bug in
the extractor still surfaces as a mismatch. (The pre-2026-07-07 check used the
retired radius-1 single-GCell kernel and a universal `label == sqrt(cell_congestion)`
identity, both false under bbox averaging — see failure-patterns.md #19.)

The 2026-07-06 wide-coverage extension additionally re-parses the RAW
liberty/LEF/DEF with its own independent parsers (never `techlib`) and
verifies CSV↔tool truth end-to-end: gate area/leakage/x/y/orientation,
cell_type_id injectivity + the shared MACRO id, macro bus-pin classification,
`sum_pin_cap_fF` vs Σ liberty load caps, net
pin_count/drivers/sinks/hpwl/`connects_macro_flag` vs DEF+liberty+LEF-BLOCK
truth, iopin x/y/direction, metadata section counts/dbu/die/tracks/V_nom, a
FULL independent congestion demand/capacity/gaussian recompute, wirelength vs
an independent DEF route walk (+ `label == log1p(um)`), timing coverage of
every sequential instance, irdrop header/range/`label == log1p(IR/P95)`,
edge symmetry/self-loop/name-uniqueness gates, and
sampled netlist-graph port connectivity. `--batch <root>` sweeps every project
under a corpus root (exit non-zero on any failure) — run it after ANY corpus
regeneration. Baselines: 54/54 × 9 sky130hd designs (pre-extension,
2026-07-06); ALL-PASS (84–87 checks, design-dependent) × 10 nangate45 designs
incl. a fakeram45 macro design (post-extension, same day). Helper parsers are pytest-pinned in
`tests/test_verify_graph_dataset_helpers.py`.

### Comprehensive verification — three dimensions (2026-07-08)

The 2026-07-08 extension organises the harness into three named check groups
(`topology_checks` / `feature_stat_checks` / `signoff_report_checks` in
`verify_graph_dataset.py`), closing the gaps where the historical checks covered
only variant **b** or never cross-checked a sign-off artifact. Pytest-pinned with
clean-pass **and** negative-control tests in `tests/test_verify_comprehensive.py`
(each check is proven to FAIL on a deliberate corruption — a check that cannot
fail is the silent lie the harness exists to prevent). Baselines: **iir 167/167,
DMA_Controller_DMA_fsm 164/164** (sky130hd, RC-complete).

- **`top.*` — TOPOLOGY of ALL five views b–f** (was b-only): symmetry +
  self-loop ban + per-block `node_name` uniqueness on c/d/e/f too; the
  **block-positional node order** (concat of the per-block mergesorted name
  lists in the view's block order, **pin block included**) — the single guard
  that labels align by position; the **`[fwd0,rev0,…]` interleaving invariant**
  (cols 2k/2k+1 are index-reverses and share attr/type/y rows) on the directed
  edges of c/d/e/f **and** on `rc_edge_*` for every view (audit bug #5 guard);
  and **d/e edge_attr content** (d net-clique→NET feats, d gate_pin→zeros, e
  gate-clique→GATE feats, e net→NET feats), completing the c/f coverage. A stale
  pre-RC dataset (`edge_y` width 5, no `rc_edge_*`) FAILs loudly here — never an
  IndexError (the 2026-07-08 DMA stale-dataset incident).
- **`feat.*` — FEATURE STATISTICS**: re-derives columns nothing checked before —
  `placement_status_id` (== DEF PLACED/FIXED) and `fanout` (== max(0,pin_count−1))
  exactly, `num_layer` and `nearest_tap_distance_um` **bounded** (their quirky
  worker semantics are pinned exactly on the synthetic fixture instead, to avoid
  a false-fail); categorical **vocab/enum coverage** on the tensors (net nodes
  all `net_type_id==0`; orientation∈[0,7], placement∈{0,1}, pin_dir∈[0,3],
  pin_type∈[0,14], `cell_type_id≥0`); and a **stats-gate honesty** check that
  independently recomputes every `features_stats.json` / `labels_stats.json`
  distribution (same `_percentile` as the gate) from the CURRENT CSVs and diffs
  — catching a stale or hand-edited stats JSON, a lie no prior check saw.
- **`signoff.*` — LABELS ↔ SIGN-OFF REPORTS**: a **DRC/LVS clean-provenance
  gate** (`reports/{drc,lvs}.json` status ∈ {clean, clean_beol} — a dataset built
  on a dirty run is invalid); **geometry vs `ppa.json`** (`io_count` exact,
  `macro_count` == DEF BLOCK-class instances, `sequential_count` == liberty-`is_seq`
  instances — the fill-inflated `instance_count` is deliberately NOT asserted);
  the **timing label ↔ SDC clock-period transform** (`Path_Delay ==
  max(0, period − Cell_Slack)` from `6_final.sdc`, `label == log1p(Path_Delay)`,
  off-path all-zero) tying the target to the sign-off constraint file; and
  **`C_total` ∈ [Σg+Σc, Σg+2Σc]** + **`equiv_res` ≤ ΣR / scale-sane** vs an
  independent SPEF re-parse (the equiv_res bound catches the classic ohm↔kΩ
  unit bug). Timing `report_checks` goes to `/dev/null` and PDNSim's raw dump is
  deleted on success, so those two labels leave no report to diff — the opt-in
  **`--signoff-recheck`** re-runs OpenROAD `analyze_power_grid` on `6_final.odb`
  to re-derive the IR-drop label per cell (needs `OPENROAD_EXE`; honestly SKIPs,
  never passes vacuously, when absent).

**Complement — the synthetic corner-case suite (2026-07-06 nangate45 round 2).**
`verify_graph_dataset.py` cross-checks a REAL built dataset against the raw
liberty/LEF/DEF, so it only sees inputs the real designs *have* — it stays green
while a bug hides in a code path those designs never exercise (e.g. `ff_bank`,
tie-off constants, a truncated CSV). Two suites close that gap by driving the
REAL workers over inputs the extractors control:
`tests/fixtures/corner_synth.py` builds a hand-computable synthetic
nangate45-style design (std cells + a bus-pin SRAM macro; a
clock/reset/multi-layer/RECT-patch/2-driver net mix), and
`tests/test_corner_case_pipeline.py` runs it through feature workers → label
extractors → the PyG builder, asserting every stage against hand-derived ground
truth **across all five views b–f** (node/edge counts, folded-entity `edge_attr`
features + `edge_y` labels, clock-tree/FILL/TAP exclusion, undirected symmetry).
`tests/test_corner_case_units.py` pins focused corners (ff_bank/latch/statetable
sequential detection, INOUT/PG/multi-digit-bus-index classification, pf→fF caps,
CUT-vs-ROUTING LEF + VIA re-declaration, the congestion demand-key
`(x_gcell, y_gcell)` convention under an ASYMMETRIC grid, netlist constant
handling, and the `compute_feature_stats` honesty gate). Rule: a fixture liberty
MUST be one-attribute-per-line (the parser uses anchored `re.match`) — a crammed
pin silently drops direction/clock/cap and the test passes vacuously.

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

A third pass (2026-07-06, nangate45 round 2, commit 031a12f) re-verified the
pipeline on nangate45 — `verify_graph_dataset --batch` green 85/85 × 10 designs,
and wirelength independently cross-checked vs OpenROAD `getLength` over 32,005
aes_core nets (0 real mismatches) — then built the corner-case suites above,
which surfaced four more defects (all behavior-neutral on nangate45; see
failure-patterns.md #15–#18): `ff_bank`/`latch_bank` multibit-sequential
undetected (inert, asap7), `compute_feature_stats` missing its honesty gate,
netlist tie-off-constant phantom nets, and two latent parity fixes. These do NOT
change nangate45 CSV/graph output, so datasets built after the 2026-07-06 round-1
regeneration remain valid.

Tests: `tests/test_graph_stage.py` + `tests/test_corner_case_pipeline.py` +
`tests/test_corner_case_units.py` (synthetic fixtures; tensor tier skips
without torch). Verification workspace with the ground-truth scripts:
`/proj/workarea/user5/rtl2graph_verify/` (machine-local).
