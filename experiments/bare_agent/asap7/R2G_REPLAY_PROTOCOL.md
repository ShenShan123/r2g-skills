# ASAP7 Recipes + LLM arm: replayed recipe stage, live LLM stage

Status: **DRAFT, 2026-11-20.** Nothing in this arm has run yet.

**User decisions, 2026-11-20 (these supersede the [open] items below):**

1. Develop as many effective recipes as possible first, then integrate them into R2G.
2. Do not distinguish in-sample from out-of-sample; just complete the experiment. The fairness caveat about
   in-sample development is therefore dropped.
3. The arm must mimic a real LLM–R2G interaction. The LLM drives R2G live, and R2G invokes recipe 1. If the
   design is not fixed, R2G invokes the next recipe, and so on. A flow whose exact inputs match a recorded
   run returns that run's recorded artefacts and verdict instead of re-running. This is a flow-result cache
   keyed by the full effective input: RTL, config, overlays and tool versions. Anything not in the cache runs
   live.
4. If no recipe fixes the design, the LLM reasons and attempts repairs beyond the recipes, live. This is the
   existing `hybrid.py` fallback stage.

Implementation consequence: the replay sits *under* the real R2G pipeline (a cache in front of the
flow+signoff call), not beside it. The model calls, recipe order and stop decisions are R2G's own and run
live. Each cache hit is logged with `"provenance": "replayed_development_run"` and the source run; each miss
runs and is logged `"executed_live"`.

## Why a replay

On the other three platforms, the Recipes + LLM arm (Sec. 4.3.1) ran in two stages:

1. **Recipe stage.** R2G repaired each design for real (`measure_agent_repair_cost.py`, one job per design).
   This stage makes **no model calls**.
2. **LLM stage.** `experiments/bare_agent/hybrid.py` reads R2G's verdict. Any design that is not
   dual-clean is queued for an LLM fallback (`run_bare.py --from-project`). The fallback starts from
   R2G's final project, with R2G's diagnosis as context.

On ASAP7, every recipe the R2G stage would apply was already executed during recipe development, under the
frozen baseline spec and graded with the same Calibre decks. The repair arms are recorded under
`216:/proj/workarea/yangao/asap7_baseline/repairs/<arm>/{runs,signoff}/`. Running those flows again would
spend about 0.1–0.4 machine-hours per design and teach nothing new.

The user asked (2026-11-20) to replay the recipe stage instead. Its measured outcomes and resource costs come
from the recorded runs, and only the LLM stage runs live.

## What is replayed and what is live

| | Recipe stage | LLM stage |
|---|---|---|
| Execution | **Replayed** from recorded development runs, or **executed for the replay** where no record exists | **Live**: same harness, model routes, caps and grading as the LLM-only arm |
| Model calls | none (the recipe stage makes none on any platform) | real, metered by the relay |
| Verdict | the recorded Calibre `signoff.json` of the run, unchanged | graded live by `run_bare.py` |
| Cost recorded | flow wall seconds and peak disk from `result.json`; signoff wall from `signoff.json`; host, workers and date of the original run | as in the LLM-only arm |

Every replayed record carries `"provenance": "replayed_development_run"`. It also carries the source run
directory, the sha256 of its `6_final.gds`, and the DRC/LVS deck sha256 from its `signoff.json`. A record
produced by a flow run for this arm carries `"provenance": "executed_for_replay"`. No record is
synthesised, and an outcome is never estimated.

Recorded costs were measured under development load: other arms ran concurrently, and the hosts were 203
(native) and 216 (container). They are reported as measured, with their host and load context. They are not
adjusted to look like a fresh run.

## Cohort

The same 300-case random sample as the LLM-only arms: `sample300_seed20261120.json`, built with
`random.Random(20261120).sample(sorted(eligible_509_ids), 300)`, sorted. That makes the comparison paired
case by case.

## Recipe-stage policy (what R2G would do)

**Eligibility rule (user, 2026-11-20).** R2G invokes a recipe only when that recipe's precondition holds on
the design's **current** state: the latest signoff or flow failure, not the baseline. It never cycles
blindly through every recipe.

- Each recipe declares a precondition: rule families or rules present in the current Calibre DRC, LVS state,
  or a flow failure signature, plus any structural condition.
- After each recipe, signoff is re-read and the eligible set is recomputed from the new violations.
- A recipe already applied to the design is not re-applied.
- When the eligible set is empty, the recipe stage stops and the design goes to the LLM fallback.

