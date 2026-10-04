# R2GSkills — Experiment Plan

Source: abstract + `sections/01_introduction_V2.tex` + Fig. 1 only.
Platforms: sky130, Nangate45, ASAP7. LLMs: Opus, GPT, Qwen, GLM — versions TBD: pick the latest available at P0, record exact API model ID + access date, freeze for all runs (a mid-study model update invalidates E2 comparisons).
Labels: wirelength (WL), HPWL, ground capacitance (Cgnd), congestion.

## Claims → experiments

| Claim | Statement | Experiments |c
|---|---|---|
| C1 | Skill-packaged, deterministic workflow builds graphs from public RTL; logs acceptance/loss per handoff; admits only strict-clean signoff | E1 |
| C2 | Recipes validated on disjoint designs, frozen, reused on same platform → fewer repair attempts + LLM tokens for covered failures | E2, E5 |
| C3 | Stage-restricted inputs; independent identity / visibility / label checks | E3, E4 |
| Implicit | Silent corruption is real and hurts learning | E3 (prevalence), E4 (impact) |

## P0 — Pilot (budget is unknown → size it first)

- 15–20 designs per platform, full flow.
- Measure: CPU-h per design, per-stage failure rate, tokens and $ per from-scratch LLM repair session (per model).
- Output: fill the budget formulas below, then fix N (designs) and F (held-out failures).
- Gate check: confirm a real DRC + LVS signoff deck exists for each platform (Nangate45/ASAP7 coverage in the open flow is limited). Define "strict-clean" per platform in the paper.

## E1 — Source-to-graph yield funnel (C1) · must

- Run: M repos → N candidate designs × 3 platforms, fixed config.
- Report: count at each of the 9 handoffs; loss-reason taxonomy (unsynthesizable, missing top/SDC, black box, DRC, LVS, timing, graph-join fail, ...); end-to-end yield; CPU-h per admitted sample.
- Baseline: default ORFS flow, no qualification, no repair → where losses concentrate, what repair recovers.
- Gate ablation: drop signoff gate → % "admitted" samples carrying residual violations.
- Determinism: rerun K=20 designs × 3 → graph/label hashes identical.
- Dedup: near-duplicate RTL detection across repos/forks (needed for E2/E4 splits).
- Figures: funnel per platform; loss table; dataset stats (#designs, cells/nets distribution) vs. prior datasets.

## E2 — Held-out repair transfer (C2) · must, core

**Split.** Per platform, disjoint dev / held-out by design family (repo-level, after dedup). Learn + validate recipes on dev only; freeze before touching held-out.

**Learner.** Learn recipes once with Opus (recommended). Optional ablation: learner = Qwen, to show recipes from an open model also transfer.

**Arms (held-out failures, per platform):**

| Arm | LLM | Seeds |
|---|---|---|
| a. No repair | — | 1 |
| b. From-scratch LLM repair | Opus, GPT, Qwen, GLM | 3 |
| c. Frozen recipes → LLM fallback (ours) | Opus, GPT, Qwen, GLM | 3 |
| d. Frozen recipes only | — | 1 |
| e. Unvalidated recipes (all proposals adopted) | — | 1 |

Same action policy, attempt cap, and token cap across b/c.

**Metrics.**
- Strict-clean fix rate; flow reruns; LLM tokens (in/out/cached) and $; wall-clock.
- QoR delta vs. un-failed reference: WNS/TNS, area, WL, power — savings must not come from worse layouts.
- Split covered vs. uncovered failures; report coverage rate and recipe-match precision.
- Token comparisons only within a model (tokenizers differ); cross-model comparison in $ and fix rate.

**Stats.** Paired per design: McNemar (fix rate), Wilcoxon (attempts, tokens), bootstrap 95% CI.

**Optional boundary test.** Apply sky130 recipes on Nangate45/ASAP7 → delimits the "same platform" scope.

## E3 — Semantic validity audit (C3) · must

- Identity: graph node/edge ↔ netlist/DEF/SPEF join rate per stage, esp. after CTS/route buffer insertion, resizing, renaming.
- Visibility: feature-provenance audit; table of blocked features per stage.
- Labels: recompute via an independent path (e.g., HPWL from DEF vs. tool report; Cgnd from SPEF vs. alternate extractor; WL from DEF vs. route report; cell congestion re-derived from cell DEF location + GRT GCell map) → agreement.
- Checker validation: fault injection (label index shift, later-stage feature leak, ID mismatch) → detection recall and false-positive rate.
- Prevalence: run a no-audit builder on the same designs → % samples silently corrupted, per corruption type.

## E4 — Downstream learning (C3 + motivation) · must

**Task matrix** (✓ = valid; ✗ = label computable from that stage's visible features → leakage):

| Input stage | HPWL | WL | Cgnd | Congestion |
|---|---|---|---|---|
| Floorplan | ✓ | ✓ | ✓ | ✓ |
| Place | ✗ | ✓ | ✓ | ✓ |
| CTS | ✗ | ✓ | ✓ | ✓ |
| Route | ✗ | ✗ | ✓ (no SPEF features) | ✗ |

Confirm against the actual graph builder. Granularity: net-level for HPWL/WL/Cgnd; cell-level for congestion (each cell takes the GRT overflow/utilization of the GCell containing it). Report how cells spanning multiple GCells are handled (e.g., max overflow over covered GCells).

**Setup.**
- Models: GCN, GraphSAGE, GAT, graph transformer + XGBoost on hand-crafted features.
- Design-level splits (same dedup families as E2); 3 seeds.
- Metrics: MAE, R², Spearman (log-scale for WL/HPWL/Cgnd); congestion also hotspot F1/AUC.

**Comparisons.**
1. Audited vs. no-audit dataset → accuracy/variance gap.
2. Stage-restricted vs. leaked features (e.g., positions → HPWL at Place; SPEF features → Cgnd at Route) → leaked scores inflated.
3. Cross-technology (optional): train sky130, test Nangate45/ASAP7 with per-tech label normalization.

## E5 — Cost amortization (C2 deployment) · should

- Cumulative tokens / reruns / $ vs. #designs processed, with vs. without frozen recipes, per LLM.
- $ and CPU-h per admitted sample, end to end.

## Positioning table (no runs)

Stage coverage (1–9), signoff guarantee, reported yield, identity/label audit, held-out repair — vs. OpenABC-D, CircuitNet, ForgeEDA, AiEDA, CircuitOps, EDA-Schema-V2, R2G, HighTide, ORFS-agent, EvoDRC.

## Budget formulas (fill from P0)

- Flow CPU-h ≈ 3 × N × h_flow + Σ_p F_p × (reruns in arms a–e) × h_rerun
- LLM tokens (E2) ≈ Σ_p F_p × 4 models × 3 seeds × (T_scratch + (1 − coverage_p) × T_fallback)
- From-scratch arm (b) dominates token cost.

**Tiers if budget is tight:**
- Minimum: all 4 LLMs on sky130; Opus + Qwen on Nangate45/ASAP7; E4 on sky130 only.
- Full: all 4 LLMs × 3 platforms; E4 on all platforms + cross-tech.

## Risks

- C2 is trivial unless fix rate and QoR are shown not to drop; low coverage weakens "covered failures".
- HighTide (agent skills, stages 1–7) is the obvious baseline; run it on a subset or justify its absence.
- Abstract has no numbers yet (repos, designs, yield %, token reduction %) — fill from E1/E2.
- Signoff decks for Nangate45/ASAP7 may not support full DRC+LVS → "violation-free signoff" must be defined per platform.

## Order

P0 → E1 → E2 → E3 → E4 → E5
