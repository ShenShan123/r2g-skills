# Cross-stage net lineage study (2026-11-07)

Code: `lineage_check.py` (per design), `lineage_summary.py` (pooled; written on 203 as
`code/lineage_summary.py`), and `hpwl_split_check.py`.
Data: `203:/data/yangao/exp4_validation_20261107/lineage/`, `lineage_summary.json`, `hpwl_split/`.

The truth is independent of the builder. In every stage DEF (2_floorplan, 3_place, 4_cts,
5_route), physical nets are clustered through backend-added 1-in/1-out cells: BUF or INV
instances that are not in the reference netlist, identified by connectivity and Liberty function,
not by name pattern. Each cluster is anchored by its reference net names, with the A3
backend-clone rule. Two reference levels are used:

- **group:** 1_2_yosys.v merged through synthesis non-inverting buffers. These are the
  dataset's fixed-buffer WL groups.
- **individual:** 2_floorplan.def canonical nets. These are the graph's Net nodes.

## Checks (43-design sample)

| Check | Result | Example | Cause | Fix | After |
|---|---|---|---|---|---|
| 1a. One-to-many, group level: dataset `verified_group_to_stage[S].stage_nets` vs truth pieces | **496,079 / 496,099 groups exact at every stage** (floorplan, place, CTS, route). 0 physical nets claimed by two groups. Truth splits: 48,229 nets split at route, max degree 722 (a clock tree). | — | — | — | — |
| 1a'. The 20 non-exact groups | The dataset has empty stage_nets and the group is **masked** (`missing_verified_driver`, `unverified_or_deleted_endpoint`); the truth finds 1–6 pieces. | C03319 `HEX1[1]`; C00198 `_1293_`→`sram_ce_bar` (constant-driven ports; ORFS tie cells `LOGIC0/1_X1`) | SPEC: LABEL_SPEC "复杂常量源重建…未验证项不能填零或强行归组" — a documented exclusion, not a defect | none | masked as documented |
| 1b. WL label aggregation over pieces | V1 L1: 460,875 / 460,875 resolvable groups equal Σ truth pieces (max 3.6e-12 µm); 0 double counts; group total ≤ DEF total in 43/43 | — | — | — | — |
| 1c. Route-stage feature aggregation over CTS pieces | `stage_net_count` exact on 521,155 nets; 913 non-exact are all dataset `stage_lineage_valid = 0`. HPWL over split nets (Σ per-piece HPWL): release 205 / 1,047 match, **fixed-builder copies 1,047 / 1,047** | C02816 `i_csrbus_strobe[1]` (2 pieces): release 12.83 vs truth 13.30 µm | D-A1 (pin orientation), not lineage | `builder_fix.diff` hunk 1 | 1,047 / 1,047 |
| 1d. Individual level, route (`net_lineage.json.gz`) | 521,155 exact; 913 non-exact, all **dataset-masked**; 1,000 unresolved in the truth (excluded) | C00180 `_3328_` (truth piece `_3328_`, dataset none, masked) | Many-to-one merges: synthesis buffer retired, so two canonical nets share one physical net. Spec: the v2 label uses groups exactly for this case; the individual file is "reference only". | none | — |
| 1e. Clock nets split by CTS | Pieces equal in truth and dataset for every clock net (4–722 pieces). Group valid in all but 1 design. | C01423 `clk`: 576 pieces, group masked (1 of 3,046 endpoints unverified) | SPEC: conservative mask | none | — |
| 2. Synthesis connectivity preserved | Graph nodes = floorplan canonical set in 43/43, identical at both stages. 0 inserted buffer/CTS/fill/tap/diode cells became nodes. 215 floorplan gates are not in 1_2_yosys.v and 24 synthesis instances are not in the floorplan; **all 215 are LOGIC0/1_X1 tie cells** that ORFS inserts or renames at floorplan (`_2514_` → `_2515__1`). | C03319 `_2515__1` LOGIC0_X1 | SPEC: the docs define canonical = real floorplan DEF, not 1_2_yosys.v; the difference is tie cells only | none | — |
| 3. Cross-stage alignment | 43/43: identical gate/net/pin/IO name order in placement and route, all views equal B, WL group mapping and `label_grid_id` equal across stages and views | — | — | — | — |
| 3'. Renamed nets | Escapes: 0 collisions. `/`→`.`: S-A1, 274 nets in 3/500 designs, 0 collisions. Bus bits: `[0:0]` bare-bus case found in our parser (A4), not the dataset | C00180 | SPEC-AMBIGUITY (S-A1) | — | — |
| 4a. Many-to-one merges | 1,521 physical route nets in 43 designs contain ≥ 2 canonical nets; 27,240 canonical nets have no own piece at route (merged into a neighbour). The group label covers all of them. | C02544: 1,249 merges | design behaviour; handled by groups | — | — |
| 4b. Nets removed by optimisation | No group is lost: only 20 constant-driven groups are masked | — | — | — | — |
| 4c. Physical-only nets | 0 unanchored after the clone rule (A3) | — | — | — | — |
| 4d. IO feedthrough | Group lineage exact (the input→output port net is one group). The route-stage net feature counts were wrong: **D-A2**, already fixed | C02816 `i_csrbus_address[0]` | DEFECT | `builder_fix.diff` hunk 2 | fixed copies pass |

**Conclusion:** the builder's one-to-many handling is correct wherever it declares a value valid.
Every disagreement with the truth is on an entry the dataset itself masks, for documented reasons.
The only real errors are D-A1 and D-A2, already in `builder_fix.diff`. No new hunk was needed.

## def-graph skill (read, not changed)

- **b–f views (`scripts/extract/graph/build_graphs.py`).** Identity is the final 6_final.def. Physical
  nets ARE the graph nets, so one-to-many does not arise; inserted buffers/CTS cells are ordinary
  nodes (not flagged). `netlist_graph.pt` is a separate synthesis-level graph with no labels.
- **Four-stage R2G2.0 path (`scripts/r2g2/`).** It has the same canonical-plus-lineage design
  (`build_base_to_stage_lineage` in `r2g2/02_extract_features.py`):
  - The route WL label sums Route DEF lengths over `stage_nets` and is valid only if all pieces are
    present and the lineage is valid (`03_extract_labels.py` ~l.3037).
  - **Already fixed there:** the FN/FS orientation (`techlib/lef.py apply_orient` and
    `r2g2/02_extract_features.py` l.1043), and the IO net (`r2g2/02` l.2577 uses the canonical `io_map`).
  - **Weaker than the 6-label builder:** transparent cells are chosen by master **name**
    (`"BUF" in master or "INV" in master`), not by Liberty function. Non-transparent backend cells
    (logic clones, tie cells) are ignored, so clone-output nets stay unassigned and their canonical
    net is masked.
