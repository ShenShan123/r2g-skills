# Typed Executable Hardware Memory (TEHM)

TEHM is the typed, evidence-backed memory plane for R2G hardware repair. It stores verified execution history, derives typed views, and supports bounded retrieval and source binding. The original R2G memory remains a separate legacy baseline; the two are not interchangeable sources of authority.

This README describes the current repository entry points and the limits of the available evidence. It does not treat an implemented component, a passing clean test, or a DEV demonstration as a verified transfer result.

## Current research status

The active RTL research protocol is [Revision 5](docs/TEHM_R2G_Revision5_RTL测试判定力_受限绑定与独立迁移Pilot方案_2026-09-24.md). The [progress and blockers report](evaluation/research_r5_progress_and_blockers_20260927.md) is the detailed status record. The following is a snapshot of evidence available as of September 27, 2026; this README was updated on September 28.

| Workstream | Established evidence | Open boundary |
| --- | --- | --- |
| RTL qualification | [QF-1-r4](evaluation/research_r5_qf1_and_dev_20260924.md) indexes ten pinned repositories. The specified axis and AES negative controls were detected; the specified UART RX payload error was missed. The old binder matched none of 249 scanned files. | These counts are not ten repair tasks, a mutation detection rate, or binder recall. Each new scope needs its own oracle-sensitivity check. |
| Skid-payload mechanism | The frozen gen6 v8 research generation has a [legally admitted TRAIN Memory](evaluation/research_r5_gen6_m0_v8_20260927.md). TRAIN-side routing/selection is `NO_SKILL / CONSIDER+SELECT / NO_SKILL` for M− / M+ / Mremove, with Mremove equivalent to M−. | This is a TRAIN consumption check, not a repair on an unseen target. The preregistered C1 candidate was [structurally unqualified](evaluation/research_r5_gen6_c1_disposition_20260927.md). |
| I²C NACK-status mechanism | The source-only v2 binder bound three observed DEV fault shapes. Its freecores candidate passed a fresh native Icarus test; adversarial checks and a cold artifact audit passed. [DEV result](evaluation/research_r5_i2c_nack_binding_v2_dev_result_20260927.md) | All three sources have been observed during development. v2 has no admitted TRAIN Memory or fourth, unseen target; its positive DEV result is not transfer or a Memory gain. |
| Agent calibration | One observed DEV task received three authorized DeepSeek V4.1-Flash calls. All three strategies passed with the same candidate; no TEHM-specific benefit was observed. [C7 report](evaluation/research_r5_s2_provider_c7_20260926.md) | This is neither an unseen comparison nor permission for additional provider calls. |
| Paper protocol | The [measurement protocol](evaluation/research_r5_paper_protocol_20260925.md) separates qualification, repair, preservation, source groups, and paired Memory contrasts. A historical gen5 F1 task yielded 0/1 repair in every arm. | The final task list is empty and `final_test_ready=false`; there is no paper-scale repair-rate or ΔMemory claim. |

The skid gen6 TRAIN Memory, I²C v2 DEV action, gen5 F1 task, and C7 provider calibration belong to different software generations or task roles. They must not be combined into a single transfer or attribution result.

The immediate critical path is to admit v2 I²C TRAIN evidence under the same frozen software generation, preregister a fourth source lineage with a qualified native oracle, and run M−, M+, Mremove, and a same-code transform-only control on the same task. All rejection and `UNKNOWN` outcomes remain in their declared denominators. See the [blocker-by-blocker handoff](evaluation/research_r5_progress_and_blockers_20260927.md) for the acceptance gates and evidence-preservation requirements.

## System boundaries

At process start, `R2G_MEMORY_BACKEND` selects `none`, `legacy`, or `tehm` (default: `legacy`). The [factory](factory.py) locks that choice for the process and rejects invalid values. `legacy` reads its own baseline; TEHM does not ingest legacy knowledge as TEHM authority.

The TEHM path is:

```text
verified execution record -> canonical state/transition/episode
  -> typed views -> retrieval -> source binding -> execution
  -> independent verification -> governed update
```

The implementation contains an R2G evidence adapter, canonical capture, typed views, retrieval, bounded activation, physical-effect memory, and research evaluation modules. Production activation remains restricted to `promoted` rules. A candidate or shadow Asset, a RAM-only promotion rehearsal, and a syntactic binder match do not grant production authority. The Parametric View remains shadow-only; do not present it as a materialized production view. Predicate `UNKNOWN` is not `FALSE` or `PASS`.

A canonical memory atom is a verified state transition, `e_t = (S_t, A_t, S_{t+1}, O_t, V_t)`; related transitions form a repair episode graph. Semantic, Diagnostic, Episodic, and Procedural views retain links back to canonical evidence. The fifth, Parametric, is not a production materialization. Evidence is typed by formal, regression, target, and compile/lint scope (`F/R/T/H`), and content-addressed IDs support deterministic deduplication. A missing obligation is `UNKNOWN`, never negative evidence.

