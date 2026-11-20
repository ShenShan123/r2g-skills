# Experiment 4 (conversion): R2GSkills def-graph vs LLM-written converters — results (2026-11-07)

Protocol: `PROTOCOL.md` (frozen before the LLM runs; amendments in its §9). Test set: the 43 designs
of `exp4_validation/sample.json:v1_all`; development: the 6 of `dev_designs.json`. Host 216.

## Scope (stated explicitly)

- **Graph task**: def-graph views **b and c only**, homogeneous `Data`; all node features of b/c,
  c's edge features, labels congestion (y1), timing (y3), wirelength (y4), RC ground cap (y5) with
  their raw twins. Out of scope: views d/e/f, IR drop, coupling/resistance edges, global features.
  Spec: `spec/CONTRACT.md`.
- **Image task**: def-graph's image product minus every power-related channel: features
  `cell_density, macro_region, rudy, pin_rudy, pin_density` (from `3_place.def`), labels
  `congestion, congestion_raw` (from `5_route.def`); `ir_drop` excluded. Spec: `spec_image/CONTRACT_IMAGE.md`.

## Scorer of record

The validation study's shared code, snapshot `216:$W/v1snap/20261107_1126` (sha256 in its
`SHA256SUMS`, identical to the repo files at scoring time): `adapter_defgraph.py`, `truth_defgraph.py`
(+ `sta_pins_dg.tcl`, `gt_raw.py`, `match.py`), `v1_defgraph_check.py` (graphs), `v1_images_dg.py`
(images). `prelim_v1_20261107_1126` was produced by this shared code, not by my scorer, and the
final numbers below come from the same snapshot (R2G scored into the same result root). The
checker's `present_views` group (asks for b–f) is out of scope and dropped.
Cross-check: my own scorers (`scoring/defgraph_check.py`, `scoring/image_check.py`) were run on
every arm and design; they give the **same pass/fail verdict on every design × arm × task**
(graphs; images under the same binning). One bug in my graph scorer was found and fixed before its
results were used (num_layer looked up by raw name after `gt_raw.py` switched to canonical keys).

## Results

### Graph (b, c)

| arm | model calls | tokens in / cached / out (reasoning) | USD | dev wall | conversion success | wall s/design median (max) | peak RSS MB median | full pass |
|---|---|---|---|---|---|---|---|---|
| R2G def-graph (fixed tree) | 0 | 0 | 0 | — | 43/43 | 59.6 (148.7) | 503 | **43/43** |
| DeepSeek (deepseek-flash) | 100 (cap hit) | 6,516,010 / 6,327,424 / 66,986 (33,833) | 0.164 | 28.5 min | 43/43 | 26.3 (60.4) | 543 | **0/43** |
| GPT (gpt-6.1-sol) | 24 (finished) | 722,950 / 673,677 / 15,786 (3,133) | 0.324 | 10.6 min | 43/43 | 15.3 (31.0) | 461 | **43/43** |

Per check group (pooled match rate; designs passing out of 43): all 68 in-scope groups
(identity, edges and fwd/rev pairing, every gate/net/iopin/pin feature, all labels, b and c) are
1.0 (43/43) for R2G and GPT. DeepSeek is 1.0 (43/43) everywhere except:

| group | DeepSeek |
|---|---|
| b/c.L_congestion | 0.9449 (0/43) |
| b/c.L_congestion_raw | 0.9438 (0/43) |
| b/c.L_wirelength | 0.9900 (0/43) |
| b/c.L_wirelength_raw_um | 0.9900 (0/43) |

Full table: `results/tables.md`; per design: `results/summary.json`.

### Image (final, after the reference binning fix)

