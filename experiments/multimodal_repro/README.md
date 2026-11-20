# Reproducing published congestion predictors on the R2G multimodal set (Nangate45)

Purpose (paper appendix): show that the R2G dataset is directly usable for **multimodal (layout + netlist)
learning** by running *published* methods on it, with their released code where it exists, rather than
proposing a new model. Replaces the earlier `experiments/multimodal/` study of our own fusion models
(stopped 2026-11-18 at the authors' request; its SKY130HD/GF180 results stay archived there).

## Data

- 282 Nangate45 designs, each **signed off** (DRC + LVS clean) in the frozen-spec corpus run
  (`rerun_nangate45_20261018`). Selection list: 216:`/proj/workarea/yangao/mm_dataset_n45/select.tsv`.
- Features come from the placement stage: each signed-off `config.mk` was re-run to `3_place.odb` in the same
  container image (`n45_place.sh`, 282/282 OK); labels come from the signed-off `6_final.def`.
- Per design (`experiments/multimodal/mm_prep.py`): 5 layout channels (def-graph `build_images.py`, verified by
  `verify_images.py`), the placed netlist on the same G-cell grid (`graph.npz`: cells, IO terminals, nets,
  pins with in-cell offsets and driver flag), and the label `congestion_raw` = routed max(H, V) demand/capacity
  per G-cell (def-graph `extract_congestion.py`). Range 0–0.59; grids 32–100 G-cells per side.
- Split: repository-grouped, size-stratified 70/15/15 (seed 20261118) → 195 / 39 / 48 designs.

## Methods

| Method | Venue | Inputs | Code |
| --- | --- | --- | --- |
| RUDY + pin RUDY, least-squares fit | DATE 2007 (RUDY) | layout | ours (`run_rudy.py`) |
| GPDL | DATE 2021, CircuitNet model zoo | layout (macro, RUDY, pin RUDY) | official, vendored `upstream/circuitnet/` |
| Lay-Net, layout only | ablation of Lay-Net | layout | official, RGCN stages set to identity |
| LHNN | DAC 2022 | netlist hypergraph + G-cell lattice | **re-implemented** from the paper (no release) |
| VeriHGN | KDD 2026 | netlist (cells, nets, pins) + hierarchical grid | official, vendored `upstream/verihgn/` |
| Lay-Net | ICCAD 2023 / TCAD 2025 | layout (Swin) + netlist heterograph (RGCN) | official, `lanchengzou/congPred` (no licence → not vendored) |

Pinned upstream commits: CircuitNet `41ade1d7e913`, VeriHGN `28a6f1ebbd61`, congPred `5b8bf5645171`.
Each runner's docstring lists exactly what is unchanged and what had to be adapted, and why: our labels stay in
physical units, CircuitNet's iteration budget scales to our training-set size, VeriHGN's c_max follows the
paper rather than the code, and LHNN's class label and G-net cap are adapted to our grids.

Every method is scored by the same function (`bench.score`) on the native G-cell grid of each of the 48 test
designs: R², NRMSE, SSIM, Pearson, Spearman, Kendall, and top-10% hotspot F1. Three seeds per method.

## Questions the study answers

1. Do published multimodal methods train and run on R2G as released, given only a format adapter? (feasibility)
2. Does adding the netlist help on this data? Within one architecture: Lay-Net vs its layout-only ablation.
   Across methods: netlist-aware vs layout-only.
3. Does performance keep improving with more R2G designs? Data scaling at 25 / 50 / 100% of the training split.

## Running

```
RUNS=/home/yangao/mm_runs/n45_repro  ./launch.sh JOBS.txt <gpu> <parallel>   # lines: "<method> <seed> <frac>"
python3 aggregate.py /home/yangao/mm_runs/n45_repro --frac 1.0
```
Environments: `gnn_blackwell` (torch 2.11, PyG, torch_scatter) for GPDL / LHNN / VeriHGN; the DGL venv
`/home/yangao/r2g_toolchain/pyenvs/laynet` (torch 2.4 cu124 + dgl 2.4) for Lay-Net.
