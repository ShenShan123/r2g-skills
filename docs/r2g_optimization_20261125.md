# R2G optimisation round, 2026-11-25: Fmax search

**Bottom line.** Three changes to the signoff-loop Fmax search were tested on 203.
- **Lazy confirm grid.** Its output was identical to the old grid on every design, and the search spent 29 % less time running ORFS. It is now the default.
- **ECP refinement.** One extra full flow per design raised the full-flow-verified Fmax by a median of **+20.6 %** on nangate45 (28 designs) and **+7.6 %** on sky130hd (15 designs).
- **Learning from `--verify` results.** This path had been silently turned off. With it switched on, the verified Fmax on held-out designs rose by **+11.8 %**, with no extra flows.

While running the A/B the work also found and fixed four latent bugs in `--verify`. Codex (GPT) reviewed the change, found two real issues (both fixed), and ran the skill end to end on two designs with both options on.

## 1. Techniques surveyed (2024–2026)

The arXiv API was reached through the 208 proxy. WebSearch was not available.

| Work | arXiv | What it does | Fit for R2G |
|---|---|---|---|
| ORFS-agent: tool-using agents for chip design optimisation | 2506.08332 | LLM agent tunes ORFS parameters. It beats OR-AutoTuner (Bayesian optimisation) with 40 % fewer iterations. Its objective is the **effective clock period** (ECP). | Full parameter tuning costs too much per design. **The ECP objective is cheap: adopted as Opt 2.** |
| AuDoPEDA: automated QoR improvement in OpenROAD with coding agents | 2601.06268 | A Codex-class agent edits OpenROAD source code. Gains: up to −10 % effective clock period, −5.9 % wirelength. | Changes the tool itself, which is out of scope. It confirms ECP as the standard figure of merit. |
| StateTune: stateful, closed-loop LLM flow tuning | 2608.23601 | Keeps an evidence-gated persistent optimisation memory and promotes quick-stage candidates by expected gain per unit runtime. Memory is its largest contributor (−58 % hypervolume when removed). | R2G already has the memory (`knowledge.sqlite`). The finding that memory matters most prompted the audit of the verify→learn path. **That audit found the path dead: Opt 3.** |
| OpenROAD AutoTuner (Ray Tune + HyperOpt/Ax/Optuna); multi-fidelity tuning | ORFS `tools/AutoTuner` | Bayesian/random search over flow knobs. It scores with ECP and uses cheap early stages as proxies. | R2G's Fmax search is already a place-stage multi-fidelity proxy. The new idea is to spend fidelity only where it decides the answer: **Opt 1** (lazy confirm). |
| CROP: circuit retrieval + parameter guidance with LLMs | 2507.02128 | Retrieves similar designs with embeddings to warm-start flow-parameter search. | R2G already does k-nearest-neighbour retrieval (`suggest_config.py`, Win 5). Nothing new to add. |
| MCP4EDA: backend-aware synthesis optimisation | 2507.19570 | Feeds post-layout timing back into synthesis scripts. | Already in R2G as the A/B-gated recipe `backend_aware_synth_retune`. Not re-tested here. |
| ChipMEM: verification-grounded memory for EDA agents | 2609.27067 | Ranks recovery strategies with hierarchical Beta posteriors. | R2G's `fix_model.rank_strategies` already uses a Beta(1,1)-smoothed success rate. In `fix_events`, most classes have only one candidate strategy, so re-ranking would change nothing. Dropped. |
| AgentDRC: multi-agent spatial DRC correction | 2611.31876 | LLM agents fix DRC geometry (up to 55.7 % pass@5). | Edits the layout, which conflicts with R2G's real-fixes-only and frozen-flow rules. Not integrated. |
| TicTacBench / TicTacSkill: timing-closure skill for coding agents | 2609.23363 | A procedural skill raises the timing-closure rate by 9 %. | R2G already has many A/B-gated timing recipes in `diagnose_signoff_fix.py`. That file is being edited by another worker, so it was left alone. |
| CLOSER-Bench (2607.16632); "Can AI agents really complete RTL-to-GDS?" (2607.17528); HighTide (2606.04126) | | Benchmarks for agentic RTL-to-GDS work. They report a wide spread in Token ROI between agents. | Useful for evaluation framing. Nothing to integrate. |
| IICPilot (2407.12576); GR-Evolve (2604.22234); ZhuLong-SE (2611.26912) | | LangChain backend agent; LLM evolution of global-routing code; self-evolving scripting memory. | Either covered by existing R2G pieces or tool-internal. |

