# rtl-acquire

Fourth r2g-skills sub-skill: discover, screen, and acquire RTL at **corpus
scale**, then expand it — **synthesis-only** — into pre-layout
`netlist_graph.pt` PyG graphs with dedup, quality scoring, and publish gating.

Ingested 2026-07-09 from the standalone `nangate45-graph-expander` skill under
a scoped-reuse contract (see `SKILL.md` "The scoped-reuse contract" and
`docs/superpowers/plans/rtl-acquire-ingestion-2026-07-09.md`): the acquire
front-end and corpus publish machinery are skill-owned; env/toolchain, ORFS
synthesis (`signoff-loop/run_orfs.sh`), the graph format
(`def-graph/netlist_graph.py`), and failure learning
(`signoff-loop/knowledge/knowledge.sqlite`) are converged onto the sibling
sub-skills. The legacy 30pt/AutoGraph converter is retired.

`SKILL.md` is the operating contract (stage order, hard rules, success
definitions). Use this `README.md` for structure and navigation.

Setup on a new machine: run eda-install (`bash r2g-skills/bootstrap.sh`) —
it pins `references/env.local.sh` for this skill too. Verify resolution with
`python3 scripts/skill_env.py`.

## Directory Layout

- `SKILL.md` — operating contract
- `scripts/`
  - orchestration drivers (top level): `run_expansion_round.py`,
    `run_until_empty.py`, `search_and_expand_until_target.py`
  - `scripts/skill_env.py` — env resolution (thin delegate over the shared
    `scripts/flow/_env.sh`)
  - `scripts/flow/_env.sh` — byte-identical copy of the shared r2g env helper
  - `scripts/acquire/` — discovery / search / clone / screen
  - `scripts/execute/` — `expand_candidates.py` (synth via run_orfs.sh +
    graph via netlist_graph.py + knowledge ingest), `graph_stats.py`
  - `scripts/knowledge/` — `project_frontend_diagnosis.py` (journal→knowledge
    projection + the fast honesty check)
  - `scripts/repair/` — classification + deterministic fixes + LLM queue
    (LLM execution default OFF)
  - `scripts/validate/` — duplicate audits + publish readiness gate
  - `scripts/publish/` — publish candidates, merged corpus manifest, snapshots
  - `scripts/report/` — repo/design quality scoring, corpus scale report
  - `scripts/hygiene/` — corpus cleanup/dedup maintenance
  - `scripts/common/` — io/state/manifest helpers
- `references/` — policy JSONs + curated docs (`operation_matrix.md`,
  `script_index.md`, failure KB/taxonomy, `env.local.sh.template`)
- `tests/` — pytest suite (env resolution, bookkeeping, retry scope, publish
  eligibility, discovery limits, knowledge-ingest convergence)

## Corpus Layout (defaults; all `R2G_ACQUIRE_*`-overridable)

- `<repo>/design_cases/_rtl_acquire/`
  - `_downloads/` — cloned/scanned RTL repos
  - `workspace/` — `candidates/ scan_state/ failures/ quality/ audits/ runs/
    manifests/ synth_projects/`
  - `corpus/` — per-design `netlist_graph.pt` + `mapped_netlist.v` +
    `cell_stats.json` + `design_meta.json`; `index.csv`; `_design_status/`
  - `netlist_graph_corpus_manifest.csv` — the merged manifest (publish-gated)

Per-design live tracing: `corpus/_design_status/<design>.json(.jsonl)` and
stage-oriented stdout lines (`[rtl-acquire] design=<name> stage=... status=...`).

## Quick Start

```bash
# one full round: discover from _downloads, expand, repair, validate, publish
python3 scripts/run_expansion_round.py --discover --run-retry

# keyword-search repos until the corpus hits a target size
python3 scripts/search_and_expand_until_target.py \
  --backend github --keyword "wishbone axi dma" --preferred-size medium

# fast honesty check on the knowledge side
python3 scripts/knowledge/project_frontend_diagnosis.py \
  --check ../signoff-loop/knowledge/knowledge.sqlite
```
