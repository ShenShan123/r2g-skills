# def-graph (R2GSkills) debug / validation results (2026-11-07)

**Inputs.** Raw pilot500 nangate45 designs on memlab203, read-only, via
`make_defgraph_project.sh`. The shim was ported from `exp4_conversion/r2g_arm` (that file was not
modified). It adds every stage ODB plus a stage-artifact manifest, so the four-stage builder and
the image builder can run.

**Products run.**
- `run_labels`, `run_features`, `run_graphs` (`R2G_GRAPH_KIND=both`);
- `run_stage_dataset` (four-stage);
- `run_images`.

**Our checks.**
- `adapter_defgraph.py`, the format adapter, which the conversion study can reuse;
- `truth_defgraph.py` + `v1_defgraph_check.py` for b–f;
- `v1_4stage_check.py`;
- `v1_images_dg.py`.

**def-graph's own checks.**
- `tools/verify_graph_dataset.py --batch --signoff-recheck`;
- `checks/validate_four_stage.py` and `summarize_four_stage_graph_data.py`;
- `verify_images.py`;
- the pytest suite.

**Sets.**
- dev: the 6 designs of `dev_designs.json`;
- check: 12 designs, `check_set.json`, seed 20261109, plus 3 edge-case designs (C00180 slash names,
  C02816 and C00293 IO feedthroughs);
- confirm: 10 designs, `confirm_set.json`, seed 20261110, drawn after the check set exposed two
  bugs; disjoint from both.

## Bugs

| # | Bug | Evidence | Fix | Test (fails on HEAD) | Before → after |
|---|---|---|---|---|---|
| 1 | Four-stage lineage masks legally pin-swapped nets (R2G2 `02`) | C03005 `_338_` NOR2: synthesis A1=`_054_`, DEF `( _338_ A2 )` on `_054_` | D15 `legal_pin_permutation_remap` (Liberty truth-table proof), threaded to `02`/`03`/`05` | `test_r2g2_lineage_pin_permutation.py` (4 tests) | dev: 1,384 → 7 masked-but-resolvable nets; +1,377 valid labels, all equal to truth |
| 2 | Validator aborts every combinational design | C02241, C00702: `已配置Timing源但没有有效Pin标签` | D16 `timing_label_presence_error` | 3 tests | dev four-stage builds 4/6 → 6/6 |
| 3 | Verifier false-red on `graph_kind=both`; 'both'-mode homo files never verified | `[FAIL] hetero manifest graph_kind == hetero — both` | `graph_kind_is_hetero` + `both_mode_homo_checks` (edge multiset) | `test_graph_kind_both_counts_as_hetero` | false FAIL on every 'both' build → PASS, plus 5 new checks |
| 4 | RC labels dropped for names with a literal '/' (`techlib.spef._deesc`) | C00180: 59 nets have NaN ground cap; own verifier join rate 56.9 % | keep `\/` (shared techlib) | `test_spef.py` (corrected + 1 new) | C00180 b–f ground cap 4,827 → 4,886 / 4,886 |
| 4b | Verifier SPEF oracle had the same rule | after fix 4: coupling count 45,598 vs 43,690 | `_spef_deesc` keeps `\/` | `test_verifier_spef_deesc_keeps_literal_slash` | gift_core 296/297 (only the signoff gate fails) |
| 5 | `input [0:0] x` named `x` in the four-stage base graph (R2G2 `01`) | C02816 `i_register_ready`: NaN IO position, 24/288 STA paths rejected, build aborted (path_ratio 0.9167 < 0.95) | D17 `single_bit_vector_names` | `test_r2g2_single_bit_vector.py` (2 tests) | C02816 four-stage build fails → PASS |
| doc | graph-dataset.md stale ("pin positions = instance origin"; "exact inverse"); image `pin_density` wording | — | docs corrected | — | — |

The regression tests for bugs 1–5 fail on the HEAD code: 11 of the new/changed tests
(`evidence/regression_tests_on_HEAD.txt`). The verifier-oracle test (4b) was added afterwards and
was not run against HEAD; it should fail there because the HEAD regex strips `\/`.

