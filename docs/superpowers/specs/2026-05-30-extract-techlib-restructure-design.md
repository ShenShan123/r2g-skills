# Extract `techlib` Restructure — Design Spec

**Date:** 2026-05-30
**Status:** Approved (design)
**Branch:** `feat/label-extraction-stage`

## Goal

Refactor and re-structure `r2g-rtl2gds/scripts/extract/` so the label and feature
extractors share **one technology layer** (`techlib`) that handles every per-platform
concern automatically (tap cells, supply voltage, cell names, routing layers, liberty
corner/units), instead of the current scattered, partly-duplicated per-platform logic.

**Hard constraints (from the user):**
1. **ORFS platforms only** — `nangate45`, `sky130hd`, `sky130hs`, `asap7`, `gf180`,
   `ihp-sg13g2`. No generic/foundry-PDK abstraction, no auto-detect.
2. **Output CSVs byte-for-byte identical** to today on every platform. This is a *pure*
   behavior-neutral refactor — no schema, model, or value changes.
3. **Automatic per-technology handling** — the consolidated code resolves tap cells,
   supply voltage, cell-name vocabularies, and routing layers from the platform name /
   resolved PDK files, with no nangate assumption leaking into another platform's output.

## Why this is safe: current behavior is already tech-correct (verified 2026-05-30)

Empirical cross-platform check confirmed the *current* extractors already produce correct,
non-misleading per-platform output — so the refactor only has to **preserve** it:

| Platform | What was tested | Result |
|----------|-----------------|--------|
| nangate45 (`aes_core`) | full 8 features + 4 labels | 1.1 V, metal1–10, curated cell-type map, taps, area/power non-zero |
| sky130hd (`cordic`) | full 8 features + 4 labels | 1.8 V, dbu 1000, li1/met layers, **runtime** cell-type map (98 sky130 masters), taps, area 100 % non-zero; congestion 6508 / wirelength 1454 / timing 6508 / irdrop 1870 rows |
| asap7 / gf180 / ihp-sg13g2 | tech resolution on real PDK files | supply 0.70 / 5.00 / 1.20 V; `.lib.gz` liberty parses; M1–M9 / Metal1–5(+TopMetal) routing layers parse; TAPCELL / `__filltie` tap patterns match |

This covers **both** cell-type strategies (curated nangate45 map + liberty-runtime map for
the rest), both layer-naming schemes, all six supply voltages, and gzip liberty.

## The technology-dependent concerns to centralize (ported verbatim — no value change)

| Aspect | Current location(s) | Moves to |
|--------|---------------------|----------|
| supply / nominal voltage | `resolve_platform_paths.sh` case-map + `PWR_NETS_VOLTAGES` | `techlib.profile` (+ `techlib.resolve`) |
| tap / well-tap / endcap cell names | `features/lib_db._PLATFORM_TAP_EXTRA` + `"TAP"` | `techlib.profile.tap_patterns` |
| cell-name → `cell_type_id` | `features/cell_type_map` (curated / std-cell-runtime) | `techlib.cell_types` (profile selects strategy) |
| routing layers (names, pitch, direction) | `labels/extract_congestion.parse_tech_lef` **and** `features/def_parse.routing_layer_regex` (duplicated) | `techlib.lef` (one parser, two views) + per-platform `fallback_routing_layers` in profile |
| liberty parse + classifiers + units | `features/lib_db` | `techlib.liberty` |
| liberty/lef PATH + corner resolution | `resolve_platform_paths.sh` (ORFS make-eval) | `techlib.resolve` (shell becomes a shim) |
| power/ground net/pin names | `lib_db` global sets | `techlib.liberty` shared default (universal supersets — not per-platform) |

## Architecture