**Candidates looked at and dropped:**
- KLayout DRC tiling and threads: the decks already have `tiles` and `threads(4)`.
- Parallel label/feature workers in def-graph: already there (`R2G_JOBS`).
- Cost-aware ranking of repair recipes: no class has competing strategies in `fix_events`.
- Synthesis reuse across Fmax probes: synthesis is only about 4 s of a ~20 s probe on these designs.
- A secant root-finder in place of the fixed-point search: the measured slack-vs-period slope is about 0.93, so the existing fixed point already converges in 2–3 probes.

## 2. What was implemented

All changes are in `r2g-skills/signoff-loop`. New behaviour is behind flags, except where noted.

**Opt 1: lazy confirm grid.** It is now **on by default**. Turn it off with `--eager-confirm` or `R2G_FMAX_LAZY_CONFIRM=0`.
- After the root-find, `confirm_grid` probes `t*·{0.98, 1, 1.02}` and returns the *largest passing* period.
- The lazy version probes loosest-first and stops at the first pass. For a deterministic probe this gives exactly the same answer.
- A small memo also skips any place probe at a period already tried in the same search.
- `engineer_loop fmax-drain` inherits the setting through the environment.

**Opt 2: ECP-refined verification.** Opt-in: `--ecp-refine` with `--verify`. Environment: `R2G_FMAX_ECP_REFINE=1`; `--ecp-margin` defaults to 0.02.
- After the verify flow, it runs **one** more full flow at `(period − verified slack)·(1+margin)`. That is tighter when the verify closed with slack, looser after a setup miss.
- No extra flow runs if the gain would be under 1 %, or after a hold miss.
- The tightest period that a full flow closed becomes the `winner`. The report records which flow won: `source` is `ecp_refine_verified`, `verify_confirm_edge`, or `proxy_unverified`. The old proxy result is kept as `proxy_winner`.
- Every full-flow try is listed in `verify.tries[]`.

**Opt 3: learn from verify results.** Opt-in: `R2G_LEARN_FMAX_VERIFY=1` in `learn_heuristics.py`.
- Each `--verify` records a measured floorplan/place/finish slack triple, tagged `eval_arm='fmax_verify'`.
- The learner's P0-14 filter drops every eval-arm row, so these triples never reached the model. The documented "verify self-corrects the deterioration model" was therefore inert.
- nangate45 sat on the static `max(0.10 ns, 1 %)` place→finish erosion floor, which is 20 % of a 0.5 ns period. Its learned pooled model was stuck at n=10, below the 20 needed to use it.
- With the flag, the triples are pooled into the **platform** slack model only, never into family or recipe learning.
- `fmax_search.py` now reads `R2G_HEURISTICS_PATH`, as the other readers already did.

**Bug fixes found along the way** (logged as failure-patterns P0-2f):
- `--verify` crashed after the full flow had already run: the knowledge-store guard refused the write to the shipped store, and the result was lost. This is now a warning.
- An incomplete verify flow (run_orfs exit ≠ 0) could count as closed. Found by Codex.
- When the tighter ECP try missed, the winner was still labelled `ecp_refine_verified`. Found by Codex in its end-to-end run.
- The report now also carries `confirm`, `probe_stats` and `verify` blocks. These are additive telemetry.

## 3. Files changed

These are working-tree changes only; nothing is committed. Other workers' uncommitted files were not touched.

- `r2g-skills/signoff-loop/scripts/reports/fmax_search.py`
  - lazy confirm and memo;
  - `--eager-confirm`, `--ecp-refine`, `--ecp-margin`;
  - `_record_verify` and `_update_report`;
  - `R2G_HEURISTICS_PATH`;
  - robust `verify_winner` (handles a refused knowledge-store write and an incomplete flow).