| arm | model calls | tokens in / cached / out (reasoning) | USD | dev wall | conversion success | wall s/design median (max) | peak RSS MB median | full pass |
|---|---|---|---|---|---|---|---|---|
| R2G def-graph (fixed tree + binning fix) | 0 | 0 | 0 | — | 43/43 | 25.5 (72.2) | 229 | **43/43** |
| DeepSeek | 79 (finished) | 8,315,848 / 8,241,408 / 129,352 (74,408) | 0.213 | 18.7 min | 43/43 | 1.9 (6.0) | 50 | **43/43** |
| GPT | 11 (finished) | 193,806 / 166,351 / 10,158 (1,880) | 0.173 | 3.2 min | 43/43 | 3.8 (11.0) | 66 | **43/43** |

All 8 image groups (`grid_shape, X_cell_density, X_macro_region, X_rudy, X_pin_rudy, X_pin_density,
label_congestion, label_congestion_raw`) are 1.0 (43/43) for all three arms. Scored with the
shared checker snapshot `216:$W/v1snap/20261107_1457` (only change: `v1_images_dg.py`
sha256 `13fdd4770a4a24a34e217782c3b8ad898863ac2bec788ca3202d6de6650235d9`, amendment A9),
result root `216:$W/results/final_img_20261107_1457/`. This matches my exact-binning scorer exactly.
R2G images were rebuilt (49/49) with the fixed `build_images.py`; def-graph's own
`verify_images.py` now reports 19/19 on 49/49 (two new per-GCell checks).

**Pre-binning-fix (superseded, kept):** with def-graph's and the checker's old floating-point µm
binning, R2G 43/43 and both LLMs 5/43 (`X_pin_density` 0.982269, `X_pin_rudy` 0.982198) —
`results/summary_image_PRE_binning_fix.json`, `216:$W/r2g_fix/apply_image_PRE_binning_fix/`,
`216:$W/results/prelim_v1_20261107_1126/per_design/*/image_*.json`.

## The identical image scores (diagnosis, one design)

C00198, GCell (4,17), `pin_density`: GPT 8, DeepSeek 8, R2G 9, truth (shared checker) 9, truth
evaluated exactly 8. The single differing point is pin `_1797_/ZN` of net `_0124_` at
x = 28,500 DBU = exactly 5 × 5,700 (a GCell line). CONTRACT_IMAGE §2: `i = floor((x − X0)/sx)` →
28500/5700 = 5 → column 5. def-graph (`build_images.Grid.index`) and the shared checker
(`v1_images_dg.cell`) evaluate it in floating-point µm: 14.25 // 2.85 = 4.0 (14.25/2.85 =
4.999…), so they put the point in column 4. Of the GCell lines 1..199 at this step, 134 suffer this
rounding. Every mismatching cell on all 38 designs is explained by such boundary points: with
exact binning both LLMs match on every GCell of all 43 designs. The other candidates were ruled
out: the pin position rule (mean of all LEF RECT centres) agrees everywhere else, and clock pins
are included by spec, LLMs, def-graph and checker alike (no A7-style exclusion in `v1_images_dg.py`).

