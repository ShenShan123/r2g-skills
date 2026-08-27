# def-graph References — Wiki Index

This `references/` directory is the deep-dive companion to the **def-graph** skill — the
dataset-construction half of the RTL→GDS→Graph pipeline. `../SKILL.md` (the workflow) points
into these when you need detail. The physical-design inputs these stages consume (the signed-off
`6_final.def`/`.odb`/`.spef` and platform liberty/LEF) are produced by the sibling **signoff-loop**
skill; see `../../signoff-loop/SKILL.md`.

## By task

**Build the dataset from a signed-off backend run**
- [`label-extraction.md`](label-extraction.md) — the **Y** side: `run_labels.sh` emits per-cell /
  per-net regression targets (congestion, wirelength, timing, IR drop) + per-design stats.
- [`feature-extraction.md`](feature-extraction.md) — the **X** side: `run_features.sh` emits
  per-node / per-edge / graph feature tables + per-design stats. Reads the same `6_final.def` as
  the labels, so rows join.
- [`graph-dataset.md`](graph-dataset.md) — training-ready PyG graphs: `run_graphs.sh` joins X+Y
  into the five b–f graph topologies + the synthesis-netlist graph. Tensor schemas, RC-parasitic
  label views, torch-venv setup, and the 2026-07-05 RTL2Graph audit record.

**The tech-lib / LEF / DEF parser**
- `../scripts/extract/techlib/` — the shared per-platform tech layer every stage consumes
  (`profile.py`, `resolve.py`, `def_parse.py`, `lef.py`, `liberty.py`, `cell_types.py`).

**Verify a built dataset**
- `../../../tools/verify_graph_dataset.py` (repo-level) — re-parses raw DEF/LEF/liberty + OpenDB
  independently and checks the tensors against ground truth (`--batch` over many designs). Run it
  after any corpus regeneration.

## All docs

| Doc | Purpose |
| --- | --- |
| [`feature-extraction.md`](feature-extraction.md) | The dataset **X** side: `run_features.sh` per-node/per-edge/graph feature tables plus per-design stats. |
| [`graph-dataset.md`](graph-dataset.md) | Training-ready PyG graphs: `run_graphs.sh` joins X+Y into the five b–f graph topologies + the synthesis-netlist graph (torch venv, fail-soft). |
| [`label-extraction.md`](label-extraction.md) | The dataset **Y** side: `run_labels.sh` per-cell/per-net regression-target tables plus per-design stats. |

> `env.local.sh.template` in this directory is a **config sample**, not a doc — copy it to
> `env.local.sh` to pin tool/PDK paths that override `../scripts/flow/_env.sh` autodetection.

## See also

- `../SKILL.md` — the def-graph workflow (labels → features → graphs) and hard rules.
- `../../signoff-loop/SKILL.md` — the flow that produces the signed-off DEF/LEF/SPEF this skill reads.
- `../../../CLAUDE.md` — project orientation + architecture.
