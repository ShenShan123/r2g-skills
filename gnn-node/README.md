# R2G downstream node prediction

## Supported entry point

Use `stage_train.py` for the new four-stage experiment. It reuses `model.py`'s
`GraphHead`, GINE layers and MLP head with a named-schema feature encoder.
`main.py`, `dataset.py`, `sampling.py`, `downstream_train.py` and the old shell
sweeps remain legacy entry points, not approved for the new experiment. They
still assume the old tensor layout and split conventions. Do not launch the
old tmux sweeps: they can kill an existing same-name session and use several GPUs.

The node head now supervises only valid seed nodes and GINE has trainable epsilon.
The legacy training loop's `y != -1` checks are not suitable for normalized
targets; the new runner does not use that loop.

## Task contract

| Stage argument | Information available | Target |
| --- | --- | --- |
| `cts` | Post-placement, before CTS | Post-route net wirelength or gate congestion proxy |
| `route` | Post-CTS, before routing | The same post-route targets |

The primary tasks are `wirelength` (net, micrometers) and `congestion` (gate,
dimensionless). `ground_cap` (net, pF) remains supported as an optional task.
Input library sink capacitance (fF) differs from post-route ground capacitance.

Congestion is a local **routing utilization proxy**, not a probability, actual
router overflow, or a percentage of congested gates. It is the maximum of
horizontal/vertical routed centerline length divided by nominal LEF-derived
grid capacity, assigned by the post-route gate origin. It does not model
blockages or router capacity adjustments; values can exceed one. Missing gates
remain masked, not zero. Patch-rectangle offsets are not centerline points.

- Input fields are explicitly listed in `stage_data.py`; no graph ID is encoded.
- Message passing uses gate-pin, pin-net and IO-net logical incidence and their
  reverse relations. Geometry, timing-path and extracted RC relations are excluded.
- Features and labels retain independent validity masks. Unavailable features
  become standardized zero plus a missingness indicator; unavailable labels
  never become training targets. A normalized target of -1 is still valid.
- Category vocabularies and numeric/label normalization fit training graphs only.
  The current adapter requires one consistent encoding-map hash across designs.
- Target transform is `log1p(raw)` then training z-score; metrics invert it to
  raw units. Constant training columns use unit scaling, not tiny denominators.
- Same repository or identical normalized source closure shares one split.
  This is a preliminary grouping, not a substitute for family review.

## Inventory and pilot

Run from this directory with the `gnn_env` Python interpreter:

```bash
python stage_train.py prepare \
  --cohort /path/to/cohort.json \
  --graph-root /path/to/methods/r2g-frozen-v3 \
  --output /path/to/inventory.json
```

The inventory hashes tensors and PASS validation evidence without rewriting any
source artifact. For a small separate pilot, add `--pilot-designs 6` and use a
different output path. Inventories refuse replacement. A source group is not
automatically asserted to be an independently reviewed family.

Before congestion training, run `congestion_audit.py --manifest ... --output ...`
and use its new `manifest.json`. It independently recomputes labels and grid
membership from raw DEF/LEF and records hashes for both input and output evidence.
It currently supports rectilinear DEF and radius-zero labels. The original
24-design labels predate the RECT parsing fix and must be re-exported before
formal training; `refresh_graphs.py` creates a separate export from existing
raw inputs, with unchanged design splits and no physical implementation rerun.

```bash
python stage_train.py train \
  --manifest /path/to/pilot_manifest.json \
  --output /path/to/pilot/cts_wirelength_seed42 \
  --stage cts --target wirelength --model gine \
  --pilot --epochs 2 --device cpu
```

Pilot mode uses at most 128 target seeds per training design per epoch, does not
open test graphs, and cannot finalize test scores. Pilot validation metrics are
software diagnostics, not estimates of final task accuracy.

## Training and evaluation

The first backbone uses three GINE layers, per-node LayerNorm, dropout 0.1,
two-layer MLP readout, SmoothL1 loss, Adam and gradient clipping at 1.0. These
settings must be selected before formal testing. `--model mlp` removes message
passing while retaining the same schema encoder and input fields.

Training samples neighbors; evaluation uses all neighbors at each layer and
asserts that every valid target is scored exactly once. Reports contain pooled
and per-design metrics plus macro design MAE. R2 is null for constant targets.
Congestion also reports occupied-grid metrics: average gate predictions within
each design/grid, count each occupied grid once, and report macro design MAE.
The grid covers only valid canonical gates, not empty regions of the die.
Final grid coordinates are label-only metadata and never enter model inputs.
Congestion selects the best checkpoint by validation grid macro MAE; other
tasks retain validation scaled loss. Node-level errors remain visible alongside
grid metrics. Training still supervises individual valid gates.
The HPWL baseline reports its own valid subset; compare against GNN on the same
subset before drawing conclusions. RC edge-row counts in inventories are storage
counts, not necessarily unique physical pair counts.

Before formal training, review family grouping and data eligibility, finalize
the corpus and split independently of test performance, and record
`family_reviewed: true` in a new reviewed manifest. Do not promote a pilot subset
to a formal dataset. Use the same reviewed split for both stages, both targets,
GNN/MLP and all training seeds. The initial 24-design inventory is not a claim
that every qualified design from experiment one has a complete dataset.

Isolate an allocated GPU explicitly, for example:

```bash
CUDA_VISIBLE_DEVICES=1 python stage_train.py train \
  --manifest /path/to/reviewed_manifest.json \
  --output /path/to/formal/cts_wirelength_gine_seed42 \
  --stage cts --target wirelength --model gine \
  --epochs 100 --seed 42 --device cuda:0
```

This is an example, not an instruction to claim GPU 1 without checking availability.
Use unique output directories for stage/target/model/seed. A nonempty directory
requires `--resume`; mismatching code, input or configuration refuses resume.
Add `--resume --finalize` after development is complete to evaluate the held-out
test set with the best validation checkpoint. Do not tune after that evaluation.

Checkpoints include optimizer, preprocessing state, best and current model,
Python/NumPy/Torch/CUDA RNG states and epoch history. Recovery resumes at the last
complete epoch; an interrupted partial epoch is repeated. No background monitor
or automatic GPU-training scheduler has been installed.

## Verification

```bash
python -m pytest tests/test_stage_pipeline.py -q
```

Tests cover split/source isolation, masks and zero-valued labels, physical-unit
round trips, unavailable coordinates, relation whitelist, unknown categories,
constant-column scaling, seed-only supervision, singleton batches, metric
accounting and exact CPU resume equivalence after a simulated interruption.
