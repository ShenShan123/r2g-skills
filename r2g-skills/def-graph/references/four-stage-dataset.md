# The R2G2.0 four-stage dataset (`run_stage_dataset.sh`)

The **second** dataset product of this skill, alongside the b–f views. Where
`run_graphs.sh` reads one signed-off `6_final.def` and folds it into five
topologies, this builder emits **four `HeteroData` graphs per design** —
`floorplan`, `placement`, `cts`, `route` — that share one canonical topology and
one set of post-route labels, and differ only in **what was legally knowable at
that prediction cutoff**.

```text
G_stage = shared canonical base graph (post-Yosys)
        + features available before <stage>
        + shared post-route labels
```

Upstream logic is vendored under `scripts/r2g2/`; the authoritative per-dimension
contract is `scripts/r2g2/upstream_docs/B_VIEW_DATASET_STRUCTURE.md`. Provenance
and every local delta: `scripts/r2g2/R2G2_UPSTREAM.md`. **Read that before
editing anything under `scripts/r2g2/`** — the main logic is upstream's and must
stay re-vendorable.

## Why a separate builder rather than a sixth view

The b–f views answer "given the finished layout, what is its structure?". These
four answer "given only what a tool knows *before* stage N, predict stage N's
outcome". That forces three things the b–f pipeline deliberately does not do:

| | b–f views | four-stage |
| --- | --- | --- |
| Topology source | `6_final.def` COMPONENTS | `1_2_yosys.v` (post-synth, backend-invariant) |
| Node set | whatever survived the backend | fixed canonical set, identical in all four graphs |
| Missing physical value | filtered or NaN | NaN **plus** an explicit `*_valid` feature column |
| Labels | per-view | one shared post-route set attached to all four |
| Leakage control | n/a | strict per-stage input whitelist, re-checked by a validator |

A gate the resizer deleted is still a node here, with `placement_valid=0`. That
is the point: the model must see the same entity set at every cutoff.

## The causal contract

| Prediction graph | Input cutoff | Physical input | Std-cell coords | HPWL / congestion | `gate|congestion_geom|gate` |
| --- | --- | --- | --- | --- | --- |
| `floorplan` | post-Yosys | none | — | — | absent |
| `placement` | post-floorplan | `2_floorplan.def` | fixed macros + IO only | — | absent |
| `cts` | post-placement | `3_place.def` | trusted | available | present |
| `route` | post-CTS | `4_cts.def` | trusted | available | present |

`5_route.def`, the SPEF, the PDNSim netlist and the post-route OpenSTA reports
are **label-only**. Stage 02 runs one subprocess per prediction stage so a stage
can only ever open its own DEF, and `checks/validate_four_stage.py` re-derives
the check independently from `metadata.csv`'s recorded `feature_source_path`.

Early-stage absence is `NaN` with the matching `*_valid` feature at 0 — never a
zero fill and never back-filled from a later DEF.

## Running it

```bash
export R2G_GRAPH_PYTHON=/proj/workarea/user5/pyenvs/rtl2graph/bin/python
bash scripts/flow/run_stage_dataset.sh design_cases/<design>
```

Picks the newest backend run that has all four stage `.odb` plus `6_final.spef`,
runs the signoff gate, then: config → base graph → features → OpenSTA →
labels → assemble → snapshots → validate.

Useful flags: `--run-dir` (pin a run), `--out-dir`, `--platform`,
`--skip-timing`, `--irdrop-sp <VDD_extracted.sp>`, `--stages config,base,…`,
`--force` (re-export stage DEFs). Env: `R2G_SIGNOFF_GATE`, `R2G2_MAX_PATHS`.

Each numbered script is independently runnable with `--config <sample>.json`,
which is the fastest way to iterate on one stage.

## What the adapters do

Upstream expects a hand-written JSON pointing at a curated `/data/...` tree.
`scripts/stage_dataset/` derives the same contract from an ORFS run:

* **`make_sample_config.py`** — exports the four stage DEFs from their `.odb`
  (via `extract/graph/odb_to_def.py`), resolves platform lib/LEF through the
  shared `resolve_platform_paths.sh`, detects `top_module` from the netlist, and
  writes `manifest.json` (SHA256 + stage `semantics` tokens the upstream
  integrity gates check) plus the sample config.
