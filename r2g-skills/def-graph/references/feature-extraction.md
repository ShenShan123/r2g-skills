# Feature Extraction (dataset building)

`scripts/flow/run_features.sh <project-dir> [platform]` runs after a completed ORFS
backend and emits per-node / per-edge / graph-level **feature** tables (the ML **X** side)
plus a per-design statistics JSON. It is the complement of the label stage
([`label-extraction.md`](label-extraction.md), the **Y** side): both read the **same**
`6_final.def`, so feature rows join label rows on `graph_id` + `inst_name`/`net_name`. It
is fail-soft — each of the eight workers is independent, and a missing input or worker
error records a per-feature status without aborting the others.

## Outputs

Written to `design_cases/<design>/features/` and `design_cases/<design>/reports/`:

| File | Rows | Columns |
|------|------|---------|
| `features/metadata.csv` | per design (1 row) | `graph_id,num_cells,num_nets,num_ios,avg_fanout,die_width,die_height,core_area,dbu_unit,PLACE_DENSITY,CORE_UTILIZATION,ABC_AREA,C_total,tracks_per_layer,V_nom,freq_Hz,tracks_detail` — `tracks_per_layer` is the numeric MEAN per-layer track count (2026-07-06 fix: the old pipe-joined string coerced `global_feat[12]` to 0 on every platform); the per-layer detail string moved to `tracks_detail` |
| `features/nodes_gate.csv` | per placed instance | `graph_id,inst_name,master,cell_type_id,cell_area,cell_power,x_um,y_um,orientation,orientation_id,placement_status,placement_status_id` |
| `features/nodes_net.csv` | per net | `graph_id,net_name,net_type_id,fanout,pin_count,num_drivers,num_sinks,connects_macro_flag,num_layer,hpwl_um` |
| `features/nodes_iopin.csv` | per top-level I/O pin | `graph_id,iopin_name,net_name,pin_x_um,pin_y_um,pin_owner_master,pin_name,pin_layer_hint,nearest_tap_distance_um,pin_direction,pin_direction_id,net_use,net_type_id` |
| `features/nodes_pin.csv` | per pin (I/O + instance) | `graph_id,inst_name,pin_name,pin_type_id,sum_pin_cap_fF,pin_x_std_um,pin_y_std_um` |
| `features/edges_gate_pin.csv` | per (gate → pin) | `graph_id,inst_name,pin_name,cell_type_id,pin_type_id` |
| `features/edges_pin_net.csv` | per (pin → net) | `graph_id,inst_name,pin_name,pin_type_id,net_name,net_type_id` |
| `features/edges_iopin_net.csv` | per (I/O pin → net) | `graph_id,iopin_name,net_name,net_type_id,pin_direction_id` |
| `reports/features_stats.json` | — | per-CSV row count + status + numeric summaries (min/mean/p50/p90/p95/p99/max) of key columns; `design`, `platform`, `spef_present` |

`graph_id` (= the design name) joins to the label CSVs' `Design`; `inst_name` / `net_name`
/ `iopin_name` join nodes↔edges and join to the labels' `Cell` / `Net`. Validated on
`aes_core`: `nodes_gate` rows == DEF `COMPONENTS`, `nodes_net` == `NETS`, `nodes_iopin` ==
`PINS`, and 100% of feature cells/nets join the label rows.

`cell_type_id` and the `*_type_id` columns are **categorical**: their integer values are
stable + distinct within a platform but are **not comparable across platforms** (the
per-platform vocabulary differs — see below). Filter datasets by `platform`.

`num_drivers`/`num_sinks` are the **true** direction-parsed counts — no force-fill (2026-07-14):
a net whose driver liberty direction can't be resolved honestly reads `num_drivers=0` rather than a
fabricated `1` (which also used to corrupt `num_sinks`). `hpwl_um` and `pin_x/y_std_um` use each
pin's **true orientation-aware in-cell LEF position** (`techlib.lef.macro_pin_geometry` +
`apply_orient`) when a cell LEF resolves (`SC_LEF`/`ADDITIONAL_LEFS`, exported by `run_features.sh`);
absent a cell LEF they fall back to the instance origin.

## Inputs & resolution