- `r2g-skills/signoff-loop/scripts/reports/fmax_model.py`: `confirm_order`, `ecp_refine_period` (pure functions).
- `r2g-skills/signoff-loop/knowledge/learn_heuristics.py`: `_fmax_verify_rows` and the opt-in platform pooling.
- `r2g-skills/signoff-loop/tests/test_fmax_search.py`: 13 new test functions (some parametrized).
- `r2g-skills/signoff-loop/tests/test_learn_heuristics.py`: 1 new test.
- `r2g-skills/signoff-loop/SKILL.md` §5a; `references/orfs-playbook.md` (new "Lazy confirm grid and ECP-refined verification" section); `references/failure-patterns.md` (P0-2f).
- `experiments/r2g_opt_20261125/` (note: `experiments/` is gitignored, so this stays local): the experiment code, result tables and the Codex outputs.
  - `mkproj.py` builds projects from the corpus.
  - `fmax_ab_job.sh`, `sky_ab_job.sh` and `armc_*.py/.sh` are the job scripts.
  - `collect_*.py` and `analyze_*.py` produce the tables and statistics.
  - `test_codex_lazy_confirm.py` is Codex's randomized property test.
  - `results/` holds the tables and outputs.

**Tests.**
- Fmax and learn tests: 78 pass.
- Full signoff-loop suite: 1658 pass (2 deselected). The two deselected tests fail on 208 with or without this change, so they are unrelated:
  - `test_knowledge_sync::test_committed_bundle_in_sync_with_db`: the local `knowledge/store/` bundle (gitignored) is stale.
  - `test_orfs_audit::test_observer_isolated_…`: this shell has `PYTHONHOME` set by the oss-cad-suite Python.

## 4. Experimental evaluation (203, ≤16 cores, isolated ORFS symlink farm, private knowledge store)

**Set-up.**
- **Designs:** the 34 smallest sequential designs, 47 to 14k cells, taken from the repro_n45 corpus (the 40 "control" designs plus 40 from the antenna@r1 group, filtered by synthesis size).
- **Project template:** the skill's own `config-template.mk`: `CORE_UTILIZATION` 30, `PLACE_DENSITY_LB_ADDON` 0.20, `ABC_AREA` 1. The SDC follows `constraint-template.sdc`: seed period 2.0 ns on nangate45 and 5.0 ns on sky130hd, 20 % I/O delay, 5 % setup uncertainty.
- **Arms on nangate45:** **A** = legacy `fmax_search.py --verify`. **B** = `--verify --lazy-confirm --ecp-refine`.
- **Scheduling:** each design ran in both arms as separate projects. The job order was randomised (seed 20261125), with the two arms interleaved; 4 jobs ran in parallel with `NUM_CORES=4`.
- **Exclusions:** five designs under about 160 cells abort at place under the template in both arms (`place_probe_inconclusive`), and one design has no constrained path. That leaves **n = 28 paired designs**.

### Opt 1: lazy confirm (nangate45, n=28 paired)

| Metric | Legacy (A) | Lazy (B) |
|---|---|---|
| Identical `t*` / confirm edge / first-verify slack | — | **28/28 / 28/28 / 28/28** |
| Confirm-grid ORFS probes | 84 | **32** |
| All search ORFS runs | 199 | **147 (−26 %)** |
| Search ORFS wall time, sum | 5341 s | **3793 s (−29.0 %)** |
| Per-design saving | — | median **−31.8 %** (bootstrap 95 % CI −29.2 … −32.9 %); faster on 27/28 (sign test p = 2·10⁻⁷) |

The answer was the same on every design while the cost went down, which also shows ORFS is deterministic here. On sky130hd (n=15, arm B only) the confirm grid took 17 probes; the eager grid would have taken 45.

Codex's independent property test checked 20 000 randomized monotone and non-monotone deterministic grids, and lazy and eager agreed on all of them.

### Opt 2: ECP refinement

The baseline is the verified period from the first verify flow at the confirm edge (arm A). The same flow is reproduced exactly in arm B.

| | nangate45 (n=28) | sky130hd (n=15) |
|---|---|---|
| First verify closed | 27/28 (one hold miss, C03614) | 15/15 |
| Median verified slack left at the confirm edge | 0.131 ns (**19 % of the period**) | 0.205 ns (8.8 %) |
| ECP extra flow attempted / closed | 26 / **26** | 15 / **15** |
| Verified-Fmax gain over baseline | median **+20.6 %** (95 % CI +11.5 … +23.4 %), geomean +18.1 %, improved on 26/27 | median **+7.6 %** (CI +2.3 … +11.9 %), geomean +7.6 %, improved on 15/15 |
| Cost | one more full flow: median 70 s, against 75 s for the first verify | median 65 s |

