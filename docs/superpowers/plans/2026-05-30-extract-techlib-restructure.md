# Extract `techlib` Restructure — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL — use `superpowers:subagent-driven-development`
> (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate every per-platform concern in `r2g-rtl2gds/scripts/extract/` (tap cells,
supply voltage, cell-name → id, routing layers, liberty parse) into one `techlib` Python package
shared by the label and feature extractors — a **behavior-neutral** refactor (byte-identical CSVs).

**Architecture:** New `scripts/extract/techlib/` package = single source of truth; `labels/` +
`features/` workers import it; `resolve_platform_paths.sh` becomes a thin shim over
`python3 -m techlib.resolve`. ORFS platforms only (`nangate45`, `sky130hd`, `sky130hs`, `asap7`,
`gf180`, `ihp-sg13g2`) — no generic-PDK abstraction, no auto-detect.

**Tech Stack:** Python 3.10+ stdlib only (no 3rd-party imports). Existing pytest suite must stay green.

**Date:** 2026-05-30 · **Branch:** `feat/label-extraction-stage`
**Design:** [`specs/2026-05-30-extract-techlib-restructure-design.md`](../specs/2026-05-30-extract-techlib-restructure-design.md)

---

> **REVISION 2026-05-30 (after empirical v3 diff).** Three changes vs. the original plan, all from
> the cross-platform value-diff run this session:
> 1. **`feature_test_v3/` is NOT a source of new behavior — do not merge it.** It is the
>    pre-refactor (`feature_test_v2`-style) ancestor of `features/`. Proven this session: on
>    `aes_core` (nangate45) all 8 feature CSVs are **byte-identical** to the skill; on `cordic`
>    (sky130hd) v3 is **strictly worse** — `num_layer` collapses to `0` (hardcoded `metal\d+` vs.
>    the skill's tech-LEF-derived layers) and `cell_type_id` collapses to UNKNOWN (nangate-only
>    curated map). The skill already supersets v3. This refactor consolidates the **skill's**
>    current code; v3 is irrelevant to it.
> 2. **Baseline-DEF trap (Task 0):** `cordic` has BOTH a nangate45 and a sky130hd backend run.
>    Task 0 MUST pin the sky130 DEF — see the exact path + the masters check below.
> 3. **Measured baseline values** for `aes_core` + `cordic` are recorded in Task 0 / Task 10 as
>    concrete assertions, and the `cell_type_id` baseline quirk is documented as preserve-don't-fix.

> **BEHAVIOR-NEUTRAL GATE.** The pass/fail criterion is **byte-for-byte identical CSV output** on
> two platforms. Capture the baseline FIRST (Task 0); after every module move, re-run the
> extractors on `aes_core` (nangate45) and `cordic` (sky130hd) and assert the CSVs are unchanged.
> No step lands red.

## Invariants (must hold after every task)
- `labels/*.csv` + `features/*.csv` for `aes_core` and `cordic` are byte-identical to the
  Task-0 baseline.
- Every per-tech constant moved into `techlib` equals its pre-refactor value (asserted).
- `resolve_platform_paths.sh` emits the same `KEY=VALUE` lines as before (asserted before it
  becomes a shim). `run_labels.sh` / `run_features.sh` / `.tcl` CLIs unchanged.
- No 3rd-party imports; `python3` stdlib only. Existing pytest stays green.
- **Do not "improve" any value while moving it** — `cell_type_id` is uniformly `UNKNOWN` on
  cordic in the baseline (see Task 0 note); preserve it. Behavior fixes are out of scope here.

## Source-material migration (`scripts/extract/{labels,features}` → `scripts/extract/techlib`)

| From | To | Transform |
|------|----|-----------|
| `features/def_parse.py` | `techlib/def_parse.py` | move; add `parse_components` (x,y,master,cell_type view) + a `route_segments()` iterator that congestion-demand & wirelength-Manhattan both call |
| `features/lib_db.py` | `techlib/liberty.py` | move verbatim (parser + classifiers + tap + power/gnd sets) |
| `features/cell_type_map.py` | `techlib/cell_types.py` | move verbatim (curated map + runtime map + std-cell filter) |
| `labels/extract_congestion.parse_tech_lef` + `def_parse.routing_layer_regex` | `techlib/lef.py` | unify → `routing_layer_info()` (pitch/dir) + `routing_layers()` (names); congestion's `DEFAULT_LAYER_INFO` → `profile.fallback_routing_layers` |
| `resolve_platform_paths.sh` (voltage map + make-eval) | `techlib/resolve.py` + `techlib/profile.py` | port to Python; shell → shim |
| scattered tap/voltage/cell-type constants | `techlib/profile.py` | one `TechProfile` per ORFS platform, values copied verbatim |

Voltage map to port verbatim (from `resolve_platform_paths.sh:74-82`): nangate45 `1.1`,
sky130hd/sky130hs `1.8`, asap7 `0.70`, gf180 `5.0`, ihp-sg13g2 `1.2`, fallback `1.0`. Tap extras
to port verbatim (`features/lib_db._PLATFORM_TAP_EXTRA`): base `"TAP"`, gf180 += `FILLTIE`,`ENDCAP`.

## File structure
**Create:** `scripts/extract/techlib/{__init__,profile,resolve,def_parse,lef,liberty,cell_types}.py`;
`tests/test_techlib_profile.py`, `tests/test_techlib_resolve.py`, `tests/test_techlib_lef.py`,
`tests/test_techlib_crossplatform.py`.
**Modify:** all `features/*.py` + `labels/extract_{congestion,wirelength}.py` (imports);
`labels/extract_{timing,irdrop}.tcl` (drop nangate fallback; paths from orchestrator);
`scripts/flow/resolve_platform_paths.sh` (→ shim); `tests/conftest.py` (+`TECHLIB_DIR` on
`sys.path`); `references/{label,feature}-extraction.md`, `CLAUDE.md` (layout note:
`scripts/extract/techlib/`); `r2g-rtl2gds/SKILL.md` resource map.
**Delete after move:** `features/def_parse.py`, `features/lib_db.py`, `features/cell_type_map.py`
(keep a 1-line re-export shim from the old path ONLY if a test/consumer still imports it —
prefer re-pointing the importer).

## Tasks

- [ ] **Task 0 — Baseline gate (do this first; it is the whole safety net).**
  Pin these exact inputs (verified this session):
  - `aes_core` (nangate45): DEF `design_cases/aes_core/backend/RUN_2026-04-12_18-04-55/results/6_final.def`.
  - `cordic` (sky130hd): DEF `design_cases/cordic/backend/RUN_2026-05-17_05-58-40/results/6_final.def`.
    ⚠️ **cordic has multiple backend runs incl. a nangate45 one** (`RUN_2026-04-12_*`,
    `RUN_2026-05-17_05-40-32` — masters `FILLCELL_X32`). Verify the DEF you pick has masters
    starting `sky130_fd_sc_hd__` (`grep -m1 sky130_fd_sc_hd__ <def>`) before using it. Using the
    nangate cordic DEF silently produces a bogus baseline.

  Run `run_features.sh` + `run_labels.sh` on both; copy their `labels/` + `features/` CSVs to
  `/tmp/techlib_baseline/<design>/` and record md5sums. Write `tests/test_techlib_crossplatform.py`
  scaffolding that diffs re-generated CSVs vs this baseline (skipif baseline absent).

  **Measured baseline anchors (assert these survive the refactor):**
  - aes_core: all 8 feature CSVs reproduce byte-identically via the skill workers (validated);
    cell_type_id uses the curated map (distinct ids 0–128). aes_core is the design that
    **exercises** cell-type differentiation.
  - cordic metadata row: `num_cells=6508, num_nets=1454, num_ios=107, dbu=1000,
    tracks_per_layer=li1:1125|met1:1294|met2:956|met3:646|met4:478|met5:128, V_nom=1.80`.
  - cordic `nodes_net.num_layer` distinct set = `{0,2,3,4,5}` (li1/met layers via tech-LEF).
  - cordic `nodes_gate/edges_gate_pin.cell_type_id` ≡ `428` for **every** master — i.e. cordic's
    masters all resolve to `UNKNOWN` (=sky130 std-cell count). **This is the current baseline;
    preserve it byte-for-byte. Do NOT try to "fix" cell-type differentiation in this refactor.**
    (Worth a separate investigation later: runtime cell-type map appears not to differentiate on
    non-nangate platforms.)

- [ ] **Task 1 — `techlib.def_parse`.** Move `features/def_parse.py`; add `parse_components`
  (rich view) + `route_segments()` iterator. Unit-test the iterator against congestion's and
  wirelength's current point-chain handling (incl. `*`-relative coords).
- [ ] **Task 2 — `techlib.lef`.** Unify the two tech-LEF parsers
  (`labels/extract_congestion.parse_tech_lef` + `features/def_parse.routing_layer_regex`) into
  `routing_layer_info()` (pitch/dir) + `routing_layers()` (names). Test on real
  nangate/sky130/asap7/gf180/ihp tech LEFs: layer names + that `routing_layers()` reproduces the
  old `metal\d+` / `TYPE ROUTING` results (regression-check `num_layer` against the cordic anchor).
- [ ] **Task 3 — `techlib.liberty`.** Move `features/lib_db.py`. Tests: classifiers unchanged;
  `.lib.gz` parses on a real asap7/gf180 lib; "no liberty" warning emitted on empty input.
- [ ] **Task 4 — `techlib.cell_types`.** Move `features/cell_type_map.py`. Tests: curated nangate
  map preserved (`UNKNOWN`=95, FAKERAM upper-cased), runtime-map determinism + macro→UNKNOWN, and
  the cordic-style "all masters → UNKNOWN" path stays stable.
- [ ] **Task 5 — `techlib.profile`.** `TechProfile` per ORFS platform (supply_voltage,
  tap_patterns, cell_type strategy, fallback_routing_layers). `test_techlib_profile.py` asserts
  each value **equals the current scattered constant** (voltage map above, `_PLATFORM_TAP_EXTRA`,
  `NANGATE45_CELL_TYPE_MAPPING`).
- [ ] **Task 6 — `techlib.resolve` + shim.** Port the ORFS `make --eval` path resolution + the
  voltage map out of `resolve_platform_paths.sh` to Python with a `KEY=VALUE` CLI.
  `test_techlib_resolve.py` asserts the CLI output **equals the current `resolve_platform_paths.sh`
  output** on each platform's ORFS design config (nangate45, sky130hd, asap7, gf180, ihp). Only
  then replace the shell script with the shim.
- [ ] **Task 7 — Re-point feature workers** to import `techlib.*` (drop `case_dir` lib path →
  techlib liberty/cell_types). Re-run features on `aes_core` + `cordic`; **assert byte-identical to
  baseline.**
- [ ] **Task 8 — Re-point label workers.** `extract_congestion` + `extract_wirelength` use
  `techlib.def_parse` + `techlib.lef`; congestion fallback ← `profile.fallback_routing_layers`.
  Drop the `.tcl` nangate hardcoded liberty fallbacks (paths from orchestrator). Re-run labels on
  `aes_core` + `cordic`; **assert byte-identical to baseline.**
- [ ] **Task 9 — Tests green.** `conftest` `TECHLIB_DIR` on `sys.path`; re-point/keep
  `test_extract_{congestion,wirelength}.py`, `test_compute_label_stats.py`,
  `test_compute_feature_stats.py`, `test_feature_regression.py`,
  `test_feature_parameterization.py`; full suite green.
- [ ] **Task 10 — Cross-platform resolution regression.** `test_techlib_crossplatform.py` asserts
  (against real PDK files) the values verified 2026-05-30: voltages nangate `1.1` / sky130 `1.8` /
  asap7 `0.70` / gf180 `5.0` / ihp `1.2`; routing layers parse (metal*, li1/met*, M1–M9,
  Metal*+TopMetal); `.lib.gz` cells parse.
- [ ] **Task 11 — Docs/wiring.** Update `references/{label,feature}-extraction.md` to point at
  `techlib`; add `scripts/extract/techlib/` to `CLAUDE.md` layout + the SKILL.md resource map.
- [ ] **Task 12 — Self-review + commit.** Full pytest green; baseline diffs clean on both
  platforms; adversarial read of the diff; commit
  `refactor(skill): consolidate extract per-tech logic into techlib (behavior-neutral)`.

## Self-review checklist
- [ ] `aes_core` + `cordic` CSVs byte-identical to baseline (both label + feature stages).
- [ ] cordic used the **sky130** DEF (masters `sky130_fd_sc_hd__*`), not the nangate run.
- [ ] Profile values == pre-refactor scattered constants (test).
- [ ] `resolve_platform_paths.sh` shim output == old output on all platform configs (test).
- [ ] No nangate hardcode remains on a live path; `.tcl` get paths from orchestrator only.
- [ ] cell_type_id baseline (cordic ≡ 428) preserved, not "fixed".
- [ ] Stdlib only; existing + new pytest green; docs/layout updated.