These preconditions are written into R2G's recipe table (signoff-loop) when the recipes are integrated. The
table below is the draft.

Families come from the current Calibre DRC result: UM = M5–M7 rules, V1 = V1.*, GATE = GATE.*.

| Step | Baseline symptom | Recipe (repair arm) | Content |
|---|---|---|---|
| 1 | UM only | `um_skip_all` | M5–M7 SPACING raised in the router tech LEF (no GDS/deck change) |
| 1 | any GATE and/or mixed DRC | `combo_pad1_um` | `CELL_PAD_IN_SITES_DETAIL_PLACEMENT=1` + um_skip tech LEF |
| 1 | frontend failure with a nangate45 donor | `fe_transfer_full` (`_combo` when DRC follows) | transplanted frontend repair |
| 1 | RSZ-0074 route failure | `grt_skipincr` | `SKIP_INCREMENTAL_REPAIR=1` |
| 2 | V1.S.1 left after step 1 | `v1_obsbloat10` | step-1 content + SC LEF with the V1/M2 OBS completed and bloated 10 nm |
| 2 | GATE.S.3 left after step 1 | `gate_pad2_um` | padding 2 + um_skip tech LEF |
| stop | anything else, or still failing | hand over to the LLM stage (`recipe_failed` / `no_recipe`) | |

**[open]** The policy has to be frozen before the arm runs. It must also be written into R2G itself
(signoff-loop recipes for asap7) so the replay replays R2G and not a hand-made table. Until then this table
is the definition of record.

## Coverage of the sample (measured 2026-11-20 from the recorded arms)

| Recorded recipe outcome | Cases |
|---|---|
| dual-clean in a recorded run | 205 |
| recorded, still failing (GATE.S.3 in 39 cases, V1.S.1 in 22, median 4 violations) | 59 |
| flow recorded, no signoff verdict | 2 |
| **no recorded run of the policy's recipe** | **34** (14 UM, 7 UM+V1, 4 GATE+UM, 2 GATE+UM+V1, 7 flow failures) |

There are three kinds of gap. Each is filled by **executing** the missing step and labelling it
`executed_for_replay`:

- **The policy's recipe was never run on the case.** These are the 34 cases above, plus the V1.S.1 cases
  that only ever got step 1.
- **Incomplete DRC verdicts.** `unknown` in 8 `v1_obsbloat10` and 6 `um_skip_all` sampled cases. Fix:
  re-sign the recorded GDS; no flow needed.
- **New recipes** (GATE.S.3 work, see `RECIPE_DEVELOPMENT_LOG.md`). If a new recipe joins the frozen policy,
  it runs on the cases it applies to. A replay can only contain outcomes that were measured.

## Fairness caveats (to state in the paper)

1. **[open] In-sample development.** The ASAP7 recipes were developed and selected on the very designs being
   replayed. There was no held-out split, so recipe-stage results here are in-sample. The other platforms'
   R2G knowledge was learned before their 4.3.1 runs. Options:
   - (a) report as is, with this caveat;
   - (b) freeze the policy now, and replay only runs that were made with exactly the frozen recipe content
     (true here), still in-sample;
   - (c) report the 300-case numbers next to a held-out check on designs outside the sample.
2. **Two hosts and setups.** Recorded runs used 203 native ORFS or 216 containers. These were verified to
   give identical Calibre verdicts on the same inputs (2026-11-19 cross-host check, three designs).
3. **Comparison rule.** The LLM stage uses exactly the LLM-only arm's harness, model settings, budgets and
   grading. The Recipes + LLM arm's success is: recipe stage dual-clean, or LLM fallback dual-clean. The
   recipe stage's cost is reported separately and flagged as replayed. It is never summed into "live" wall
   time without that label.

## Reproducing

- Recorded recipe runs and their signoffs: `216:/proj/workarea/yangao/asap7_baseline/repairs/` (arm
  directories, each with `extra.mk` / `stage` overlays).
- The replay table will be built by `experiments/bare_agent/asap7/build_replay.py`, not yet written. It
  maps each sampled case to its policy step, the recorded run (or the run executed for the replay), its
  verdict and cost fields, and the provenance label. It also emits the `hybrid.py` input (R2G-shaped project
  per case: `config.mk`, SDC, final reports, a `fix_log.jsonl` whose rows name the replayed recipe and its
  provenance).
- LLM stage: `launch_bare.py` over the `hybrid.py` queue, on the same hosts and launcher as the LLM-only arms.