## Results after the fixes (fix2: 31 designs = 6 dev + 15 check/edge + 10 confirm)

**Our independent checks: every applicable group passes on all 31 designs.**
- b–f: 125 groups. Covers identity, topology b–f, gate/net/iopin/pin features, and the congestion,
  wirelength, timing and ground-cap labels with their raw twins.
- Four-stage: 25 groups.
- Images: 8 groups.

**def-graph's own checks.**
- `validate_four_stage` PASS and `structural_issues=0` on 31/31.
- `verify_images` 17/17 on 31/31.
- pytest: 597 passed / 57 skipped (208) and 593 / 61 (203); skips are machine-local platform files.
- `verify_graph_dataset`: 296–297 checks per design. Two failures remain, both documented rather
  than fixed:
  - `signoff.gate verdict` fails on every design. This is correct: the pilot500 re-run has no
    DRC/LVS reports.
  - `ext.irdrop physical (≤ 20 % of supply)` fails on C01484, at 298 mV. The label equals an
    independent PDNSim rerun, so this is an extreme design tripping a heuristic bound.

## Net mapping (four-stage; canonical = 1_2_yosys.v)

Totals over the 31 designs:
- 249,118 valid route wirelength labels, each equal to Σ over the net's independently traced
  physical pieces (max |Δ| float32 rounding).
- Valid ground caps also equal Σ over the pieces.
- Split nets per design: up to 4,754; max split degree 694 (clock tree).

**Clock nets split by CTS.** The label equals Σ over the CTS pieces whenever it is valid (e.g.
counter `clock0`: 6 pieces, 266.675 µm).

**Masked nets.**
- 22,009 masked nets are also unresolvable or ambiguous in our truth: many-to-one merges, deleted
  endpoints, clones.
- 143 remain masked though our truth resolves them:
  - C00293 (96) and C02816 (40) are IO feedthroughs, where one physical net carries an input port
    and an output port, i.e. two synthesis nets. These are correctly ambiguous; our merge detector
    ignores port endpoints (a checker limit, not a def-graph bug).
  - C00702 (7) are swaps D15 cannot prove: the swapped nets were also renamed or buffered.

**Synthesis-level connectivity and alignment.**
- Gates, pins, nets, ports and pin→net edges equal 1_2_yosys.v.
- Node order, labels and edges are identical across floorplan, placement, CTS and route on 31/31.
- Canonical net pin_count shows no IO-feedthrough leak (the D-A2 class is absent).
- Names follow the documented r2g2 `canonical_name` convention (`/`→`.`): SPEC-AMBIGUITY, the
  same as in the pilot dataset.

**b–f product.** Identity is the final DEF's physical nets, so there is no one-to-many mapping.
The clock tree and FILL/TAP cells are excluded, as documented.

**Orientations.** FN/FS and E/W/FE/FW are correct in every code path: `techlib.lef.apply_orient`,
r2g2 `transform_pin`, congestion `cell_bbox_dbu`, image `footprint`/`orient_pt`, and the verifier.

## Timing (s per design, mean / max over 31 designs; 203, 1 thread each, nice 10)

shim 1.0 / 2.5 · labels 6.5 / 21.5 · features 6.3 / 12.7 · graphs 37.8 / 106.9 ·
four-stage 84.8 / 174.5 · images 1.7 / 3.7.

Each four-stage sub-step carries about 4–8 s of fixed start-up even on a 245-cell design:
signoff gate, env sourcing, torch import. D15 adds about 6 % to four-stage time (C02146: 113 →
120 s). No optimisation was attempted.

## Unresolved / not done

- IR drop is covered only by def-graph's own PDNSim recheck (no independent PDNSim of ours).
- Coupling-cap and equivalent-resistance edges are checked by the def-graph verifier only.
- D15 residue: a swapped pin node keeps the geometry of the physical pin of the same name.
- The confirmation set has only one unseen small design left in the sample.
- CLAUDE.md still says "eleven deltas (D1–D11)" and `hetero_to_homo` "exact inverse"; it was not
  edited.
