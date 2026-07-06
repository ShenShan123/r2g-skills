# RTL2Graph Integration + Correctness Audit — 2026-07-05

**Branch:** `feat/rtl2graph-integration` (worktree). **Commits:** `4d8e032`
(label fixes), `6b09000` (feature fixes), `69c10e2` (graph stage).
**Verification workspace (machine-local):** `/proj/workarea/user5/rtl2graph_verify/`.

## Task

Integrate the operator-provided `RTL2Graph/` pipeline (ODB→DEF, Verilog→PyG
netlist graph, feature/label CSVs, five graph-dataset topologies) into the
r2g-rtl2gds skill so every completed design can become a GNN training graph
autonomously — verifying RTL2Graph's correctness BEFORE touching skill code.

## Verification method (reusable)

Ground truth via `openroad -python` (OpenDB counts, ITerm directions, sampled
placements/connectivity), `report_wire_length -detailed_route` per net, and
`report_checks` per endpoint — on cordic (nangate45, ORFS leftovers) and
aes_core (sky130hd, fresh campaign run). Then equivalence testing: run the
RTL2Graph originals and the port on IDENTICAL inputs, diff tensors.

## Findings

**RTL2Graph's `feature_test_v3` + `label_test` are stale ancestors** of the
skill's `scripts/extract/{features,labels}` (the skill had already fixed the
sky130 liberty quote-bug, nangate-only `num_layer`, fakeram dead keys, and has
per-platform cell-type vocabulary in `techlib.cell_types`). They were NOT
ported; the skill's stages are the substrate.

**Five new defects** (1–4 also live in the skill's extractors — all fixed;
full detail in `failure-patterns.md` "Dataset-Extraction Silent-Value Defects"):

1. Timing labels lost on EVERY register — STA `get_full_name` (unescaped) vs
   odb `getName` (DEF-escaped) join miss. cordic: 0/56 registers labeled;
   aes_core sky130hd: 5/2476. Fixed → 56/56 and 2476/2476.
2. sky130 `RECT ( dx dy dx dy )` patch groups parsed as absolute route points →
   wirelength inflated ~100–400× on 1283/30k aes_core nets (1168 µm vs
   OpenROAD's 3.29 µm), congestion "utilization" up to 11×. nangate45 unaffected
   (no RECT in NETS) — which is why the techlib correspondence tests never saw it.
3. DEF PIN direction inverted in `num_drivers`/`num_sinks` (chip-perspective);
   every output-port net was 2-driver/0-sink. Also implemented the hardwired-0
   `connects_macro_flag` via new `techlib.liberty.macro_cell_keys`.
4. `sum_pin_cap_fF` summed the driver's `max_capacitance` (a drive LIMIT) into
   net load (62.54 fF vs true 3.19 fF) — new `get_pin_load_cap_fF`.
5. RTL2Graph c–f variants misaligned `edge_attr`/`edge_type`/`edge_y` with
   `edge_index` ([fwd|rev] concat vs pairwise attr repeat): 171/3001 sampled
   pin-edge attrs aligned on cordic. The port interleaves fwd/rev → 3001/3001.

**Verified-good:** base_garph Verilog parser exact vs OpenDB (cells, nets,
per-net connectivity); odb_to_def; b-variant assembly (node counts vs
independent recomputation, feature/label joins by name, edge-type closure);
name-escaping consistent across all CSVs (DEF-escaped everywhere).

**Also noted:** RTL2Graph's `base_graph` input was DEAD in single-case mode
(loaded, never used) — graphs are DEF-derived; the base_garph nangate45
hardcoded cell map assigned per-PROCESS dynamic ids off-platform (corpus
inconsistency) — replaced by the techlib vocabulary in the port.

## What shipped

- `scripts/extract/graph/`: `graph_lib.py` (consolidated core), `build_graphs.py`
  (variants b–f + manifest), `netlist_graph.py` (bipartite netlist graph,
  techlib vocab, names attached), `odb_to_def.py` (utility).
- `scripts/flow/run_graphs.sh` — stage 13d: staleness-aware auto-run of
  13b/13c, `R2G_GRAPH_PYTHON` torch-venv probe with fail-soft SKIP,
  `<project>/dataset/*.pt` + `reports/graph_dataset.json`.
- Tests: `test_graph_stage.py` (9), `test_feature_semantics_fixes.py` (6),
  RECT cases in `test_techlib_def_parse.py` (3). Suite: 964 passed / 16 skipped.
- Docs: `references/graph-dataset.md` (new), dated correction notes in
  `references/{feature,label}-extraction.md`, SKILL.md step 13d, CLAUDE.md row.

## Operator actions still open

- **Regenerate any pre-2026-07-05 label/feature CSVs before training** —
  timing/wirelength/congestion/num_drivers/sum_pin_cap values changed (were
  wrong). Same for the machine-local byte-baseline
  (`tools/regen_extract_baseline.sh`).
- The torch venv used here: `/proj/workarea/user5/pyenvs/rtl2graph` (torch
  2.12.1+cpu, PyG 2.8.0, pandas 3.0.3) — set `R2G_GRAPH_PYTHON` to its python.
- `RTL2Graph/` stays an untracked reference copy at the repo root (252 MB with
  sample outputs; not committed by design).
- Corpus-level batching (a `tools/run_graphs_batch.sh` + dataset aggregation
  across designs with real per-design `graph_id`s) is the natural next step.

## 2026-07-05 (second pass): sky130-focused verification round — 2 more silent-value defect chains (commits 0574308, f1302ee)

A same-day independent verification round (fresh audit of topology conversion,
techlib/LEF parsing, feature + label extraction on sky130) confirmed the
port's topology is exact (b-variant node/edge counts + spot connectivity +
netlist-graph counts re-derived independently; c-variant edge_attr alignment
4000/4000) and found two defects the first pass missed:

1. **sky130 quoted liberty pin attributes** (`0574308`): `direction : "input";`
   and `clock : "true";` never matched the unquoted-only regexes — every sky130
   std-cell pin lost direction (pin_type_id 95% collapsed to 14; num_drivers
   always the assume-1 fallback; 390 nets + 1,065 pin-cap sums provably wrong
   on aes_core). Supersedes the "equivalence-proven" caveat: the port faithfully
   reproduced features that were themselves degenerate on sky130.
2. **Interrupted-irdrop raw-CSV chain** (`f1302ee`): the 09:03 validation run's
   labels stage had been killed mid-irdrop; the RAW PDNSim dump sat at
   labels/ir_drop.csv and the shipped aes_core dataset carried y2 100% NaN
   under manifest status "ok". Four honesty gaps fixed: atomic publish
   (extract_irdrop.tcl), stats "invalid" status, manifest `label_health` +
   `ok_with_label_gaps`, completion-marker freshness in run_graphs.sh. Plus
   loud duplicate-key guards on every graph-side label/feature join.

**Supersedes above:** "Suite: 964 passed" → 983 (torch venv) / 968 (stdlib);
the "Regenerate pre-2026-07-05 CSVs" action now extends to the 2026-07-05
morning aes_core dataset itself (all-NaN y2 + collapsed pin_type_id) — the
corrected reference dataset lives at
`/proj/workarea/user5/rtl2graph_verify/aes_core_fixcheck/` until the corpus
regeneration runs. Detail: failure-patterns.md "Dataset-Extraction
Silent-Value Defects" #5/#6.

## 2026-07-05 (wave 2 addendum): three more defects from the agent audits (commit dd88a0b)