So the spec does **not** mis-describe the intended definition — it states it exactly, and both
models implemented it exactly (integer DBU). The reference implementation and the shared checker
share a floating-point artefact on points that lie exactly on a GCell line. I therefore did **not**
re-run the image development runs (that would require writing the artefact into the spec).
Graph spec checked the same way: congestion binning is in integer DBU in def-graph, spec and truth;
GPT matches all graph groups on all 43 designs, so no analogous issue exists there.
**Resolved** (coordinator, failure-patterns #68): exact-DBU binning (`Grid.bin`) in def-graph's
`build_images.py` (index and splat bounds) and `verify_images.py` (+ per-GCell pin_density/pin_rudy
checks), and `cell()` in the shared `v1_images_dg.py` (amendment A9). Deployed to 216 (sha256 on
both sides; added to `r2g_arm/identity/`), R2G images rebuilt, all three arms re-scored: 43/43 each.
The LLMs had it right; the reference was wrong.

## What each LLM converter got wrong

- **DeepSeek, graph** (converter sha `46a92547…`): its DEF route-point regex
  `\(\s*([*-]?\d+|\*)\s+([*-]?\d+|\*)\s*\)` accepts only two-number points and silently drops a
  point with an extension value. Example C03387 net `b[0]`: `+ ROUTED metal6 ( 78590 154140 )
  ( * 154530 0 )` → the 390-DBU segment is lost: wirelength 5.940 µm vs truth 6.135 µm. The spec
  (§6.1/6.3) says every pair of consecutive points of a routing statement counts and `*` repeats a
  coordinate; it does not mention DEF's optional extension value, but such points are standard DEF
  5.8 and present in the dev designs (GPT handled them). Mismatching nets ⊂ nets with a 3-value
  point (C03387: 10 of 12; C00198: 51 of 53). The lost demand also shifts congestion
  (e.g. `_0854_` raw 0.06728 vs 0.06734). Everything else (identity, edges, all features, timing,
  ground cap) is exact on all 43 designs. It used all 100 calls.
- **GPT, graph** (sha `a32d8d43…`): no error found.
- **Both, image** (shas `ba3b4d0e…`, `6f6bc83c…`): no error — both image converters exposed a
  GCell-binning error in our reference (def-graph's image builder and the shared checker
  mis-binned pins lying exactly on a GCell line); fixed on our side, after which all three arms pass.

## R2G self-check (reported separately, not counted against conversion)

Graph manifests: `status: ok`, `signoff_health: dirty` on 49/49 (blockers drc, lvs, orfs): the
pilot500 re-run layouts carry no DRC/LVS reports (README: not DRC/LVS checked), so the gate ran in
warn mode and recorded this. def-graph's own image verifier: 17/17 on 49/49 before the binning fix, 19/19 on 49/49 after it.

## Deviations and caveats

- Signoff: the README says these layouts were not DRC/LVS checked (the task statement said
  signed off). No reports were fabricated; the gate recorded `dirty`.
- GPT's Responses route rejects `temperature`; 0.2 was sent to DeepSeek only (as in Exp 3).
- 216 is rootless cgroups v1: `--memory/--cpus` are ignored; thread pools capped by env (4 apply, 8 dev).
- Load gate raised 85 → 110 and ≤ 4 extra containers (coordinator); application used 1–2 workers per arm.
  Wall times were measured on a shared host at load ~95–160, so they are indicative; R2G's time
  includes its shim (ODB builds) and work outside the scope (IR drop via PDNSim, RC edges).
- R2G arm: rebuilt from the fixed, uncommitted def-graph working tree (identity:
  `r2g_arm/identity/`; the binning-fix update to the two image files is appended there, the graph
  arm was built before it and is unaffected — those files are image-only); 14 designs built earlier from HEAD 48bd9970 kept as
  `216:$W/r2g/apply_PREFIX_partial_stopped/`, not scored.
- The models could read the OpenROAD/OpenSTA source tree inside the ORFS image (both did); no R2G code was present.
- The shared checker's image tolerance (rel 1e-4 × max|truth|) differs from CONTRACT_IMAGE §5's
  per-element table; no verdict depends on the difference.
- Dev designs: R2G built all 6 (graphs and images, success); scores reported are test designs only.

## Paths

- Repo: `experiments/exp4_conversion/` — `PROTOCOL.md`, `RESULTS.md`, `spec/`, `spec_image/`,
  `harness/`, `r2g_arm/` (+ `identity/`), `scoring/`, `tests/negative_controls.py`, `results/`.
- 216: `/proj/workarea/yangao/exp4_conversion_20261107/` — `dev/`, `dev_img/` (transcripts, usage,
  frozen converters + sha256), `apply/`, `apply_img/`, `r2g_fix/apply_{graph,image}/`,
  `results/prelim_v1_20261107_1126/` (shared checker, all arms), `results/mine_20261107/`
  (cross-check), `results/final/`.