```
scripts/extract/techlib/            # NEW — single source of truth (Python package)
  __init__.py
  profile.py        # TechProfile per ORFS platform + get_profile(name); concrete, ORFS-only
  resolve.py        # ORFS make-eval liberty/lef paths + corner + voltage  [Python API + KEY=VALUE CLI]
  def_parse.py      # units, design, components, nets, route-segment iterator (the ONE DEF parser)
  lef.py            # routing layers from tech LEF: routing_layers() names + routing_layer_info() pitch/dir
  liberty.py        # liberty(.lib/.lib.gz) parse + cell/pin/net classifiers (was features/lib_db.py)
  cell_types.py     # cell_type_id: nangate curated + std-cell-runtime (was features/cell_type_map.py)
scripts/extract/labels/    extract_{congestion,wirelength}.py, extract_{timing,irdrop}.tcl, compute_label_stats.py  → import techlib
scripts/extract/features/  case_paths.py, metadata/nodes_*/edges_*.py, compute_feature_stats.py                    → import techlib
scripts/flow/resolve_platform_paths.sh  → thin shim → `python3 -m techlib.resolve`  (same KEY=VALUE contract)
```
Untouched: top-level `extract_{ppa,drc,lvs,rcx,progress}.py` (report parsers, not cell/tech data).

### Single source of truth via the shell shim
`techlib.resolve` ports the ORFS `make --eval` path resolution + the per-platform voltage
map out of `resolve_platform_paths.sh`. The shell script becomes a 3-line wrapper emitting
the **same `KEY=VALUE`** lines, so `run_labels.sh`, `run_features.sh`, and the `.tcl`
workers are unaffected; every tech constant now lives in `techlib` exactly once.

### Dedup (the value)
- **3 DEF parsers → 1** (`techlib.def_parse`): congestion's `parse_def_header_and_components`,
  wirelength's `parse_def_wirelength`, and the features parsers collapse onto shared
  `parse_units / parse_design_name / parse_components / parse_nets` + a shared route-segment
  iterator (the `*`-relative coordinate-chain logic congestion-demand and wirelength-Manhattan
  both reimplement).
- **2 tech-LEF parsers → 1** (`techlib.lef`): congestion's pitch/direction parser and
  `def_parse.routing_layer_regex` become `routing_layer_info()` + `routing_layers()`.
- liberty + classifiers → `techlib.liberty`; cell-types → `techlib.cell_types`.
- The `.tcl` workers keep using OpenROAD `read_liberty` but take **all paths from the
  orchestrator** — the dead-in-skill-use nangate hardcoded fallbacks are removed
  (output-neutral: they never fire when `R2G_LIB_FILES` is passed).

## Behavior-preservation guarantee + verification

- **Byte-for-byte on TWO platforms (empirical gate):** snapshot `aes_core` (nangate45) and
  `cordic` (sky130hd) `labels/*.csv` + `features/*.csv` **before**, refactor, assert
  **identical after**. These two exercise both cell-type strategies, both layer schemes, two
  voltages, two dbu — so a refactor regression in any shared parser or profile is caught.
- **Other ORFS platforms — identical by construction + unit tests:** every per-tech value is
  ported verbatim into the profiles; a unit test asserts each profile's values **equal the
  current scattered constants** (voltage map, tap patterns, curated cell-type map), and the
  tech-LEF/`.lib.gz` resolution tests (asap7/gf180/ihp real PDK files) become regression tests.
- Existing `tests/test_extract_{congestion,wirelength}.py`, `test_compute_label_stats.py`,
  and the feature tests stay green (re-pointed at `techlib`).

## Out of scope / non-goals
- No new platforms, no foundry-PDK abstraction, no auto-detect.
- No CSV schema changes, no congestion-model change, no label/feature value changes.
- `.tcl` stay OpenROAD scripts (cannot import Python `techlib`) — they consume resolved paths.

## Risks
- A faithful-move miss in the shared DEF/LEF parser or a profile-value typo → caught by the
  two-platform byte-for-byte diff + the profile-equals-current-constants unit test.
- `techlib.resolve` shim must reproduce `resolve_platform_paths.sh`'s exact `KEY=VALUE`
  output → asserted by a test comparing shim output to the current script on each platform
  config before the shell script is replaced.
