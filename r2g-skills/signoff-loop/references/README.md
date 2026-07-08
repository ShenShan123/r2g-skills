# signoff-loop References — Wiki Index

This `references/` directory is the deep-dive companion to the skill. It holds the long-form docs
that `../SKILL.md` (the workflow and hard rules) points into when you need detail. For the
big-picture orientation and architecture, read `../../CLAUDE.md`; for the memory-database schema,
CLI, and honesty invariants, read `../knowledge/README.md`. This page is just the front door —
start here when you don't know which doc to open.

## By task

**Run a flow RTL → GDS**
- [`workflow.md`](workflow.md) — the phase-by-phase walkthrough (env check → intake → RTL → sim →
  synth → backend → signoff → reports).
- [`orfs-playbook.md`](orfs-playbook.md) — ORFS backend setup, env knobs, macro designs, and the
  Fmax (max-frequency) search.
- [`spec-template.md`](spec-template.md) — the normalized-spec YAML schema + required fields.
- [`ppa-report-guide.md`](ppa-report-guide.md) — how to read PPA / signoff / Fmax JSON outputs.

**Fix a signoff failure (DRC / LVS / timing / route)**
- [`signoff-fixing.md`](signoff-fixing.md) — the iterative real-fix loop (diode insertion, density /
  route relief, LVS) — record → learn → apply.
- [`failure-patterns.md`](failure-patterns.md) — one section per failure mode; look here first when
  a stage is stuck before retrying.

**The closed learning loop & autonomous campaigns**
- [`engineer-loop.md`](engineer-loop.md) — the autonomous campaign driver: flow → ingest → learn →
  A/B-promote → escalate.
- See also `../knowledge/README.md` (the two memory DBs: schema, CLI, full invariants) and the
  "The Closed Learning Loop" section in `../../CLAUDE.md`.

**Datasets / ML feature & label extraction**
- Moved to the companion **def-graph** skill. Feature (X) / label (Y) extraction and the five PyG
  graph views now live in `../../def-graph/` (`SKILL.md` + `references/{feature-extraction,
  label-extraction,graph-dataset}.md`). This skill produces the signed-off DEF/LEF/SPEF those
  stages consume.

**Historical debug narratives & corpus results**
- [`lessons-learned.md`](lessons-learned.md) — dated debug narratives and campaign results.

## All docs

| Doc | Purpose |
| --- | --- |
| [`engineer-loop.md`](engineer-loop.md) | Runbook for the deterministic, resumable campaign orchestrator that drives the full PD flow unattended across a queue of designs. |
| [`failure-patterns.md`](failure-patterns.md) | Catalog of failure modes (spec gaps, placement/route congestion, DRC/LVS, etc.) with symptoms and actions. |
| [`lessons-learned.md`](lessons-learned.md) | Dated lessons from physical-design debugging and batch/campaign runs. |
| [`orfs-playbook.md`](orfs-playbook.md) | ORFS usage playbook: environment autodetection, root layout, env knobs, macro designs, and Fmax search. |
| [`ppa-report-guide.md`](ppa-report-guide.md) | How to read the PPA / signoff / route / Fmax JSON reports and the extraction-script table. |
| [`signoff-fixing.md`](signoff-fixing.md) | The automated, iterative loop that applies real DRC/LVS layout fixes after the backend run (never relaxes the rule deck). |
| [`spec-template.md`](spec-template.md) | The normalized-spec template (YAML schema, minimum required fields, signoff considerations). |
| [`workflow.md`](workflow.md) | The phase-by-phase EDA workflow from environment check through signoff and reporting. |

> `env.local.sh` and `env.local.sh.template` in this directory are **config samples**, not docs — an
> optional place to pin tool/PDK paths that override `scripts/flow/_env.sh` autodetection.

## See also

- `../SKILL.md` — the workflow, hard rules, and env knobs (the skill itself).
- `../../CLAUDE.md` — project orientation + architecture ("The Closed Learning Loop").
- `../knowledge/README.md` — memory-DB schema, CLI, and the full list of honesty invariants.
