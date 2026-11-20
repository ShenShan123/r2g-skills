# Experiment 4 — dataset validation protocol (frozen 2026-11-07, before any result was seen)

Claim under test: the released R2G datasets are correct physical supervision — (V1) their
contents equal EDA ground truth recomputed from the raw layout files, (V2) they rebuild
bit-identically, (V3) placement-stage inputs carry no route-stage information, (V4) the
validation catches the silent-value defects such pipelines are known for.

Datasets under test (read-only):
- **A** `memlab203:/data/r2g_n45_pilot500/R2G_dataset_6labels_v1` — 500 designs × {placement,
  route} × views B–F, label contract `r2g6_v2_synthesis_fixed_buffer_group_wirelength_six_tasks`.
- **B** `208:/home/yangao/cnn_pilot500/npz/*.npz` — CNN images (X: 6 placement channels,
  `cong` = stored GR usage/capacity [h,v], `ir_drop_mV`).
- Raw layouts: `memlab203:/data/r2g_n45_pilot500/nangate45_pilot500/<design>/`.

## 0. Sample (frozen in `sample.json`, `dev_designs.json`; `make_sample.py`, seed 20261107)

Strata from `select_cells.tsv`: small ≤1k (42), medium ≤10k (431), large >10k (27).

- **V1 test sample: 43 designs** = 25 of the 27 large + 5 small + 13 medium. Two large designs
  are held out so that a disjoint 6-design *development* set (2 per stratum, seed 20261108,
  `dev_designs.json`) exists for later converter development; converters are developed on the
  dev set and scored on this sample.
- Why this size: entity-level checks run over every gate/net/pin of each sampled design
  (≈ 10^5–10^6 entities per group), so entity-level rates are tight; the binding quantity is the
  design-level failure rate. With 0 failing designs out of n, the one-sided 95 % upper bound is
  ≈ 3/n: 7 % overall (n=43), 12 % within the large stratum (n=25, i.e. 93 % of all large
  designs). Large designs are over-sampled on purpose — size-dependent defects (grid boundary,
  hierarchy/escaped names, backend buffering, long nets) concentrate there. The budget limit is
  203's shared CPU (≤ 6 processes); 43 designs fit in a few hours.
- Pilot: one design per stratum (`sample.json:pilot`), run first; the code may be fixed after the
  pilot for **crashes and parser bugs only** — tolerances and pass criteria below are not changed.
- V2: 10 designs (2 S, 5 M, 3 L) ⊂ V1. V3: 3 designs (1 per stratum) ⊂ V2. V4: 5 designs
  (1 S, 2 M, 2 L) ⊂ V1, disjoint from V3.

## 1. Independence rule (V1)