- **Design geometry:** the collected `backend/RUN_*/{final,results}/6_final.def` (newest
  run), falling back to the live ORFS results dir. This is the same artifact the label
  stage uses — *not* the route-stage `5_route.def` the original scratch scripts used
  (which is not collected on disk). Override with `R2G_DEF` to feed an exported
  `5_route.def` if route-stage geometry is required (the bare ORFS `DEF_FILE` variable is
  intentionally **not** honored as an override — it is commonly exported in ORFS shells and
  would silently pin every batch design to one DEF). DEF parsing is handled by
  `scripts/extract/techlib/def_parse.py` (the single shared DEF/SDC parser).
- **Parasitics (optional):** `backend/RUN_*/rcx/6_final.spef` (fallbacks: `…/results/`,
  `<project-dir>/rcx/`). SPEF feeds `C_total` (metadata) and the I/O contribution to
  `sum_pin_cap_fF` (nodes_pin). When absent (no RCX), those degrade to 0 and the stats
  JSON records `spef_present:false` — non-fatal.
- **Platform liberty/tech-lef:** `resolve_platform_paths.sh` (a thin shim over
  `scripts/extract/techlib/resolve.py`) asks the ORFS Makefile to expand
  `LIB_FILES`/`ADDITIONAL_LIBS`/`TECH_LEF` for the design's `config.mk` (so asap7/gf180
  corner-built variables resolve), with a platform-dir glob fallback. Liberty is parsed by
  `techlib.liberty`; `.lib.gz` (asap7/gf180) is decompressed transparently. Routing-layer
  names and the `\b(<layer>|...)\b` matcher are provided by `techlib.lef`.
- **Clock ports:** parsed from `constraints/constraint.sdc` (`create_clock ... [get_ports]`,
  resolving `$var`) to set `net_type_id`/`pin_type_id` clock classification.

## Why pin/net typing reads liberty

`cell_area`, `cell_power`, `sum_pin_cap_fF`, and the pin direction that drives
`num_drivers`/`num_sinks`, `pin_type_id`, and `pin_direction_id` all come from the cell
liberty models. Without liberty (`R2G_LIB_FILES` empty / unresolved) those fall to 0 and
pin typing degrades — the loader emits an explicit `WARN: no liberty file found` rather
than silently zeroing.

## Platform handling (fully parameterized)

Per-platform constants (supply voltage, tap patterns, cell-type strategy, fallback routing
layers) are stored in `scripts/extract/techlib/profile.py` as a `TechProfile` per ORFS
platform, retrieved via `techlib.profile.get_profile(name)`. The workers import the
consolidated `techlib.*` modules instead of maintaining per-extractor copies.

- **cell_type_id** — provided by `techlib.cell_types`. EVERY platform (nangate45
  included since 2026-07-06) gets a deterministic map built from the platform's
  **standard-cell** liberty only (`R2G_SC_LIB_FILES` = `LIB_FILES` minus
  `ADDITIONAL_LIBS`, sorted → 0..N-1, `UNKNOWN` = N) so the ids are stable across every
  design of that platform; per-design macro cells (`ADDITIONAL_LIBS`) share the
  dedicated `MACRO` id (= N+1) rather than reshuffling the std-cell ids or aliasing
  onto `UNKNOWN`. nangate45's former curated map
  (`techlib.cell_types.NANGATE45_CELL_TYPE_MAPPING`) was retired 2026-07-06 after it
  drifted 22 masters behind the deployed liberty (every SDFF*/CLKGATE*/TLAT →
  UNKNOWN=95; failure-patterns.md #12) and the leftover import shim was deleted
  2026-07-09 — nangate45 datasets built against the curated map must be regenerated.
- **num_layer** — distinct routing layers a net traverses, derived from the tech LEF's
  `TYPE ROUTING` layer names via `techlib.lef.routing_layers` / `routing_layer_regex`
  (nangate `metal1..10`, sky130 `li1`/`met1..5`, asap7 `M1..9`); falls back to `metal\d+`
  if no tech LEF.
- **tap detection** — `nearest_tap_distance_um` keys on a `"TAP"` substring (matches
  Nangate/sky130/asap7 tap cells), plus per-platform extras from `TechProfile.tap_patterns`
  for platforms whose well-tap masters lack "TAP" (gf180 `FILLTIE`/`ENDCAP`); extend via
  `R2G_TAP_PATTERNS`. (ihp-sg13g2 places no tap cells, so the column is legitimately 0 there.)

## Env knobs (override resolution)

| Var | Effect |
|-----|--------|
| `R2G_DEF` | explicit input DEF (default: collected `6_final.def`; bare `DEF_FILE` is not honored) |
| `R2G_LIB_FILES` | space-separated liberty paths incl. macros (overrides resolver; feeds area/power/cap) |
| `R2G_SC_LIB_FILES` | standard-cell liberty only (defaults to `LIB_FILES`; builds the cell-type map) |
| `R2G_TECH_LEF` | tech LEF for routing-layer naming |
| `R2G_SPEF` | explicit SPEF (default: collected `6_final.spef`; empty = skip cap) |
| `R2G_SDC` / `R2G_CONFIG` | explicit SDC / config.mk (default: `constraints/`) |
| `R2G_PLATFORM` | platform name for cell-type-map selection |
| `R2G_TAP_PATTERNS` | comma-separated extra tap-cell name substrings |
| `FEATURE_TIMEOUT` | per-worker timeout seconds (default 2400) |

Each worker is also independently runnable: `python3 <worker>.py <DEF> <out_csv> <graph_id>`
with the `R2G_*` env above.

## Batch backfill

`tools/run_features_batch.sh [N] [design ...]` runs `run_features.sh` across many
completed designs with a concurrency cap (`N`, default 4 — the workers are pure-Python,
memory-light). With no design args it auto-discovers designs that have a collected
`6_final.def`. Per-design logs and a `features_backfill.jsonl` roll-up land under
`design_cases/_batch/logs_features_<tag>/`.

## Scope notes

- Per-design only — corpus-wide aggregation, knowledge-store ingest, and dashboard
  surfacing are intentionally not wired here (matching the label stage).
- Liberty follows the design's **configured ORFS corner** (`CORNER`, e.g. BC/fast on
  asap7/gf180), matching the label stage's resolver. Area is corner-invariant; pin
  capacitance differs ~2–5% by corner, so mixing fallback-resolved (TT) and make-eval (FF)
  designs in one dataset introduces minor cap inconsistency — filter/normalize per corner
  if it matters.
