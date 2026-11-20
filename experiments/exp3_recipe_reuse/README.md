# Exp3 extension: recipe accumulation and reuse (simulation)

Question: when an LLM fallback repairs a design after R2G's own recipes failed, can R2G keep that repair as a
reliable recipe and apply it itself the next time the same or a similar problem appears, without the LLM?

Population: Exp3's **R2G + model fallback** attempts on in-scope designs R2G did not fix (DeepSeek and GPT
fallbacks). Standalone model-only attempts are never used. No OpenROAD run is made: every input is a recorded
artifact of the campaign.

Mechanism under test: `r2g-skills/signoff-loop/knowledge/learned_recipes.py` (+ its hook in
`scripts/reports/diagnose_signoff_fix.py` and the evidence write in `scripts/flow/fix_signoff.sh`), tests in
`r2g-skills/signoff-loop/tests/test_learned_recipes.py`.

| step | script | what |
|---|---|---|
| collect | `collect_attempts.py`, `collect_r2g_logs.py` | run on 203 / 216, read-only: per attempt `result.json`, flow audit, R2G's final config + diagnosis context + logs, the LLM's final config + hooks |
| simulate | `simulate.py` | distill -> gate -> validate; A mechanism check; B campaign-time replay; C second encounter |

Rules (fixed before the run):
- A recipe is OFFERED only if it passes the gate (reasonable, generalizable) AND the identical fix was verified on
  >= 2 independent split units (repo / identical sources, the frozen split's relation).
- R2G is put back in each design's recorded stop state and queried through its real selection path
  (`diagnose_signoff_fix --next`, then `--apply`), with a knowledge-DB snapshot holding only what was known then.
- A reuse counts as a success only if the project R2G produces is **fix-equivalent** to that design's own
  verified LLM fix (same non-plumbing edits, equal values, byte-identical hooks), so the recorded dual-clean
  signoff of that layout applies. RTL is never edited by a recipe. Every other offer is "unverified" and is
  neither a success nor a saving; it is charged its flow time as if it had failed.
- Similar-problem scenarios exclude the design's own split unit from the store.

Inputs/outputs of the 2026-11-08 run: `design_cases/r2g_vs_bare_20261029/full/recipe_reuse_inputs_20261108/`,
`.../recipe_reuse_sim_20261108/{results.json,RESULTS.md}`.
