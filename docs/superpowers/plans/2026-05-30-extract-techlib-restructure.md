# Extract `techlib` Restructure — Implementation Plan

**Date:** 2026-05-30 · **Branch:** `feat/label-extraction-stage`
**Design:** [`specs/2026-05-30-extract-techlib-restructure-design.md`](../specs/2026-05-30-extract-techlib-restructure-design.md)

> **REQUIRED SUB-SKILL — TDD + verification-before-completion.** This is a *behavior-neutral*
> refactor: the gate is **byte-for-byte identical CSV output** on two platforms. Capture the
> baseline FIRST (Task 0); after every module move, re-run the extractors on `aes_core`
> (nangate45) and `cordic` (sky130hd) and assert the CSVs are unchanged. No step lands red.

## Invariants (must hold after every task)
- `labels/*.csv` + `features/*.csv` for `aes_core` and `cordic` are byte-identical to the
  Task-0 baseline.
- Every per-tech constant moved into `techlib` equals its pre-refactor value (asserted).
- `resolve_platform_paths.sh` emits the same `KEY=VALUE` lines as before (asserted before it
  becomes a shim). `run_labels.sh` / `run_features.sh` / `.tcl` CLIs unchanged.
- No 3rd-party imports; `python3` stdlib only. Existing pytest stays green.

## Source-material migration (`scripts/extract/{labels,features}` → `scripts/extract/techlib`)

| From | To | Transform |
|------|----|-----------|
| `features/def_parse.py` | `techlib/def_parse.py` | move; add `parse_components` (x,y,master,cell_type view) + a `route_segments()` iterator that congestion-demand & wirelength-Manhattan both call |
| `features/lib_db.py` | `techlib/liberty.py` | move verbatim (parser + classifiers + tap + power/gnd sets) |
| `features/cell_type_map.py` | `techlib/cell_types.py` | move verbatim (curated + runtime + std-cell filter) |
| `labels/extract_congestion.parse_tech_lef` + `def_parse.routing_layer_regex` | `techlib/lef.py` | unify → `routing_layer_info()` (pitch/dir) + `routing_layers()` (names); congestion's `DEFAULT_LAYER_INFO` → `profile.fallback_routing_layers` |
| `resolve_platform_paths.sh` (voltage map + make-eval) | `techlib/resolve.py` + `techlib/profile.py` | port to Python; shell → shim |
| scattered tap/voltage/cell-type constants | `techlib/profile.py` | one `TechProfile` per ORFS platform, values copied verbatim |

## File structure
**Create:** `scripts/extract/techlib/{__init__,profile,resolve,def_parse,lef,liberty,cell_types}.py`;
`tests/test_techlib_profile.py`, `tests/test_techlib_resolve.py`, `tests/test_techlib_lef.py`,
`tests/test_techlib_crossplatform.py`.
**Modify:** all `features/*.py` + `labels/extract_{congestion,wirelength}.py` (imports);
`labels/extract_{timing,irdrop}.tcl` (drop nangate fallback; paths from orchestrator);
`scripts/flow/resolve_platform_paths.sh` (→ shim); `tests/conftest.py` (+`TECHLIB_DIR`);
`references/{label,feature}-extraction.md`, `CLAUDE.md` (layout note: `scripts/extract/techlib/`).
**Delete after move:** `features/def_parse.py`, `features/lib_db.py`, `features/cell_type_map.py`
(re-exported from techlib only if a test/consumer still imports the old path).

## Tasks

- [ ] **Task 0 — Baseline gate.** Run `run_features.sh` + `run_labels.sh` on `aes_core` and
  `cordic`; copy their `labels/` + `features/` CSVs to `/tmp/techlib_baseline/<design>/` and
  record md5sums. Write `tests/test_techlib_crossplatform.py` scaffolding that will diff
  re-generated CSVs vs this baseline (skipif baseline absent).
