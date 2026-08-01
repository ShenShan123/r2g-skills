# R2G2.0 vendored pipeline — provenance and deltas

This directory is a **vendored copy** of the manually-verified R2G2.0 four-stage
dataset builder. The main logic is upstream's and must stay that way: when a new
drop arrives, re-vendor the numbered scripts and re-apply the deltas below rather
than hand-merging our own reimplementation.

## Upstream drop

| Field | Value |
| --- | --- |
| Archive | `Dataset_R2G2.0(B).zip` |
| Ingested | 2026-08-01 |
| Reference sample | `bp_multi_top/v01` (nangate45), reported PASS on both upstream checks |
| Contract doc | `upstream_docs/B_VIEW_DATASET_STRUCTURE.md` (+ `_CN`), `upstream_docs/R2G2.0_README.md` |

SHA256 of the files as they arrived (verify before re-vendoring):

```
d08f97b08ce24715eaef3304d8565c9994a03e1f2e47543c523d89cc303e3247  01_build_base_graph.py
3243d850d7d4fa5ba0138ac83baffe16b727c5e82297f03396758b7d0f90a3e8  02_extract_features.py
a17292207e1e0a50a14d2eb14104bb12b9d86e0905e05d699348b8a487a21dee  03_extract_labels.py
89833662f1d68335798e00c6ac54361888a6849ee75222e313a70e1dcf2a6ff6  04_assemble_heterograph.py
859c1bc48fc778eeec9e514f8862ed0f6775de99824caa1ad1404bd5c2749a6d  05_build_stage_snapshots.py
0ca26dd59d310156d23db223436cf98bcbac74490eeb2bbf616a29cf7cfc038b  checks/summarize_four_stage_graph_data.py
9e8d9a6610553bccc3b0865fd519b7ce402d69e8b44ad0072b7976049d31aa14  checks/validate_four_stage.py
975fc54a05d74ab7dc7c38b73b7c339a221a1f091bf96b46def3015b9b2e0d91  configs/encode_map.csv
```

## Why there are deltas at all

Upstream was verified on **one sample, on one platform** — `bp_multi_top/v01` on
nangate45. Every delta below is a defect that is invisible on nangate45 and
fires on the platforms this skill actually defaults to (sky130hd/sky130hs), or a
packaging assumption that does not hold outside upstream's `/data/...` tree.

Each delta is marked in the source with a `r2g-skills delta vs upstream R2G2.0`
comment. Nangate45 behaviour is unchanged by all of them.

Total footprint, so a re-vendor can confirm nothing else drifted (`diff -u` changed
lines, excluding context):

| File | Changed lines |
| --- | ---: |
| `01_build_base_graph.py` | 95 |
| `02_extract_features.py` | 120 |
| `03_extract_labels.py` | 63 |
| `04_assemble_heterograph.py` | 68 |
| `checks/validate_four_stage.py` | 55 |
| `05_build_stage_snapshots.py` | 0 (byte-identical) |
| `checks/summarize_four_stage_graph_data.py` | 0 (byte-identical) |
| `configs/encode_map.csv` | 0 (byte-identical) |

401 lines out of 11,871 (3.4%) — the main logic is upstream's.

### D1 — `FN`/`FS` pin-orientation transforms were swapped (correctness, high)

`02_extract_features.transform_pin` mapped `FN → (x, h−y)` and `FS → (w−x, y)`.
LEF/DEF define `FN` = MY (mirror about the Y axis, changes x) and `FS` = MX
(mirror about the X axis, changes y) — i.e. the two entries were exchanged.
`FW`/`FE` in the same table are consistent with the *correct* `FN`/`FS`, which
is what makes the swap a transcription slip rather than a different convention.

Standard-cell rows alternate `N`/`FS`, so this misplaces the pins of roughly
half of all instances, and propagates into `pin_x_um`/`pin_y_um`, the
normalized/boundary distances, per-net HPWL, `congestion_pin_density` and both
RUDY features.

Measured against OpenDB's own placed pin locations (`aes_core`, sky130hd, 401
placed pins with a connected net):

| Formula | `N` | `FS` |
| --- | ---: | ---: |
| upstream | 211/211 | **0/190** |
| corrected | 211/211 | **190/190** |

