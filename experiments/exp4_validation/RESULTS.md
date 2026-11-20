# Experiment 4: dataset validation results (2026-11-07)

- **Protocol.** `PROTOCOL.md`, frozen before any result was seen. The dated amendments A1–A8 at its
  end fix checker method only; no tolerance was widened, and A6 tightens one.
- **Sample.** `sample.json`: 43 test designs (25 L / 5 S / 13 M), seed 20261107. `dev_designs.json`:
  6 development designs, 2 per stratum, seed 20261108, disjoint from the sample.
- **Raw outputs.** On `memlab203:/data/yangao/exp4_validation_20261107/`. The summary JSONs are
  copied into this folder.

## V1 failures: diagnosis, classification and fixes

Counts are failing designs out of 43, before and after the amended checker (`v1_before_after.json`).

| Check group | Failures before | Cause class | Fix | Failures after | Evidence |
|---|---|---|---|---|---|
| R.F3_pin_inside_shape, R.F2_pin_xy_bbox_centre (route stage) | 43 | **DEFECT** | Builder `transform_pin` swaps FN and FS (`builder_fix.diff`); fixed copies pass | 43 (kept) | `v1a/*.json`, `fixdemo/v1/`, OpenROAD odb check below |
| R.F2_net_hpwl_um_unsplit (route stage) | 43 | **DEFECT** (follows from the row above) | Same fix | 43 (kept) | `diag_hpwl_only_N_S_cells`: 100 % match on N/S-only nets |
| R.F1_net_pin_count / num_sinks / fanout (route stage) | 3 | 2 **DEFECT**; 1 was cascade from the name convention | Builder reads stage-DEF IO incidence for net features (`builder_fix.diff`, line 2252); fixed copies pass | 2 (kept: C02816, C00293) | `fixdemo/v1/`, DEF lines below |
| P./R. I2 net identity, T1 pin–net, T2 C/D/E/F, net features, u avg_fanout (C00180) | 1 | **SPEC-AMBIGUITY** (name convention) | Join under the builder's documented `canonical_name` (`/`→`.`); strict DEF identity reported as S1, collisions gated by S2 (0) | 0 | `v1a/C00180*.json` (S1/S2) |
| L1_group_wirelength | 4 | **METHOD** (our resolver did not anchor backend logic clones) | A3: compare clone inputs by owning group; skip backend buffers when finding a cluster's driver; iterate | 0 | `v1/` vs `v1a/` (`L1_group_wirelength_mismatch_diagnosis`) |
| L1_group_one_to_one, L1b_group_boundary_endpoints (C02816) | 2 / 1 | **METHOD** (our Verilog parser missed `[0:0]` buses connected bare) | A4 | 0 | `1_2_yosys.v`: `input [0:0] i_register_ready;` … `.A(i_register_ready)` |
| L1_group_names_map_to_synthesis (C00180) | 1 | SPEC-AMBIGUITY (same name convention) | A5 | 0 | |
| P./R. F5 per-pin / per-IO clock period (pilot) | 3 pilot designs | **METHOD** (our assumption that every pin carries the period) | A2: value ∈ {0, SDC period} | 0 | |
| Image X_power_density | 30 (frozen tolerance) | **DEFECT**, plus a tolerance flaw | Builder joins STA power to odb names with different escaping (`image_builder_fix.diff`); tolerance floor made relative (A6, *tighter*) | **39** (correctly counted) | `img_v1a/`, `fixdemo_img/v1/` |
| Image X_pin_density (pilot) | 1 pilot design | METHOD | A7: exclude Liberty clock pins (OpenDB sig type) | 0 | |

After the fixes, every remaining V1 failure is a real dataset defect:

- 0/43 graph designs pass all groups, all because of the pin-orientation defect.
- 4/43 image designs pass all groups. These are exactly the designs with no escaped-name instances.
- Fixed-builder copies pass **every** V1 group: 3/3 graph designs (172 groups each: C03387, C02816,
  C00293) and 3/3 image designs (C00627, C01000, C00734).

## Real defects (findings), quantified

### D-A1. Dataset A: route-stage pin positions mirrored for FN/FS cells

- **Spec.** GEOMETRY_TIMING_AND_INPUTS_REFERENCE.md §1 says existing instances use "该阶段真实
  master查Liberty/LEF". The builder docstring of `pin_position_features` defines the value as computed
  "由当前阶段组件位置和LEF Pin几何" (from the stage component position and the LEF pin geometry).
- **Code.** `02_extract_features.transform_pin` has `"FN": (x, height − y)` and
  `"FS": (width − x, y)`. DEF/OpenROAD define FS = MX (mirror about the x-axis) and FN = MY.
- **Raw evidence (C03387).**
  - `4_cts.def` line: `- _0784_ INV_X1 + PLACED ( 139460 128800 ) FS ;`
  - LEF: INV_X1 pin A `RECT 0.06 0.525 0.165 0.7`.
  - OpenROAD odb ITerm bbox: x ∈ [69.79, 69.895] µm.
  - Dataset `pin_x_um` = **69.9975**, outside the shape. Our value is 69.8425.