- [ ] **Task 1 — `techlib.def_parse`.** Move `features/def_parse.py`; add `parse_components`
  (rich view) + `route_segments()` iterator. Unit-test the iterator against congestion's and
  wirelength's current point-chain handling (incl. `*`-relative). 
- [ ] **Task 2 — `techlib.lef`.** Unify the two tech-LEF parsers into `routing_layer_info()` +
  `routing_layers()`. Test on real nangate/sky130/asap7/gf180/ihp tech LEFs (layer names +
  that `routing_layers()` == old `metal\d+`/`TYPE ROUTING` results).
- [ ] **Task 3 — `techlib.liberty`.** Move `features/lib_db.py`. Tests: classifiers unchanged;
  `.lib.gz` parses on a real asap7/gf180 lib; "no liberty" warning.
- [ ] **Task 4 — `techlib.cell_types`.** Move `features/cell_type_map.py`. Tests: curated
  nangate map, std-cell-runtime determinism + macro→UNKNOWN.
- [ ] **Task 5 — `techlib.profile`.** `TechProfile` per ORFS platform (supply_voltage,
  tap_patterns, cell_type strategy, fallback_routing_layers). `test_techlib_profile.py`
  asserts each value **equals the current scattered constant** (voltage map, `_PLATFORM_TAP_EXTRA`,
  `NANGATE45_CELL_TYPE_MAPPING`).
- [ ] **Task 6 — `techlib.resolve` + shim.** Port make-eval path resolution + voltage map to
  Python with a `KEY=VALUE` CLI. `test_techlib_resolve.py` asserts the CLI output **equals the
  current `resolve_platform_paths.sh` output** on each platform's ORFS design config (nangate45,
  sky130hd, asap7, gf180, ihp). Only then replace the shell script with the shim.
- [ ] **Task 7 — Re-point feature workers** to import `techlib.*` (drop `case_dir` lib path →
  techlib liberty/cell_types). Re-run features on `aes_core` + `cordic`; **assert byte-identical
  to baseline.**
- [ ] **Task 8 — Re-point label workers.** `extract_congestion` + `extract_wirelength` use
  `techlib.def_parse` + `techlib.lef`; congestion fallback ← `profile.fallback_routing_layers`.
  Drop the `.tcl` nangate hardcoded liberty fallbacks (paths from orchestrator). Re-run labels on
  `aes_core` + `cordic`; **assert byte-identical to baseline.**
- [ ] **Task 9 — Tests green.** `conftest` `TECHLIB_DIR`; re-point/keep
  `test_extract_{congestion,wirelength}.py`, `test_compute_label_stats.py`, feature tests; full
  suite green.
- [ ] **Task 10 — Cross-platform resolution regression.** `test_techlib_crossplatform.py`
  asserts (against real PDK files) the values verified 2026-05-30: voltages
  nangate 1.1 / sky130 1.8 / asap7 0.70 / gf180 5.00 / ihp 1.20; routing layers parse
  (metal*, li1/met*, M1–M9, Metal*+TopMetal); `.lib.gz` cells parse.
- [ ] **Task 11 — Docs/wiring.** Update `references/{label,feature}-extraction.md` to point at
  `techlib`; add `scripts/extract/techlib/` to `CLAUDE.md` layout + the SKILL.md resource map.
- [ ] **Task 12 — Self-review + commit.** Full pytest green; baseline diffs clean on both
  platforms; adversarial read of the diff; commit `refactor(skill): consolidate extract per-tech
  logic into techlib (behavior-neutral)`.

## Self-review checklist
- [ ] `aes_core` + `cordic` CSVs byte-identical to baseline (both stages).
- [ ] Profile values == pre-refactor scattered constants (test).
- [ ] `resolve_platform_paths.sh` shim output == old output on all platform configs (test).
- [ ] No nangate hardcode remains on a live path; `.tcl` get paths from orchestrator only.
- [ ] Stdlib only; existing + new pytest green; docs/layout updated.