The corrected table is byte-identical to this skill's independently validated
`scripts/extract/techlib/lef.py:apply_orient`, which carries the same fix (the
RTL2Graph original shipped the same swap; see `failure-patterns.md` #59).

### D2 — Liberty parsing is quote-intolerant (correctness, high)

`01.parse_liberty`, `02.parse_liberty` and `liberty_bus_members` matched
identifiers and values with quote-free regexes:

```python
re.match(r"cell\s*\(\s*([^)]+?)\s*\)\s*\{", line)     # cell ("name") -> '"name"'
re.match(r"direction\s*:\s*([A-Za-z_]+)\s*;", line)   # direction : "input" -> no match
re.match(r"clock\s*:\s*true", line, re.I)             # clock : "true"    -> no match
```

Nangate45 writes `cell (AND2_X1)` and `direction : input;`, so nothing shows.
sky130/gf180/ihp write `cell ("sky130_fd_sc_hd__a2111o_1")` and
`direction : "input";`, and then:

* every master name carries literal `"` quotes, so it never matches the DEF or
  Yosys spelling — every gate falls back to `cell_type_id = UNKNOWN`;
* every pin direction is empty — `pin_type_id`/`pin_role_id`/`is_driver_pin`/
  `is_sink_pin` collapse, `num_drivers`/`num_sinks` go to zero, and
  `project_gate_graph` emits no gate→gate edges at all;
* `cell_area_um2`, `cell_leakage_power`, `pin_cap_fF`, `max_transition`,
  `max_capacitance`, `v_nom` and the `clock` flag are all lost.

Measured on `sky130_fd_sc_hd__tt_025C_1v80.lib` after the fix: 428 masters parse
unquoted, `v_nom=1.8`, `cap_scale_ff=1000`, `DFXTP_1.is_ff=True` with `CLK`
flagged as a clock pin, and **0/1771 pins have an empty direction** (before the
fix: 1771/1771). This is the same defect family this skill already fixed in
`techlib/liberty.py` — see CLAUDE.md, "Quoted-unit liberty defects (sky130)".

### D3 — the "technology-derived" congestion grid is a nangate45 constant (correctness, medium)

`15 FastRoute tracks × 0.14 µm Metal3 pitch = 2.1 µm` was hardcoded in four
places (`02.FIXED_CONGESTION_GRID_UM`, `03.parse_def_header`,
`04.build_congestion_geom_edges`'s `width = 15.0 * 0.14`, and
`checks/validate_four_stage.py`, which asserted the literals `2.1` and
`4200` DBU — so a *correct* non-nangate45 build failed the contract check).
0.14 µm is nangate45's
Metal3 pitch; sky130hd's third routing layer pitch is 0.46 µm, so the correct
grid there is 6.9 µm — a 3.3× error in GCell area that shifts every congestion
input feature, the congestion label's grid indexing, and the `gate|congestion_geom|gate`
neighbourhood.

Now resolved by `resolve_congestion_grid_um(cfg)` from
`congestion_grid_um` / `congestion_grid_tracks` × `congestion_grid_pitch_um`,
defaulting to 2.1 µm so nangate45 is bit-for-bit unchanged.
`make_sample_config.py` fills the pitch from the platform tech LEF.

### D4 — SciPy imported at module scope blocked four unrelated labels (robustness)

`03_extract_labels.py` did `from scipy.sparse import ...` at the top, but SciPy
is used *only* by the IR-drop KCL solve. Without SciPy the whole label stage
refused to start, taking wirelength, congestion, timing and RC down with it —
even under `--skip-irdrop`. Now imported lazily inside `solve_vdd_network` via
`_require_scipy()`, with an actionable HINT. This matches the skill's contract
that a missing input degrades one column, never the stage.

### D5 — `nodes_iopin.csv` had no alignment check (honesty)

`04_assemble_heterograph.main` calls `require_same_keys` for `nodes_gate.csv`,
`nodes_net.csv` and `nodes_pin.csv`, but not for `nodes_iopin.csv` — and then
takes `io_keys = sorted(io_index)` straight from that CSV. The IO-pin node set
was therefore whatever the feature CSV said, not what the base graph said. Added
the symmetric `io_features` check against `base.io_pin_names`.

### D6 — `unmatched_gate_name_count` was a constant dressed as an alarm (observability)

`01.main` always calls `parse_synth_verilog_with_yosys(..., {}, {})` because
stage 01 must not read a DEF. With empty reference maps every gate lands in
`unmatched_gate_names`, so the reported count always equalled the total gate
count. Now gated on `name_reference_enabled` so "no reference supplied" and
"reference supplied but did not match" are distinguishable.

### D7 — the feature↔label grid check used exact float equality (latent upstream bug)

`04_assemble_heterograph` compared the feature-side and label-side congestion
grid specs with `label_grid_steps != {feature_grid_step}` — exact float set
equality. But the two sides compute the value differently: `02` records
`tracks * pitch` directly, while `03` round-trips it through integer DBU
(`round(grid * dbu) / dbu`). Those agree bit-for-bit only when `tracks * pitch`
happens to land exactly on a representable DBU multiple:

| Platform | `15 × pitch` | DBU round-trip | equal? |
| --- | --- | --- | --- |
| nangate45 (0.14) | `2.1` | `2.1` | ✅ |
| sky130hd (0.46) | `6.9` | `6.9` | ✅ |
| **sky130hs (0.48)** | `7.199999999999999` | `7.2` | ❌ |

Upstream never saw it because its one verified platform is in the lucky set —
this is the *pure* form of the single-platform-verification problem, since the
defect is in arithmetic, not in any technology-specific parsing. It surfaced
here the moment D3 made the grid follow the platform: a **correct** sky130hs
build aborted at stage 04 with "拥塞特征与标签的GCell规格不一致".

Now compared with `math.isclose(rel_tol=1e-9)` per axis, plus an explicit
"all label rows agree" check. A genuinely different grid (2.1 vs 7.2) and mixed
label grids are both still rejected, and `checks/validate_four_stage.py` still
re-checks the integer DBU step exactly.

### D8 — gzipped Liberty read as text, failing *open* (silent-value, whole-platform)

`01.parse_liberty` and `02.parse_liberty` used `path.read_text()`
unconditionally, and the hierarchy-Liberty discovery globbed `*.lib` only.

gf180 ships **only** compressed Liberty — 30 `.lib.gz`, 0 `.lib` — and ORFS
`LIB_FILES` points straight at one. Upstream therefore decoded gzip bytes as
UTF-8-with-replacement and parsed **zero cells, zero pin directions — and raised
nothing**. The existing `missing_liberty_cells` guard only fires for masters it
*found* but could not encode, so an unreadable Liberty passes it vacuously.

The resulting dataset is fully populated and completely wrong: every gate at
`cell_type_id=UNKNOWN`, `cell_area_um2`/`cell_leakage_power`/`pin_cap_fF` all 0,
every pin direction empty (so `is_driver_pin`/`is_sink_pin`/`num_drivers`/
`num_sinks` collapse), and `project_gate_graph` emitting **no gate→gate edges at
all**. Yosys itself reads the `.gz` fine, so only the Python side was blind.

Fixed three ways, because the parse fix alone would still fail open on the next
unreadable format:

1. `read_liberty_text()` decompresses `.gz` transparently (matching
   `techlib/liberty.py`, which already did);
2. `glob_liberty()` discovers `*.lib` **and** `*.lib.gz` (also in
   `stage_dataset/build_encode_map.py`);
3. **fail closed on an empty parse** — `01` raises when no masters were parsed,
   `02` raises when the cell table is empty, and `build_encode_map.py` refuses to
   emit a map whose only `cell_type_id` row is `UNKNOWN`.

Verified on `gf180mcu_fd_sc_mcu9t5v0__ff_n40C_5v50.lib.gz`: **229 masters,
`v_nom=5.5`, `cap_scale_ff=1000`, 0/848 pins with an empty direction** (before:
nothing parsed at all).

### D9 — stage 01 scanned the platform Liberty directory; stage 02 did not (asymmetry + wrong library)

`01.resolve_yosys_hierarchy_libs` fell back to scanning **the primary Liberty's
own directory** whenever `yosys_hierarchy_lib_dir` was unset. Stage 02 only ever
scans when that field *is* set. So for the same design, upstream's stages 01 and
02 could see different cell sets.

The scan is right only where a platform's `lib/` is "one library plus its
macros" — which is true on nangate45 (1 std-cell lib + 22 `fakeram*`) and false
everywhere else:

| Platform | `lib/` contents |
| --- | --- |
| nangate45 | 1 std-cell lib + 22 fakeram macro libs — scan is correct |
| sky130hd | std-cell lib + `sky130_dummy_io.lib` |
| sky130hs | the same library at **two temperature corners** |
| gf180 | **30 files = two different cell libraries (7-track and 9-track) × every PVT corner** |

Scanning mixes physical libraries and makes every electrical value (pin cap,
area, leakage) depend on which file the glob happened to parse last. On gf180 it
put 7-track cells into a 9-track design.

Found by the D8 fix: once `.lib.gz` was discoverable, the gf180 scan pulled in
all 30 files and **the fail-closed guard caught it** — stage 01 refused with
"encode_map missing 229 Liberty cells, e.g. `GF180MCU_FD_SC_MCU7T5V0__ADDF_1`"
rather than silently mapping 7T cells to `UNKNOWN`. That is the guard working as
intended.

The scan is now opt-in and symmetric with stage 02: set
`yosys_hierarchy_lib_dir` explicitly, or `yosys_hierarchy_lib_scan: true` to
restore upstream's implicit behaviour. `make_sample_config.py` sets neither,
because ORFS already resolves macro Liberty into `ADDITIONAL_LIBS`, which the
adapter puts into `lib`.

### D10 — LEF pin geometry read from `RECT` only, ignoring `POLYGON` (silent-value, whole-platform)

`parse_lef_geometry` matched only `RECT`. LEF equally allows `POLYGON`, and
gf180's standard cells use it for essentially every signal pin — **2135 POLYGON
vs 1294 RECT** in the 9-track SC LEF, with all four pins of `__inv_1` polygons.

With no geometry the pin silently falls back to the cell origin, so
`pin_position_valid` is 0 for **every** pin; and because `stage_net_geometry`
requires geometry on *every* endpoint before it will emit a bbox,
**every net's HPWL and bbox becomes NaN too**. Observed exactly that: gf180 came
out with 13 all-NaN columns at cts/route that no other platform had —
`hpwl_um`, `net_bbox_width/height_um`, all three `stage_segment_*_hpwl_um`,
`pin_x/y_normalized`, all four `pin.distance_to_die_*`, and
`nearest_tap_distance_um` (that last one is D11).

The tell was that `pin_x_um`/`pin_y_um` were *populated* while their normalized
and die-distance twins were not — the raw coordinate falls back to the origin,
but the derived fields are gated on `pin_position_valid`.

Now a `POLYGON`'s bbox is stored as one rectangle, so the existing centroid rule
applies unchanged to both shapes. Verified: gf180 pin geometry went 0 → **229/229
macros, 1294 pins**; nangate45 (135/135) and sky130hd (440/441) are unchanged.
Matches `techlib/lef.py`, which already read both.

### D11 — well-tap detection was the literal substring "TAP" (missing feature, whole-platform)

`nearest_tap_distance_um` collected tap cells with `"TAP" in master.upper()`.
gf180's well-tap/endcap masters are `gf180mcu_fd_sc_mcu9t5v0__filltie` and
`__endcap` — **324 placed instances in this design, none containing "TAP"** — so
the feature was NaN for every IO pin.

Now driven by a `tap_master_patterns` config field, which
`make_sample_config.py` fills from `techlib.profile.get_profile(platform)
.tap_patterns` — this skill's existing single source of truth for tap naming
(`_PLATFORM_TAP_EXTRA`, which already carried gf180's `FILLTIE`/`ENDCAP` for
exactly this reason). Defaults to upstream's `["TAP"]` when the field is absent.

## Known upstream residue (not changed here)

* `01.parse_def_logical`, `unique_reference_index`, `vector_reference_names` and
  the `def_pins` bus-recovery block are unreachable in the shipped `main()` for
  the same reason as D6. Left in place: they are the intended implementation if
  a future drop enables DEF-assisted name recovery.
* `TIMING_EDGE_TYPES` / `RC_RESISTANCE_EDGE_TYPES` in `04` include an
  `io_pin → io_pin` combination that the contract doc does not list. Harmless —
  the relation is only created when rows exist.
* `graph_id` is written into the feature CSVs as the design name but the tensor
  column is the integer passed to `matrix()` (0). Upstream documents it as a
  placeholder; batching multiple designs will need a real assignment.

## Environment notes for this machine

* **IR drop is unavailable**, not skipped by choice: R2G2.0 solves the VDD
  network from a PDNSim SPICE export (`VDD_extracted.sp`), and this OpenROAD
  build's `analyze_power_grid` offers only `-voltage_file` / `-error_file` /
  `-em_outfile`. `ir_drop_mV` is therefore NaN with `irdrop_valid=0`. Pass
  `--irdrop-sp` to `run_stage_dataset.sh` if a build that can export one appears.
* SciPy was installed into the `/proj` torch venv for the IR-drop path.
* Upstream's `report_checks -max_paths/-group_count` spelling was renamed to
  `-endpoint_path_count`/`-group_path_count`; `emit_timing_reports.py` probes
  `help report_checks` rather than hardcoding either.