### Opt 3: learning from verify results (nangate45, held-out)

**Split.** The 34 designs were split 17 train / 17 test (seed 20261125). The model was re-learned from the shipped store plus only the **train** designs' verify triples, with `R2G_LEARN_FMAX_VERIFY=1`.
- The pooled nangate45 model went from n=10 (inactive) to n=56.
- Its place→finish erosion `d_pl_fin` went from the static 0.10 ns floor to a p90 of **0.043 ns** or 4.9 % of the period.

**Arm C** = lazy search plus `--verify` using that model (`R2G_HEURISTICS_PATH`), run on the **test** designs and compared with arm A on the same designs.

| Metric | Arm A (static model) | Arm C (learned, held-out) |
|---|---|---|
| Test designs with an ok search | 12 | 12 |
| Verify flow closed | 12/12 | **12/12** |
| Verified-Fmax gain over A | — | median **+11.8 %**, geomean +11.5 % |
| Extra flows | — | **none** |
| ORFS probe runs | 86 | 75 |
| Median slack left at the verified period | 0.152 ns | 0.118 ns |

### Codex (GPT) validation (203, `codex exec`, `codex_home2`, sandbox = workspace-write)

- **v1 review** of the diff plus the unit tests: 55 tests passed, and Codex's own property test passed 200 cases (20 000 grids).
  - Verdicts: lazy confirm **pass**; refused-write handling **pass**; ECP **needs fixes**.
  - It reported (a) a proxy winner that could look verified when no flow closed, and (b) `closed` ignoring a failed flow.
  - Both are fixed, with tests.
- **v2 end to end.** Codex read SKILL.md §5a, built nangate45 projects for `spi_master` and `vga3`, and ran the Fmax search with both options. It then stamped the winner, ran the full flow, DRC, LVS and RCX, and the timing gate.

| | spi_master | vga3 |
|---|---|---|
| Lazy confirm | 1 probe | 1 probe |
| ECP refine | 0.5755 → **0.4473 ns closed (+28.7 % Fmax)** | 0.3957 → 0.2944 ns **missed hold (−0.0014 ns)**, so the winner fell back to the verified confirm edge |
| Final flow at the winner | timing clean, DRC 0, LVS clean, RCX complete, manifest strict-clean | timing clean, DRC 0, LVS clean, RCX complete, manifest strict-clean |

From the v2 run, Codex also flagged three things:
- The winner `source` label was wrong in the fallback case. Fixed: it now reads `verify_confirm_edge`.
- `probe_stats` excludes the verify flows. The docs now say so.
- One skill issue not caused by this change: vga3's default LVS invocation tried to rebuild stages, and passed only with the existing `R2G_LVS_FROZEN=1` retry. This is worth a separate look.

## 5. Recommendations

| Change | Recommendation | Why |
|---|---|---|
| Opt 1: lazy confirm | **Adopt as default (done).** | Output identical on every design; −29 % search ORFS time; proven by 28/28 real designs plus a 20k-case property test. |
| Opt 2: `--ecp-refine` | **Keep opt-in. Recommend turning it on whenever `--verify` is used.** | +20.6 % (nangate45) and +7.6 % (sky130hd) verified Fmax for one extra full flow. 41/41 harness refinements closed, but the tighter period can expose a hold miss (vga3 in the Codex run); the fallback handles it correctly. It changes what `winner` means, so it should stay explicit. |
| Opt 3: `R2G_LEARN_FMAX_VERIFY` | **Adopt (flip to default) once the shipped store is seeded.** | +11.8 % held-out verified Fmax with no extra flows and no loss of closure. The shipped `knowledge.sqlite` has no `fmax_verify` rows, so flipping the default alone changes nothing. Ingesting this campaign's 82 verify triples into the shipped store and re-learning is a curation step for the owner, and was not done here. |
| Bug fixes (P0-2f) | **Keep.** | Each one is a crash or honesty fix, covered by a test. |

The three gains overlap but are not the same thing. Opt 3 removes most of the systematic conservatism of the static model, so ECP's extra flow would gain less on top of it. A reasonable default pipeline is: learned model + lazy confirm + `--verify --ecp-refine` when a verified Fmax is the deliverable.