- **Extent (43/43 designs).**
  - 626,672 of 1,495,893 route-stage pins (41.9 %) lie outside their own pin shape, by up to 3.67 µm.
  - `hpwl_um` is wrong on 290,599 of 405,586 unsplit nets (max 6.55 µm). It is correct on 100 % of
    nets whose cells are all N/S.
- **Columns affected** (from the fixed rebuild of C00293):
  - pin: `pin_x/y_um`, normalised coordinates and `distance_to_die_*` (19,084 of 41,971 pins);
  - net: `hpwl_um` and its stage-segment variants, bbox w/h (10,079 of 15,647 nets);
  - gate: `congestion_rudy_pin` (11,885 of 12,854 gates, max 829), `congestion_rudy`,
    `congestion_net_density`, `congestion_pin_density`.
- **Unaffected:** placement-stage graphs (byte-identical after the fix) and all labels.

### D-A2. Dataset A: route-stage net terminal counts on IO feedthroughs

- **Spec.** Same doc §1: "canonical Gate/Pin/Net/IO及信号incidence来自真实floorplan … CTS新增实体不改变
  共同规范拓扑".
- **Raw evidence (C02816).**
  - Floorplan: `- i_csrbus_address[0] ( PIN i_csrbus_address[0] ) ( _337_ A )`.
  - CTS: `- i_csrbus_address[0] ( PIN o_register_address[0] ) ( PIN i_csrbus_address[0] )`.
  - The dataset's route-stage `pin_count` is **3**: canonical 2, CTS physical 2. The neighbouring
    canonical net loses its port.
- **Cause.** `02_extract_features` takes the IO port's net from the stage DEF before the canonical
  map. `build_pilot500.canonicalize_io` repaired the edges but not these features.
- **Extent.**
  - In the sample: 80 nets in C02816 and 192 nets in C00293, each wrong by ±1 in `pin_count`,
    `num_sinks` and `fanout`; timing levels of the affected pins and IOs also change.
  - Across all 500 designs, 12 have port-feedthrough nets at CTS (430 such nets).
  - Placement stage correct.

### D-B1. Dataset B: `power_density` loses every instance with an escaped name

- **Spec.** `rasterize.py` defines the channel as "placement-stage instance power".
- **Cause.** `dump_design.py` keys OpenSTA power by `get_full_name` (`count[1]$_SDFF_PN0_`) and looks
  it up by the odb name (`count\[1\]$_SDFF_PN0_`), so every such instance gets 0 W.
- **Extent.**
  - 39/43 sample designs are affected; the 4 unaffected designs have no escaped names.
  - Power missing per design: median 21 %, max 95 %.
  - The stored channel equals our raster with those instances removed in 43/43 designs
    (max error 5.5e-13).
- **Fix.** Fixed-dump copies pass (3/3).

### SPEC-AMBIGUITY S-A1. Dataset A: entity names rewritten from DEF `/` to `.`

- The docs say identities come from the floorplan DEF but are silent about spelling.
- The builder documents `canonical_name()` ("基础图统一保存Yosys风格的`.`").
- Under the strict DEF reading, 274 nets in 3/500 designs (C00058, C00180, C00350) do not name-join
  with DEF, SPEF or Verilog. Under the builder's documented reading they are correct, with 0
  collisions.
- Recommendation: state the convention in the docs, or keep raw names.

### Not defects but must be stated in the paper

1. These tight-clock layouts were **not DRC/LVS-checked**: `result.json` says
   `"signoff": "not run (DRC/LVS not checked for this re-run)"`. Only the earlier pilot runs of the
   same designs were signed off.
2. The image label `cong` reproduces the stored odb GCellGrid usage/capacity exactly. However:
   - that usage is 5.9× the `[GRT-0096]` routing demand;
   - routing-free metal9/10 read about 42 % used;
   - capacity is the un-derated value.
   So `cong` includes blockage/PDN occupancy and is not routing congestion alone.