The V1 recomputation (`gt_raw.py`) imports and copies **no** code from the dataset builders
(`reproduce/*.tar.gz`, `cnn_pilot500/scripts`). It reads only raw EDA files with its own parsers
(DEF, LEF, Liberty, gate-level Verilog, SPEF, OpenROAD logs/JSON) plus OpenROAD/OpenSTA (Tcl /
Python API) for STA and the stored GR grid. Definitions were taken from the datasets' docs
(`docs/LABEL_SPEC.md`, `docs/GEOMETRY_TIMING_AND_INPUTS_REFERENCE.md`, `rasterize.py` docstring).
Where the docs do not pin a definition (e.g. the D/E/F pair rule, which LEF point is "the pin
position"), the builder source was *read* to learn the intended definition and it is then
re-implemented; these are listed in RESULTS.md as "definition-from-code" checks. The only
dataset-owned *data* file used is `encode_map.csv` (the categorical id dictionary) as a lookup
table; it is checked for consistency, not trusted. Torch is used only to open the `.pt` files
(adapter), never in the recomputation.

Code layout (shared with a later def-graph-format study):
- `gt_raw.py` — format-independent ground truth per design (identity, edges by name, positions,
  cell masters/types, Liberty pin caps, route wirelength per physical net, per-layer demand grid,
  GRT tables, STA JSON, SPEF ground/coupling cap). Output: a pickled dict, no dataset knowledge.
- `match.py` — shared comparison and frozen tolerances (set F1, value match rate, max abs/rel).
- `adapter_r2g6.py` — thin loader of dataset A into the neutral "claims" layout.
- `img_gt_dump.py` (OpenROAD python), `img_check.py` — dataset B.

## 2. V1 — equivalence to EDA ground truth: groups, tolerances, pass criteria

All comparisons are by **name**, never by row position. Every group reports n, match rate,
max |abs| error, max relative error (and P/R/F1 for sets). A group passes on a design only if
its criterion holds for **every** entity (no percentage slack). A design passes V1 if every
applicable group passes. Frozen tolerances (float64 labels unless noted):

| Group | Ground truth (raw) | Criterion |
|---|---|---|
| I1 gate identity | 2_floorplan.def COMPONENTS with ≥1 signal-net connection | set F1 = 1 |
| I2 net identity | 2_floorplan.def NETS with USE not POWER/GROUND | F1 = 1 |
| I3 pin identity | (inst,pin) incidences of I2 nets on I1 gates | F1 = 1 |
| I4 IO identity | PINS with USE not POWER/GROUND | F1 = 1 |
| I5 netlist cross-ref | 1_2_yosys.v instances / nets | reported only (floorplan may legally differ from synthesis) |
| T1 B edges gate–pin, pin–net, io–net | DEF incidences | F1 = 1 (as name-pair multisets) |
| T2 C/D/E/F edges | re-derived from T1 + Liberty/IO direction with the documented driver→sink-or-star rule | F1 = 1 (definition-from-code) |
| F1 integer/categorical features (cell_type_id via encode_map of the *stage* master, drive strength, pin_count, num_drivers/sinks, fanout, direction flags, sequential/buffer/inverter flags, counts in u) | DEF + Liberty | exact |
| F2 coordinates (gate origin/center, IO pin, die w/h) µm, float32 storage | stage DEF / 2000 + LEF SIZE (orientation-aware) | `|a-b| ≤ 1e-4 + 1e-6·|b|` |
| F3 pin position | inside the placed, oriented LEF pin-shape bbox (EDA truth); exact match to the bbox centre reported | inside-bbox for all pins with tolerance 1e-3 µm |
| F4 Liberty values: pin cap fF, cell area, leakage, total sink cap, total lib pin cap | NangateOpenCellLibrary_typical.lib (+fakeram libs), own parser, units read from the file | rel 1e-5 (float32) |
| F5 SDC values: clock period, freq, input/output delay | constraint.sdc evaluated by tclsh with stub SDC commands | rel 1e-6 |
| F6 placement-stage geometry absent | floorplan DEF: std cells unplaced | every position-derived column NaN with valid flag 0 iff the floorplan entity is unplaced |
| L1 group wirelength (µm) | own per-physical-net centreline length from 5_route.def (ROUTED/NEW/FIXED, no vias, no SPECIALNETS) + own synthesis BUF/CLKBUF grouping from 1_2_yosys.v and backend BUF connectivity in 5_route.def | `|a-b| ≤ 1e-6 + 1e-9·|b|` on every group the independent reconstruction resolves; coverage reported; total DEF length vs `5_2_route.json` within 0.1 % |
| L2 gate congestion / overflow proxy | own per-layer 2.1 µm demand raster of 5_route.def; capacity `w·h/pitch·adjusted/original` from the last GRT-0053 table in `5_1_grt.log`; gate sampled at its 5_route.def origin | congestion `≤ 1e-9 + 1e-9·|b|`, overflow `≤ 1e-6 µm`; mask equality exact |
| L3 GCell-percentage proxy | `100·count(u>1)/(nx·ny)` of the same raster | `≤ 1e-9` |
| L4 TNS, signed WNS | `logs/**/6_report.json` (finish setup tns/ws), result.json `timing_path_constrained` | `≤ 1e-9` ns; NaN/mask iff unconstrained |
| L4b independent STA | OpenSTA rerun on 5_route.def + 6_final.spef + constraint.sdc, propagated clock | reported; agreement criterion WNS `≤ 5 ps`, TNS `≤ max(0.01 ns, 1 %)` |
| L5 E-view gate labels | cell_arc edge_y vs own L2 value of the arc's owning gate (owner by pin name) | as L2 |
| L6 cross-stage | placement and route graphs carry identical labels | exact |
| N1 native-GRT context | `[GRT-0096]` total overflow vs proxy P>0 | reported only (the proxy is documented as *not* native truth) |
| IMG-X image inputs | own raster of 3_place.def (cells/macros/pins/RUDY on the stored GR gcell grid) | per pixel `|a-b| ≤ 1e-4·max|channel| + 1e-6`; power channel via own OpenSTA call, same tolerance |
| IMG-Y image label `cong` | own read of 5_1_grt.odb GCellGrid capacity/usage (OpenROAD python) | `≤ 1e-6`; plus own sum of usage vs GRT-0096 table (reported) |

## 3. V2 — bit-identical rebuild

Rebuild the 10 V2 designs into a fresh directory on 203 `/data` with the shipped
`reproduce/` sources (input_feature_build_sources → B views → C–F views → group-WL upgrade),
the shipped runtime dependencies, `/usr/bin/yosys` (sha recorded in SOURCE_MANIFEST) and the
best available Python runtime (the frozen `py39` env under `/data1/zhouzw` is not readable by
us; this is recorded as a deviation). Only path constants that point to unreadable locations
are re-pointed (listed in RESULTS.md). Compare every file sha256 against
`analysis/dataset_manifest.json`. On mismatch: diff every tensor/attribute field (`torch.equal`
with NaN-equality, dtype, shape, Python metadata) and classify as *value-identical,
serialization-different* or *value-different*. Images: re-run the shipped `dump_design.py`,
`ir_design.tcl`, `rasterize.py` on the same 10 designs, compare npz sha and every array.
Pass = byte-identical; value-identical is reported separately and is not a pass for the
"bit-identical" claim.

## 4. V3 — no route-stage leakage (counterfactual)

For the 3 V3 designs, rebuild with route-stage artifacts replaced: (a) **perturbed own route**:
5_route.def with extra Manhattan wire on a subset of nets, GRT-0053 adjusted resources scaled,
6_report.json/result.json/6_finish.rpt timing shifted consistently, 5_2_route.json changed;
(b) **donor route**: all route-stage files taken from another design. Placement-stage (and
route-stage, which reads 4_cts.def) input tensors must be byte-identical (`torch.equal` on every
x, x_valid_mask, edge_index, edge_attr, u, names, schemas, wl readout mappings) while labels
change. A builder refusal is recorded as the guard. Images: (a) 5_1_grt.odb re-globally-routed
with a different layer adjustment, 5_route.def/6_final.spef from a donor → X must be identical,
labels change; (b) donor 5_1_grt.odb. Static list: files each stage may read (from the code) +
dynamic list (Python audit hook on `open` for the graph feature stage; `strace -e openat` for
OpenROAD image dumps). Pass = inputs byte-identical under every counterfactual that the builder
accepts, and no route-stage file in the dynamic read set of an input stage.

## 5. V4 — negative controls

On copies (scratch only) of the 5 V4 designs' graphs (and npz for image defects), inject:
D1 transposed congestion axes; D2 pin cap ×1000; D3 pin cap ×0.001; D4 name-escaping join drop
(labels of entities with escaped `\[`/`/` names set missing); D5 shuffled gate label↔node
alignment; D6 route-stage gate positions leaked into placement-stage features; D7 zero-filled
label column marked valid; D8 NaN label column marked valid; D9 wrong die scale (coordinates ×2,
DBU 1000 vs 2000); D10 WNS clipped to ≤0 (wrong sign convention); D11 timing from wrong stage
(`5_1_grt.json` ws); D12 wirelength with via/patch inflation (+10 % on 5 % of groups);
D13 1 % of pin–net edges dropped; D14 cell_type_id collapsed to UNKNOWN for one master;
image: DI1 transposed `cong`, DI2 h/v label swap, DI3 channel order swap, DI4 route-stage
(GR usage) leaked into an input channel. Run V1 and the dataset's own acceptance checks
(manifest sha, `graph_adapter.check_graph`, `r2g5.validate_graph` label invariants with a
documented contract shim, cross-view group readout equality) on clean and corrupted copies.
Detection = at least one check that passes on the clean copy fails on the corrupted copy.
Report detection rate per defect (target 100 %) and the clean-copy failure rate (target 0).

## Amendments (dated; recorded after the pilot / after diagnosing V1 failures)

No tolerance was widened. Each amendment fixes the checker so that it computes the dataset's
*documented* definition (METHOD), or isolates a spec ambiguity; real defects were left failing.

- **A1 (2026-11-07, pilot) — DEF escape canonicalisation.** DEF writes `b\[0\]$_DFF_`; the escape is
  syntax, not identity. Ground-truth names are stored de-escaped (canonical-name collisions are
  counted: 0 in all 43 designs).
- **A2 (2026-11-07, pilot) — per-pin / per-IO `clock_period_ns`.** The docs do not define it; the builder
  sets it only for pins in a clock domain (0 elsewhere). The check became "value ∈ {0, SDC period}"
  (weaker than the original equality; `u` clock periods stay exact).
- **A3 (2026-11-07, after V1) — wirelength resolver.** LABEL_SPEC.md: ownership is verified with
  "驱动、完整单元功能的合法引脚置换、新增BUF/INV相位和唯一逻辑克隆证明". Our resolver compared a
  backend clone's input *net names*. A clone's input is often a backend-buffered copy of the original
  input, e.g. C01134 `clone11741` NOR2 with inputs `_18162_` and `net11974`, the latter owned by group
  `_18229_`. The resolver now compares input *groups*, ignores backend 1-in/1-out cells when finding a
  cluster's driver, and iterates so that buffer trees under a resolved clone are resolved too.
- **A4 (2026-11-07, after V1) — Verilog `[k:k]` buses.** C02816 declares `input [0:0] i_register_ready`
  and connects it bare (`.A(i_register_ready)`). Our parser treated the bare name as a different net
  from the port bit `i_register_ready[0]`, which split one synthesis group into two. This was a parser
  bug and is fixed.
- **A5 (2026-11-07, after V1) — entity-name convention (SPEC-AMBIGUITY).** docs/ say identities come
  from the real floorplan DEF but are silent on spelling. The builder documents
  `canonical_name()` (02_extract_features.py: "基础图统一保存Yosys风格的`.`"), which maps DEF `/` to `.`.
  Name-joined groups now use the builder's documented convention. Strict DEF-name identity is
  reported separately as S1 (reported only), and S2 (pass/fail) requires the convention to cause no
  collisions.
- **A6 (2026-11-07, after V1) — image tolerance floor.** The frozen `1e-4·max + 1e-6` absolute floor is
  larger than the entire power-density channel (~1e-6 W/µm²). It hid a real defect in 6 designs. The
  floor is now `1e-4·max|channel|`: a TIGHTER tolerance that is still ~1000× looser than the float32
  storage precision.
- **A7 (2026-11-07, pilot) — image pin density.** OpenDB types Liberty clock pins as CLOCK, not SIGNAL.
  The rasterize docstring says "signal pins", so clock pins are excluded (definition-from-code).
- **A8 (note)** — the route-stage HPWL check (unsplit nets only) was added during implementation.
  Its tolerance is 1e-3 µm + 1e-5 relative, about 8 float32 ulps at 2000 µm.
- **A9 (2026-11-07) — image GCell binning in exact DBU.** `v1_images_dg.cell` floored float-um
  coordinates, so a pin exactly on a GCell line (C00198 `_1797_/ZN`, x = 28500 dbu, step 5700:
  14.25 // 2.85 == 4.0) was binned one GCell low -- the same rounding def-graph's `build_images.Grid`
  had, so this check could not see the builder's error. Found by the conversion study: both LLM
  converters, which binned in integer DBU per the spec, disagreed with both identically. Now both the
  checker and def-graph bin in DBU (half-open cells, a point on a line belongs to the cell above).

---

## v2 — ground truth for the redesigned conversion study (frozen 2026-11-07, before any v2 score)

This section was written before `v2_defgraph_check.py` scored any dataset.

**Scope.** The ground-truth side of the v2 conversion study; the design of the study itself is
in `exp4_conversion/PROTOCOL_v2.md`, owned by the conversion agent. The comparison agent does
the scoring.

### v2.0 Sample

`sample_v2.json` comes from `make_sample_v2.py` with seed 20261111. Its inputs are frozen in
`sample_v2_inputs/`.

- **Draw:** 30 designs per platform, uniform, no strata. Each platform draws with its own
  `random.Random(20261111)` over its sorted pool.
- **Cell range:** 300–30,000 cells on every platform.
- **nangate45:** the pilot500 designs, excluding the 6 dev designs and the 15 designs def-graph
  was debugged on. That way R2G is not scored on the designs whose failures produced its
  fixes. Pool: 462.
- **sky130hd / gf180:** baseline re-runs on memlab216, restricted to dev-split designs
  (`splits/dev.txt`, sha `1e500510…`). The verdict of record follows `rerun_tally.py`:
  - DRC passes if `drc_status == clean`, or if a recovered `6_drc.lyrdb` is newer than the
    GDS and has 0 items;
  - LVS passes if `lvs_status == clean`.

  Pools: 649 (sky130hd) and 353 (gf180).
- **Proof designs:** the first 3 draws per platform (`meta.<plat>.checker_proof`) prove the
  checker on R2G.

### v2.1 Inputs the truth reads

| platform | layout | SDC (timing) | tech LEF / cell LEF / Liberty |
|---|---|---|---|
| nangate45 | `5_route.def` (pilot500 has no 6_final.def) | `constraint.sdc` | as v1 |
| sky130hd | `6_final.def` | `6_final.sdc` | `sky130_fd_sc_hd.tlef`, `sky130_fd_sc_hd_merged.lef`, `sky130_fd_sc_hd__tt_025C_1v80.lib` |
| gf180 | `6_final.def` | `6_final.sdc` | `gf180mcu_5LM_1TM_9K_9t_{tech,sc}.lef`, `gf180mcu_fd_sc_mcu9t5v0__ff_n40C_5v50.lib.gz` |

- All three platforms also read `6_final.spef`.
- **Platform files** are what ORFS resolves for these runs: `make print-LIB_FILES/TECH_LEF/SC_LEF`
  inside `localhost/r2g-orfs:26Q3` with the run's own config.mk. They are byte-identical in
  memlab203's ORFS checkout (sha256 compared).
- **gf180 corner.** The flow's corner is ff_n40C_5v50 (the ORFS gf180 default), and the truth
  uses that corner.
- **Timing truth:** OpenSTA in OpenROAD 26Q3-318-g6b9d7fb806, `sta_pins_dg.tcl`. It reads
  `read_lef` + `read_liberty` + `read_def`, then the SDC whole as given. No parasitics.

### v2.2 Definitions

These are the def-graph documented definitions (graph-dataset.md, feature-/label-extraction.md;
CONTRACT.md where those are silent). Per-platform items:

- **Tap masters** (nearest_tap_distance_um), upper-case substrings: `TAP`; gf180 also `FILLTIE`,
  `ENDCAP`. *Definition-from-code:* techlib.profile / liberty `_PLATFORM_TAP_EXTRA`.
- **Gate exclusion:** master contains `FILL` or `TAP` (all platforms). *Definition-from-code:*
  graph_lib.build_feature_views.
- **Routing-layer pitch:** a two-value `PITCH x y` (sky130 li1) means the spacing across the
  routing direction: y for HORIZONTAL, x for VERTICAL. *Definition-from-code:*
  techlib.lef.routing_layer_info; it is also the physically meaningful reading.
- **Pin in-cell position:** the mean of box centres over all PORT shapes. A rectilinear
  `POLYGON` counts as the boxes OpenDB stores for it: maximal horizontal slabs, with vertically
  adjacent slabs of the same x-span merged. This is the OpenDB getAvgXY contract named in
  graph-dataset.md; our implementation is `gt_raw.polygon_slabs`. gf180 cell LEFs carry 2,135
  POLYGONs.
- **Liberty:** `.lib.gz` is read directly. Capacitance is scaled by `capacitive_load_unit`
  (sky130 `"pf"` ×1000). Quoted attribute values are accepted.

**Folded-entity edges.** Families are named after the folded entity:
- b: `connects`;
- c: `pin` / `iopin_connection`;
- d: `gate_pin` / `net`;
- e: `gate` / `net`;
- f: `net`.

Rows per undirected edge (both directed columns identical):

| family | edge_attr[8] | edge_y / edge_y_raw (5 label slots) |
|---|---|---|
| c `pin` | the pin's [pin_type_id, sum_pin_cap_fF, 0×6] | timing only: log1p(path delay) / raw path delay of the pin's gate |
| c `iopin_connection`, d `gate_pin` | zeros | all NaN |
| d/e/f `net` (clique over the net's kept endpoints) | the net's 8 features | wirelength only: log1p(µm) / µm |
| e `gate` (clique over the gate's kept pins) | the gate's 7 features + 0 | congestion only: √G-mean / G-mean (IR-drop slot not scored) |

A pair of nodes joined by several folded entities gets one edge per entity: parallel edges. They
are matched as a multiset per (family, endpoint pair).

**RC parasitic edges.** These come from our own SPEF pass (`truth_defgraph.parse_spef_v2`).

- **Coupling:** each 4-field `*CAP` line counts once per unordered node-token pair. Both nodes
  are resolved to their nets independently, and the cap is summed per unordered net pair (fF).
- **Equivalent resistance:** for every pair of `*CONN` pins, `*I` and `*P`, joined in the
  net's `*RES` graph. The truth value is the Kirchhoff effective resistance: the path sum on a
  tree, and the Laplacian pseudo-inverse on a component with a cycle or parallel resistors.
  Such components are counted (`spef_stats.cyclic`). def-graph documents "traversal spanning
  tree, parallel paths ignored" for them. A difference there is classified when it appears,
  not pre-judged.
- **Attachment** (graph-dataset.md table):
  - b/c: coupling on net↔net (kept nets only);
  - d/e: coupling on driver-pin↔driver-pin. The driver is the first `*I` pin listed with
    direction O, else the first `*P` with direction I, else the first connection
    (*definition-from-code:* label-extraction.md + spef `_pick_driver*). The driver must be a
    kept pin or a kept iopin;
  - b/d/e: resistance on pin/iopin↔pin/iopin (kept nodes only);
  - f: no RC edges;
  - ground cap: the net node in b/c, broadcast to the net's kept pins in d/e, absent in f.

**Node order.** Type blocks in the view's order; within a block, Python `sorted` of the
DEF-written (escaped) names; pins by `(inst, pin)`. Both forms are checked per type block; homo
is also checked for the global block layout.

**Forms.** Homo (`<v>_graph_homo.pt`) and hetero (`<v>_graph.pt`) are each scored in full,
through one neutral adapter (`adapter_defgraph.load_views_v2`).
- Homo structure: x width 10 / y width 6; col 0 = node type; edge_y col 0 = edge_type; fwd/rev
  interleaving with identical paired rows; rc tensors present.
- Hetero structure: per-type x width 9, y and y_raw width 5; edge_y width 5;
  `graph_kind == "hetero"`.
- Both forms: reverse symmetry of every edge and RC edge.

**Excluded: power.** cell_power (gate x4, e-view gate edge_attr[2]) and IR-drop values are not
scored. The IR-drop slot must still be NaN on non-gate rows.

### v2.T Tolerances (frozen; `v2_defgraph_check.TOLS`)

Pass iff |a−b| ≤ atol + rtol·|b|; integers and categories must be exact; NaN must meet NaN.

| quantity | atol | rtol |
|---|---|---|
| coordinates, distances (µm) | 1e-4 | 1e-6 |
| hpwl_um | 6e-3 | 1e-6 |
| cell_area, sum_pin_cap_fF | 1e-6 | 1e-5 |
| congestion (raw and normalized) | 1e-6 | 1e-5 |
| path delay (ns) and its label | 1e-4 | 1e-6 |
| wirelength µm / label | 2e-4 / 1e-6 | 1e-6 |
| ground cap fF and label | 1e-5 | 1e-5 |
| coupling cap fF | 1e-5 | 1e-5 |
| equivalent resistance Ω | 1e-4 | 1e-5 |
| RC labels (log1p) | 1e-6 | 1e-5 |

These are the v1 / CONTRACT §9 values; the three RC rows are new.
- The RC raw values pass through def-graph's CSV at 6 decimals (fF, Ω) and then float32; the
  rtol covers float32 (6e-8) by more than 100×.
- **Images:** as v1 (`v1_images_dg.py`): rtol 1e-4 of the field max for features, 1e-6 for
  labels.
- **Four-stage (nangate45 only):** as v1 (`v1_4stage_check.py`).

**Rule for the proof run (3 R2G designs per platform).** Every failing group is classified
METHOD, DEFECT or SPEC-AMBIGUITY. A METHOD fix changes the checker and is recorded as a dated
amendment below. A DEFECT fix changes def-graph under the repo rules, with a regression test and
a failure-patterns.md entry. A tolerance is never widened to make a group pass.

### v2 amendments (dated; recorded while proving the checker on R2G)

- **A10 (2026-11-07, METHOD, four-stage lineage diagnostics only).** `v1_4stage_check.merged()`
  now also counts a route piece that carries a top port other than the net's own as a
  many-to-one merge. That is an IO feedthrough left after the backend removed the synthesis
  buffer between two ports, e.g. pilot500 C03894 `FrameStrobe[k]` → `FrameStrobe_O[k]`.
  - Before A10, these nets were listed as "masked but truth-resolvable": 113 on C03894, and
    the 136 of v1 on C00293/C02816.
  - The category is informational (`N_lineage_categories`, not scored), so no pass/fail changes.
- **A11 (2026-11-07, METHOD, checker code).** Three bugs in the checker code itself, all fixed
  before any non-R2G dataset was scored:
  - `v1_4stage_check` imported the v1 name `parse_spef_dg`, which had been removed; it now
    resolves to `parse_spef_v2`;
  - an index bug in the folded-row comparison;
  - the e-view gate-edge IR-drop slot was demanded NaN although power is not scored; it is
    now skipped.