The CLI can initialize or inspect a local TEHM store, capture a reviewed `ExecutionRecord` JSON, or import transitions from an R2G project containing `reports/*.json`, `config.mk`, and `fix_log.jsonl`. The old `memory/tests/fixtures` example was removed with the test tree; use [`tehm/canonical/capture.py`](tehm/canonical/capture.py) for the current input contract rather than copying that historical command.

For real RTL experiments, the runner/verifier owns task selection, source and test closure, oracle verdicts, Memory admission, and candidate evaluation. A binder receives only allowed buggy-source text and public context; clean RTL, mutation coordinates, private tests, and other experimental arms stay on the evaluator side. Clean-test PASS establishes executability, not sensitivity to the declared fault. Functional errors may be reported in native logs even when a simulator exits with code zero.

## Repository map

| Path | Purpose |
| --- | --- |
| [`factory.py`](factory.py), [`contracts.py`](contracts.py), backend modules | Backend selection and shared R2G contracts. |
| [`tehm/`](tehm/) | Canonical capture, typed views, retrieval, activation, authority checks, and evaluation code. |
| [`tehm/cli.py`](tehm/cli.py) | TEHM store and inspection commands. |
| [`scripts/r5_phase3b_*.py`](scripts/), [`scripts/r5_phase3_agent.py`](scripts/r5_phase3_agent.py) | Current R5 generation drivers (Phase 3/3b, I²C v3 line); older generations live in the frozen worktrees under `_r5_pilot/software/`. |
| [`docs/`](docs/) | Current R5 research design. |
| [`evaluation/`](evaluation/) | Versioned protocols, receipts, negative results, and historical milestone reports. |

The public Git repository does **not** contain the full external RTL corpus, evaluator-private answers, model credentials, or the raw evidence bundles under `/data1/zhangdy/RTL/RTL_testbench`. Paths in historical reports identify the original local evidence; a new machine must acquire and verify the specified inputs before attempting replay. The frozen gen6 software worktree shares the original repository's Git object store and is not an independent backup.

## Current commands

Run these from the repository root. They are read-only CLI discovery or in-memory conformance checks; `init-db`, capture, crystallization, and experiment runners can write data and need explicit paths and a reviewed protocol.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=memory python3 -m tehm.cli --help
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=memory python3 -m tehm.evaluation.research_r5_rtl_scoped_i2c_v3_checks
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=memory python3 -m tehm.evaluation.research_r5_paper_protocol_checks
```

For a new, explicitly chosen local store (this command **writes** the database and artifact directory):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=memory python3 -m tehm.cli --db /path/to/tehm.sqlite --artifacts /path/to/artifacts init-db
```

To select a backend in an R2G process, set `R2G_MEMORY_BACKEND` to **one** of `none`, `legacy`, or `tehm` before the first backend is opened; switching it mid-run is rejected. `TEHM_DB` selects a TEHM store path, not the backend.

The QF-1 and gen6 verification commands require external frozen artifacts. Use the exact versions and instructions in the [QF-1 report](evaluation/research_r5_qf1_and_dev_20260924.md) and [gen6 M0 report](evaluation/research_r5_gen6_m0_v8_20260927.md); do not run a frozen receipt against a modified main-tree software identity and call it a successful replay. The historical `memory/tests/` tree and many one-off historical scripts were intentionally retired from the current tree; `memory/tests/` now holds only live tests for modules changed by the R2G memory redesign ([implementation record](evaluation/r2g_memory_redesign_impl_20261001.md)): `PYTHONPATH=<pytest target> python3 -m pytest -q memory/tests`. A command quoted in an older milestone is not automatically a current entry point; the 2026-10-02 cleanup ([record](evaluation/tehm_code_cleanup_20261002.md)) kept only the current R5 generation in the main tree. The [cleanup record](evaluation/tehm_tree_cleanup_20260927_r6.md) explains the retained compatibility dependencies.

## Evidence and publication rules

- Record repository commit, ordered RTL/test closure, parameters/macros, toolchain, test IDs or vectors, seeds, declared fault, and verdict adapter for each scope. Preserve raw logs and failed attempts; do not infer repair from candidate/source hash equality alone.
- Keep DEV, researcher-assisted TRAIN, PILOT_TRANSFER, FINAL_TEST, and historical evidence separate. If a source is used to develop a binder or oracle, it is not unseen for that generation. Owner names alone do not prove independent lineages.
- A Memory effect requires actual source-only routing, selection, binding, candidate generation, and fresh target/preservation/native checks on the same preregistered task under M−, M+, and Mremove. Include a same-code transform-only control to separate code capability from Memory contribution.
- Do not promote research/shadow objects to production or reuse a prior provider-call budget. Real model calls require new, explicit scope and call/token authorization.
- Before publishing or deleting unique raw evidence, verify a second content-checked copy—preferably in an independent storage failure domain—and restore a complete case in an isolated path. A Git commit, a Markdown digest, or another directory on the same disk is not sufficient.

The former multi-thousand-line progress diary was removed from this entry page because much of it described superseded commands, deleted test fixtures, or intermediate conclusions later narrowed by audit. Versioned reports under [`evaluation/`](evaluation/) and Git history preserve those details. Read the current R5 protocol and the latest scoped receipts before reusing any historical result.