- **Cell-origin approximation:** pin geometry is not parsed from the LEF, so all pins on an
  instance inherit the instance origin. `hpwl_um` and `pin_x_std_um`/`pin_y_std_um` are
  therefore cell-origin approximations, not true pin-location metrics.
- Hand-rolled regex parsers tuned to ORFS `write_def`/`write_spef` output. The stats JSON
  carries row counts; a quick sanity check is `nodes_gate` rows ≈ DEF `COMPONENTS`.
- Designs that never reached `6_final` are skipped (status recorded), not errored.

## Downstream consumer

`scripts/flow/run_graphs.sh` (SKILL.md step 13d) joins these feature CSVs with the
label CSVs into training-ready PyG graphs — see `graph-dataset.md`.

## 2026-07-05 semantics corrections (RTL2Graph integration audit)

Three feature values changed meaning on this date (commit `fix(skill): feature
extractors — PIN-direction inversion, macro flag, load-only pin caps`); CSVs
generated before it carry the OLD, wrong semantics:

- `num_drivers`/`num_sinks`: DEF PIN direction is now interpreted from the
  chip's perspective (an INPUT port drives its net; an OUTPUT port sinks it).
  Previously every output-port net counted 2 drivers / 0 sinks.
- `connects_macro_flag`: now real (1 when the net touches a master that only
  exists in the per-design macro libs, e.g. fakeram45_*). Previously always 0.
- `sum_pin_cap_fF`: now the sum of INPUT-pin load caps only. Previously the
  driver's liberty `max_capacitance` (a drive limit ~20x the loads) was added
  in, dominating the value.

A second 2026-07-05 wave (sky130 verification round) fixed three more — sky130
CSVs from between the two waves are STILL wrong on these (failure-patterns.md
#5/#8/#9):

- `pin_type_id` + liberty-side `num_drivers`/`num_sinks` (sky130 only): quoted
  `direction : "input";` / `clock : "true";` never parsed — 95% of pins
  collapsed to the catch-all id 14 and every net took the assume-1-driver
  fallback. Now quote-tolerant.
- `sum_pin_cap_fF` (sky130 only): quoted `capacitive_load_unit(1.0, "pf")`
  left the pf→fF scale at 1.0 — every sky130 pin cap was 1000× too small.
- net `use` (all platforms): `+ USE` on the net's dash line was dropped, so
  `use` was populated only for line-wrapped nets (1,666/30,345 on aes_core;
  now 30,345/30,345). `net_type_id` was mostly saved by name-token fallback.

Full defect table: failure-patterns.md "Dataset-Extraction Silent-Value Defects".