**Proposed rebuild (the user's decision; the release was not touched):**
- apply `builder_fix.diff` and rebuild the route stage of dataset A (all 500 designs; placement
  graphs and labels are unchanged);
- apply `image_builder_fix.diff` and rebuild dataset B's power channel.
The patched copies sit at `203:…/v2/project_fixed`, `…/img_builder_fixed`.

## V1: equivalence to EDA ground truth (amended checker, 43 designs)

Entities:
- 451,595 gates, 523,068 nets, 1,495,893 pins, 5,970 IO pins;
- 1,137,713 cell arcs;
- 752,024 congestion bins;
- 460,875 WL groups compared.

**Pass in 43/43 designs (exact, or within the frozen tolerances):**
- identity: gate / net / pin / IO;
- B and C–F topology;
- categorical gate and pin features; Liberty pin cap, area, leakage (max rel ≤ 1e-6);
- SDC delays and periods;
- gate and IO coordinates (≤ 1.5e-5 µm);
- placement-stage geometry: absent by design, because floorplan cells are unplaced. The
  "placement" graph is a floorplan netlist graph;
- WL groups (max 3.6e-12 µm); 92.9 % of 496,059 valid groups are resolvable by our independent
  reconstruction (min per design 83.6 %);
- group endpoints;
- DEF total vs DR report (≤ 6.5e-5 rel);
- gate congestion / overflow, mask and grid id, the full spatial field (≤ 6.7e-16), GCell percentage;
- C/D/E/F label carriers;
- TNS / signed WNS vs `6_report.json` (exact; NaN/mask on the 2 unconstrained designs);
- cross-stage label equality.

**Independent OpenSTA rerun** (5_route.def + 6_final.spef): 41/41 constrained designs agree, with
|ΔWNS| ≤ 0.15 ps and |ΔTNS| ≤ 0.083 ns (always < 1 %).

**Failing (real defects):**
- route-stage pin position and HPWL: 43/43 (D-A1);
- route-stage net counts: 2/43 (D-A2).

**Images:** grid, `cong` label, and cell / pin / RUDY / pin-RUDY / macro channels pass in 43/43;
power density fails in 39/43 (D-B1).

## V2: bit-identical rebuild

- **Graphs, 10 designs (100 graphs).** Shipped `reproduce/` sources, run as
  `build_pilot500.worker` → `convert_pilot500_views.worker` → `upgrade_release.worker` in a fresh
  root.
  - With a reconstructed frozen runtime (Py 3.9.23 · torch 2.5.1 · PyG 2.6.1 · numpy 2.0.2 ·
    scipy 1.13.1; the original `/data1` py39 env is unreadable to us): **100/100 byte-identical** to
    `dataset_manifest.json`.
  - With Py 3.13 / torch 2.13: 0/100 byte-identical. Inputs are identical in 100/100; only
    `wl_group_y` differs, by ≤ 1.8e-12 µm (consistent with Python ≥ 3.12 compensated `sum()`), and
    torch adds container members.
- **Images, 10 designs.** Shipped `dump_design.py` / `ir_design.tcl` / `rasterize.py`:
  **10/10 npz byte-identical**, including IR drop.
- **Deviations.** `r2g5.RAW` and `label_core.OLD` re-pointed (the latter to the shipped
  `runtime_dependencies` copy).
- **Limitation.** The input-feature tarball has no hash manifest of its own.

## V3: no route-stage leakage

**Dynamic reads (strace).**
- The placement feature stage opens only `2_floorplan.def` and `config.mk` from the design directory.
- The route feature stage opens only `4_cts.def` and `config.mk`.
- Route files are opened only by the label code.
- Static allowed reads are listed in `v3_summary.json`.

**Graph counterfactuals.**
- Perturbed, internally consistent own route bundle: accepted 3/3. Inputs identical in 30/30
  graphs. Labels changed: GCell % 1.0→4.8, 1.3→5.8 and 1.9→10.2; WNS/TNS shifted; 99 % of gate
  labels and 20–27 % of WL groups changed.
- Inconsistent bundle (DR json ≠ DEF): refused 3/3 (guard).
- Donor bundle: refused 2/3, but only incidentally; **accepted 1/3** (C03387 ← C03319), emitting
  the donor's TNS/WNS/GCell % as valid labels. This is a guard gap: there is no same-design check.

**Image counterfactuals.**
- Re-globally-routed `5_1_grt.odb`: X byte-identical 3/3; `cong` changed in 96–99 % of gcells and
  IR in 64–83 %.
- Donor odb: X grid changes (3/3). The grid geometry is read from a route-stage file, and the builder
  does not refuse a donor odb.

**Verdict:** no route information enters inputs under any accepted counterfactual. There are two
guard gaps (above).

## V4: negative controls

**(a) Release copies, 5 designs.** The clean copies already fail V1, and only in the real-defect
groups, so detection is measured against those clean copies. Same counting as V4 (b): pass on the
clean copy, fail on the corrupted copy.

- V1 graph: **66/66 effective injections detected**, 100 % for all 14 defect types.
- V1 image: 25/30. DI5 (power ×1000) is masked by D-B1, but the count of matched pixels falls in
  5/5 corrupted copies.
- Dataset own content checks (O2–O5): 15/66; D04, D07 and D08 are caught by `validate_graph`.
- Dataset manifest sha: catches every byte change, by construction.

**(b) Fixed-builder copies as clean baseline** (`v4_fixed_baseline_summary.json`):
- clean copies failing: **0/3 graph, 0/3 image** (no false alarms);
- detection: **41/41** graph injections (D10 is a no-op on 1 design) and **18/18** image injections.

## Limitations

- IR drop is checked only via the V2 rebuild; there is no independent PDNSim run.
- SPEF ground/coupling caps are parsed into `gt/` for the later def-graph study, but dataset A has
  no SPEF label to compare against.
- Dataset A's gate `congestion_*` input columns and other categorical ids were not checked directly.
- Checks that rely on definitions read from the builder code ("definition-from-code"):
  - the D/E/F pairing rule;
  - the pin point (LEF pin-shape bbox centre; odb average of shape centres for images);
  - the clock-pin exclusion;
  - `fanout = num_sinks`.
- V3 used the Py 3.13 runtime, comparing against the baseline built with the same runtime.
