# Label Extraction (dataset building)

`scripts/flow/run_labels.sh <project-dir> [platform]` runs after a completed ORFS
backend and emits per-cell/per-net **regression-target** tables plus a per-design
statistics JSON. It is fail-soft: each of the four label sets is independent, and a
missing input or tool error records a per-label status without aborting the others.

## Outputs

Written to `design_cases/<design>/labels/` and `design_cases/<design>/reports/`:

| File | Rows | Columns | Label transform |
|------|------|---------|-----------------|
| `labels/cell_congestion.csv` | per placed instance | `Design,Cell,cell_type,cell_congestion,label,label_raw` | `label = mean_bbox(sqrt(gaussian_util))`; `label_raw = mean_bbox(sqrt(util))`; `cell_congestion = mean_bbox(gaussian_util)` (see "Congestion label method") |
| `labels/wirelength.csv` | per net | `Design,Net,NetType,WireLength_um,label,mask_wl` | `label = log1p(WireLength_um)`; `mask_wl = NetType==SIGNAL` |
| `labels/timing_features.csv` | per placed instance | `Design,Cell,Cell_Slack_ns,Path_Delay_ns,label,in_sta_path` | `label = log(1+Path_Delay_ns)`; `Path_Delay_ns = clk_period - worst_slack` (floored at 0) |
| `labels/ir_drop.csv` | per instance (fillers/tap/endcap filtered) | `Design,Cell,X,Y,Voltage_V,IR_Drop_mV,P95_mV,label,has_irdrop` | `label = log(1 + IR_Drop_mV/P95_mV)` |
| `reports/labels_stats.json` | — | per-label count + min/mean/p50/p90/p95/p99/max for `label` and the raw metric, plus mask/in_path/has_irdrop tallies | — |

`Design` + `Cell`/`Net` are the join keys across the four tables. Note that `timing`
and `congestion` are keyed on the full instance set while `irdrop` excludes
fillers/tapcells/endcaps (PDNSim instance filtering) — different label granularities
by design.

## Inputs & resolution

- **Design geometry:** the collected `backend/RUN_*/{final,results}/6_final.odb`
  (timing, IR drop) and `6_final.def` (congestion, wirelength). Falls back to the
  live ORFS results dir.
- **Platform liberty/lef/voltage:** `resolve_platform_paths.sh` (a thin shim over
  `scripts/extract/techlib/resolve.py`) asks the ORFS Makefile to expand `LIB_FILES`,
  `TECH_LEF`, `SC_LEF`, `ADDITIONAL_LIBS`, `PWR_NETS_VOLTAGES` for the design's
  `config.mk` (so asap7/gf180 corner-built variables resolve), with a platform-dir
  glob + per-platform voltage map as fallback. The per-platform voltage constants live
  in `techlib.profile` (`TechProfile.supply_voltage_str`); the congestion worker also
  consults `techlib.lef.routing_layer_info` for tech-LEF pitch/direction (with the
  nangate45 `DEFAULT_LAYER_INFO` as the fallback). Validated on all six ORFS platforms
  (nangate45, sky130hd/hs, asap7, gf180, ihp-sg13g2). `.lib.gz` liberty (asap7/gf180)
  is read directly by OpenROAD.
- **Clock period / port:** parsed from `constraints/constraint.sdc`
  (`set clk_period`, `set clk_port_name`); defaults to 10.0 / clock-name auto-detect.
  A wrong clock period biases `Path_Delay_ns` — keep the SDC accurate.

## Congestion label method

`extract_congestion.py` is a faithful port of `RTL2Graph/label_test/py/Congestion_Parse.py`'s
label calculation (the 2026-07-06 method update). Per GCell it computes routed-wire
demand vs. track capacity, `util = max(demand_h/cap_h, demand_v/cap_v)`, smooths the
dense utilization grid with a Gaussian, then maps each cell over its footprint:

1. **Capacity** (`calculate_grid_capacities`): from tech-LEF routing layers
   (`techlib.lef.routing_layer_info`) — `cap_h += grid_w·grid_h/pitch` per HORIZONTAL
   layer, symmetrically for VERTICAL. Unit-invariant (demand and capacity both scale
   with dbu, so `util` is dimensionless and matches the reference exactly).
2. **Demand** (`extract_grid_demand`): routed segments split at GCell boundaries into
   `demand_h`/`demand_v`, keyed `(x_gcell, y_gcell)` for **both** directions (the
   2026-07-05 transpose fix; the reference agrees). Uses `techlib.route_segments`
   (RECT-patch + chain aware) — a strict superset of the reference's 2-point handling.
3. **Gaussian** (`gaussian_filter_2d`): a **pure-Python reproduction of
   `scipy.ndimage.gaussian_filter(util, sigma=1.0)`** with scipy's defaults
   (`order=0, mode='reflect', truncate=4.0` ⇒ a separable 9-tap reflect-boundary
   correlation). Bit-matched to scipy to `<1e-12` (test
   `test_gaussian_filter_2d_matches_scipy_golden`), so the label stage keeps **no
   numpy/scipy runtime dependency**. The old manual 3×3 (radius-1) kernel is retired.
4. **Cell → GCell mapping** (`cell_bbox_dbu` + `cell_congestion_over_bbox`): each
   placed instance is averaged over **every GCell its orientation-aware bounding box
   overlaps** (needs cell `SIZE` from `SC_LEF`; N/S keep `(w,h)`, E/W rotate 90° ⇒
   swap), not just the origin GCell. With no cell LEF every cell falls back to its
   origin GCell (logged) so the stage still runs.