* **`build_encode_map.py`** — per-platform `encode_map.csv`. Global maps are
  copied verbatim from upstream; `cell_type_id` is enumerated with *the same
  Liberty parser stage 01 uses over the same glob*, because stage 01 hard-fails
  on any master missing from the map. ids are per-platform categorical.
* **`emit_timing_reports.py`** — runs OpenSTA from exactly `route_def + spef +
  sdc` and writes `paths_max.rpt`, `paths_min.rpt` and the
  `r2g2-opensta-timing-v3` manifest the label stage verifies by SHA256. It
  deliberately does *not* run from `6_final.odb`, which would be easier but
  would make the manifest attest to inputs the reports did not come from.

## Output layout

```text
<out-dir>/
├── <design>.json            # the sample config (single source of truth)
├── manifest.json            # raw-artifact SHA256 + stage semantics
├── encode_map.csv           # per-platform id vocabulary
├── stage_defs/              # 2_floorplan / 3_place / 4_cts / 5_route .def
├── time_rpt/                # paths_max/min.rpt + timing_manifest.json
├── reports/signoff_gate.json
└── generated/
    ├── base_graph/base_graph.pt
    ├── labels/              # gate_con_IR, net_wirelength_Cg, {pin,iopin}_timing,
    │                        # edges_net_net_Cc, edges_pin_pin_Reff
    ├── snapshots/           # base_to_{placement,route}_snapshot.json
    ├── stages/<stage>/      # features/ (8 CSVs) + heterograph.pt + sidecars
    ├── statistics/          # four_stage_data_statistics.{json,csv,md}
    └── four_stage.validation.json
```

## Honesty invariants

* **Both checks must be run and both must say PASS.**
  `checks/validate_four_stage.py --config <cfg>` covers headers, canonical key
  order, the NaN↔`*_valid` contract, the stage input whitelist, route-DEF
  leakage, shared base/labels and the auxiliary-edge stage policy.
  `checks/summarize_four_stage_graph_data.py --root <generated>` re-reads every
  tensor and must report `structural_issues=0`. A built `heterograph.pt` is not
  evidence on its own.
* **A missing label source degrades one column, never the dataset.** Absent
  timing or IR-drop sources leave `y` NaN with `y_valid_mask` false; they must
  never become 0.0. `04` enforces `isfinite(y) == y_valid_mask` per node type
  and refuses the graph on a mismatch.
* **Labels are raw physical values** — no log/normalization/clipping. That is
  the opposite of the b–f views' `y`/`y_raw` twin convention; do not "align"
  them.
* **Timing and RC relations are supervision, not input.** Their `edge_attr` is
  `[E, 0]` by construction. Only the three logical incidence relations (plus
  `congestion_geom` at cts/route) belong in a message-passing encoder.
* **The congestion grid must be one value everywhere.** Features, labels and the
  Gate-Gate relation all resolve it through `resolve_congestion_grid_um(cfg)`;
  `04` hard-fails when the feature and label grids disagree, and the validator
  re-checks the DBU step against `dbu_per_um`.
* `cell_type_id` / `pin_layer_id` are **per-platform**. Never mix platforms in
  one dataset index without filtering on `platform`.

## Known limits on this machine

* **IR drop is unavailable.** R2G2.0 solves the VDD network from a PDNSim SPICE
  export (`VDD_extracted.sp`); this OpenROAD build's `analyze_power_grid` offers
  only `-voltage_file`/`-error_file`/`-em_outfile`. `ir_drop_mV` is therefore
  NaN with `irdrop_valid=0`, and the runner says so. Pass `--irdrop-sp` if a
  build that can export one becomes available.
* Timing coverage is bounded by `R2G2_MAX_PATHS` (default 10000 endpoint paths
  per corner). Uncovered pins keep `setup_valid`/`hold_valid` at 0 rather than a
  fabricated slack.
* Upstream's optional cross-checks (`rc_label_dir`, `irdrop_reference_csv`) are
  wired in the config schema but not produced by our flow; they only ever
  cross-verify, never source a label.