The parallel audit agents' full reports landed after the first addendum and
surfaced three MORE confirmed silent-value defects, all fixed + re-validated
end-to-end on the aes_core_fixcheck copy (failure-patterns #7/#8/#9):

1. **Congestion vertical-demand transposition** (all platforms, latent since
   the RTL2Graph ancestor): vertical demand keyed `(y,x)` vs the `(x,y)`
   convention — 79.7% of aes_core congestion labels wrong (mean |Δ| 0.052,
   max 0.323). The demand grid had zero test coverage; directional tests added.
2. **capacitive_load_unit quoted "pf"** (sky130): cap_scale_ff stayed 1.0 →
   every sky130 pin cap 1000× too small. The 0574308 quote sweep missed this
   sibling — lesson upgraded to "sweep the whole file in one pass".
3. **parse_nets dropped `+ USE` on dash lines** (all platforms): `use`
   populated for 1,666/30,345 nets (line-wrapping artifact) → 30,345/30,345.

Independently re-verified by the features agent vs ODB truth (sink mismatches
390→0; 29,825 nets zero cap mismatch vs a raw ×1000 parse; 0 USE
misclassifications). Also documented as modeling choices (not bugs): timing
I/O paths unconstrained (no set_input_delay/set_output_delay; 4% of aes_core
logic cells), sky130 fill/tap/decap → UNKNOWN ids (physical-only cells), power
iopin-edge rows dangling at CSV level (filtered graph-side). Verified-clean
list: wirelength exact vs report_wire_length (0.00µm on non-RECT nets), timing
join complete (2,476/2,476 registers), RECT strip complete + single-sourced,
GCELLGRID/units, LEF layer parse, resolve/profile paths.

**Final state:** suite 986 passed / 0 failed; corrected reference dataset at
`/proj/workarea/user5/rtl2graph_verify/aes_core_fixcheck/` (congestion labels
changed for 25,196/29,712 graph gates; pin-cap x-features ×1000; y2 populated;
manifest label_health all-ok). The live-tree `design_cases/aes_core`
labels/dataset and ALL pre-wave-2 sky130 CSVs remain stale — regenerate before
training (congestion #7 affects every platform).

## 2026-07-06 final triage (features-audit report closed)

The features agent independently confirmed all three wave-2 fixes complete
(direction: sink mismatches 390→0, pin_type_id full distribution; cap unit:
zero magnitude mismatches over 29,825 nets vs an independent raw-pf×1000
parse, incl. the SPEF-io component on iopin nets; USE: 0 clock/reset
misclassifications vs ODB sigtype) and declared the quoted-attribute bug class
closed (all parser-read attributes swept). Residual triage:

- VDD/VSS dangling iopin→net CSV rows: verified harmless graph-side — the rows
  carry power/ground net_type_id, the signal-only filter drops them, and the
  built b-graph contains zero net nodes outside nodes_net.csv (no phantoms).
  CSV-level caveat stays documented in failure-patterns.
- **OPEN verification gap:** `connects_macro_flag` is synthetic-tested only —
  aes_core has no macros, and the current design_cases corpus is sky130-only.
  Exercise it on a real fakeram45 (nangate45 macro) design when one is next
  run before trusting the flag in training data.
- fill/tap/decap area-0/UNKNOWN on sky130: stays a documented modeling choice;
  optional future enhancement = backfill physical-cell area from LEF SIZE.

## 2026-07-06 breadth round: 9-design verification campaign — ALL PASS; verifier promoted to tools/

Extended the single-design (aes_core) verification to a diverse 8-design batch
(read-only copies from the live design_cases into
`rtl2graph_verify/batch_verify/`): DMA_fsm (159 cells), Uart_RX (832),
axil_register (2,331), RISCyMCU CPU (6,342), aes_sbox (12,671, PURELY
COMBINATIONAL), usb_cdc_top (15,608), axi_cdma (41,875, escaped AXI bus
names), sha256_core (76,822). Full pipeline (features→labels→graphs) ran clean
on all 8; the new generalized verifier (`tools/verify_graph_dataset.py`)
passed **54/54 checks on all 9 designs** (incl. aes_core). New ground covered
vs. the earlier audits:

- **d/e/f clique-variant edge construction verified for the first time**
  (formula-based expected counts Σ C(k,2) over per-net/per-gate unique
  endpoints) — on all 9 designs.
- f-variant edge_attr == connecting-net features (sampled, unambiguous pairs).
- EXACT expected-NaN label-join accounting for every y slot × variant (not
  just floors), plus sampled value equality.
- Combinational honesty: aes_sbox correctly yields in_path=0/12671 timing rows
  and all-NaN y3 with label_health ok.
- Extractor-level wirelength truth re-confirmed on 3 more designs vs
  `report_wire_length -detailed_route`: 18/18 sampled nets within tolerance
  (most exact to 0.01um), incl. an escaped bus-name net.
- labels_stats across all 8: all four label sets ok; has_irdrop and in_path
  fractions scale plausibly with design size.

No new defects found — first all-clean round after the wave-1/wave-2 fixes.
Per-design JSON + logs: session scratchpad `vr_*.json` / `vlog_*.txt`;
datasets kept at `rtl2graph_verify/batch_verify/` (machine-local).

## 2026-07-06 nangate45 round: 5 NEW defect chains (#10–#14) + wide-coverage verifier (commits 5b5414e, e5854fe)

Third verification round, this time on **nangate45** (the sky130 rounds above never
exercised macro libs, the curated map, or a second dbu). Method: 2 parallel audit
agents (techlib-vs-real-nangate45-files; extractors/runners) + 10 fresh nangate45
flows in the rtl2graph-integration worktree — the 9 sky130-verified designs as
cross-platform twins (CORE_UTILIZATION=35, tiny designs on a 60×60 die floor after
PDN-0185) + **mem_soc_fakeram**, a purpose-built 2-SRAM fakeram45 macro design that
finally exercises CLASS BLOCK / bus() / ADDITIONAL_LIBS paths.

**Defects found and fixed (5b5414e; failure-patterns.md #10–#14):**
- #10 `connects_macro_flag` ≡ 0 on every macro design, ALL platforms — ORFS's resolved
  `LIB_FILES` already contains `ADDITIONAL_LIBS`, so `R2G_SC_LIB_FILES` ⊇ macro libs and
  `macro_cell_keys()`'s subtraction was ∅. Live-verified post-fix: 237/1,248 mem_soc nets
  flag=1 == DEF∩LEF-BLOCK truth.
- #11 liberty `bus()`/`bundle()` groups unparsed → every macro bus pin type-14/cap-0
  (scalar pins of the SAME macro were fine — plausible-looking corruption).
- #12 **nangate45 curated cell-type map RETIRED** — 22 live masters missing (all
  SDFF*/CLKGATE*/TLAT/AOI222_X1/…→UNKNOWN=95), 4 stale keys, 8/23 fakeram sizes;
  nangate45 now uses the runtime liberty map like every other platform; macro cells get
  a dedicated shared MACRO id (=UNKNOWN+1). SUPERSEDES the audit-note above that called
  the curated map a "platform asymmetry" feature, and the 2026-07-05 "IDs 0–128
  preserved byte-for-byte" contract in feature-extraction.md — nangate45 datasets built
  on curated ids must be REGENERATED (they were already invalidated by #7).
- #13 metadata `tracks_per_layer` pipe-string → `global_feat[12]` 0.0 on EVERY platform
  (now numeric mean + `tracks_detail` column).
- #14 FA/HA sum output `S` classified "select" (now direction-guarded); `statetable()`
  ICGs now sequential.

**Verifier promoted to complete verification infra (e5854fe):** extended_checks()
re-parses raw liberty/LEF/DEF with INDEPENDENT local parsers and closes the
CSV↔tool-truth gap the 54-check version left open (it verified CSV→tensor only — which
is exactly why #10/#11/#13 sailed through the 2026-07-06 sky130 ALL-PASS above):
X-value truth (area/leakage/placement/orientation/type-id injectivity+MACRO id/pin
caps/net counts+dirs+hpwl+macro-flag/iopin/metadata), Y-value truth (FULL independent
congestion recompute; wirelength DEF walk + log1p; timing sequential coverage; irdrop
header/range/log1p(IR/P95)), structural gates (edge symmetry, self-loops, per-block
name uniqueness), platform-generic netlist count (old regex was sky130-hardcoded →
"regex 0" on nangate45) + sampled port connectivity, and `--batch` corpus sweeps.
Result: **ALL-PASS on all 10 nangate45 designs (84–87 checks each)** after the fixes;
helper parsers pytest-pinned (`test_verify_graph_dataset_helpers.py`; suite 974+20).

Store: all 12 flows ingested (2 honest PDN-0185 `fail` rows with
`orfs-fail-floorplan-PDN-0185` events + 10 pass), honesty gates 5/5 GREEN, heuristics
re-derived (118 families). Projects live in the WORKTREE's `design_cases/` (gitignored,
machine-local).