The reference returns a 2-vector `[sqrt(util), sqrt(gaussian_util)]`; the CSV carries
the smoothed component as the canonical `label` (unchanged for `graph_lib` y1 /
`compute_label_stats` / the RTL2Graph augmenters) and the raw component as `label_raw`.

**Verified** (2026-07-06) against the reference on sky130hd `DMA_top` (1680 non-filler
cells, 30×30 GCells): identical `util` ⇒ label diff `<1e-15`; full pipeline (skill
float64 CLI vs. reference float32) label diff `<1e-8`. The residual is the reference's
float32 storage vs. the skill's float64 — the skill is strictly more precise.

## Why timing & IR drop read liberty

Both the timing STA and PDNSim IR-drop analysis need cell timing/power models.
The OpenROAD scripts `read_db <odb>` then `read_liberty` over the resolved list.
Without liberty, PDNSim reports zero current (all `Voltage_V == supply`,
`has_irdrop=false`) — so liberty loading is mandatory, not optional. PDNSim also
requires the rail voltages (`set_pdnsim_net_voltage` for the power net = supply and
the ground net = 0), else it raises `PSM-0079`.

## Env knobs (override resolution)

| Var | Effect |
|-----|--------|
| `R2G_LIB_FILES` | space-separated liberty paths for timing/IR drop (overrides resolver) |
| `TECH_LEF` | tech LEF for congestion layer pitches (capacity) |
| `SC_LEF` / `ADDITIONAL_LEFS` / `CELL_LEFS` | cell/macro LEF(s) with per-`MACRO SIZE` — congestion cell→GCell bounding-box mapping; absent ⇒ origin-GCell fallback (logged) |
| `SUPPLY_VOLTAGE` | nominal VDD for the IR-drop delta + PDNSim rail voltage |
| `CLOCK_PERIOD` / `CLOCK_PORT` | timing clock (overrides SDC; empty `CLOCK_PORT` = auto-detect) |
| `ODB_FILE` / `DEF_FILE` | explicit input design |
| `LABEL_TIMEOUT` | per-label timeout seconds (default 2400) |

## Batch backfill

`tools/run_labels_batch.sh [N] [design ...]` runs `run_labels.sh` across many
completed designs with a concurrency cap (`N`, default 4 — OpenROAD STA/PDNSim are
memory-light vs. KLayout LVS). With no design args it auto-discovers designs that
have a collected `6_final.odb`. Per-design logs and a `labels_backfill.jsonl`
roll-up land under `design_cases/_batch/logs_labels_<tag>/`.

## Scope notes

- Per-design only — corpus-wide aggregation, knowledge-store ingest, and dashboard
  surfacing are intentionally not wired here.
- Typical/primary corner only (no multi-corner labels). The corner follows the ORFS
  platform default (`CORNER`), e.g. BC for asap7/gf180.
- Designs that never reached `6_final` are skipped (status recorded), not errored.
- **Timing labels need a detectable clock.** The clock is re-created after
  `read_db` from the SDC `clk_port_name`, falling back to a `clk`/`clock` port-name
  match. Designs whose top-level clock port has a non-conventional name (and whose
  SDC `clk_port_name` doesn't match an actual port) get all-`not_in_path` timing
  rows (`label=0`) — honestly recorded, not an error. Purely combinational designs
  also correctly produce zero in-path rows.
- **Only the clock is constrained** — `extract_timing.tcl` applies no
  `set_input_delay`/`set_output_delay` (the design SDC uses 20% of period for
  both). Pure I/O paths are therefore unconstrained: input→reg slacks are
  optimistic, and cells feeding only output ports get `in_sta_path=false`
  (`label=0`). Bounded on aes_core sky130hd: 4% of real logic cells;
  reg↔reg labels are unaffected. Larger for I/O-bound or combinational
  designs — a documented modeling choice (2026-07-05 audit), not a join bug.

## Downstream consumer

`scripts/flow/run_graphs.sh` (SKILL.md step 13d) joins these label CSVs with the
feature CSVs into training-ready PyG graphs — see `graph-dataset.md`.

## 2026-07-05 corrections (RTL2Graph integration audit)

Two label defects were fixed on this date; CSVs generated before it are wrong in
these spots (regenerate before training on them):

- `timing_features.csv`: EVERY register (bus-named cell) had `slack=INF,
  in_sta_path=false` — the STA-pin-name -> odb-component join missed on DEF
  name escaping. After the fix registers carry real slack (aes_core sky130hd:
  5/2476 -> 2476/2476 labeled).
- `wirelength.csv` + `cell_congestion.csv` on sky130*: DEF `RECT` patch groups
  were misread as route points, inflating RECT-bearing nets ~100-400x (1283/30k
  nets on aes_core) and congestion utilization past 11x. Fixed lengths are
  centerline (patch metal excluded), so RECT nets read ~0.2 um below OpenROAD's
  `report_wire_length`, which includes patches.

A second 2026-07-05 wave (sky130 verification round) fixed two more, ALL
platforms' pre-fix congestion CSVs affected (see failure-patterns.md
"Dataset-Extraction Silent-Value Defects" #6/#7):

- `cell_congestion.csv` (all platforms): VERTICAL routing demand was keyed
  transposed `(y_gcell, x_gcell)`, so every cell's vertical utilization was
  read from its diagonal-mirror gcell — 79.7% of aes_core congestion labels
  change (mean |Δ| 0.052, max 0.323 on a 0–0.44 scale).
- `ir_drop.csv`: an interrupted irdrop stage could leave PDNSim's RAW dump at
  the canonical path (silently unusable labels). Now published atomically;
  `labels_stats.json` reports `invalid` for unusable label CSVs; the graph
  stage records per-file `label_health` in its manifest.

Full defect table: failure-patterns.md "Dataset-Extraction Silent-Value Defects".
