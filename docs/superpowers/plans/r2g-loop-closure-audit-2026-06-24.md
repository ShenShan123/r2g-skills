# r2g loop-closure audit + fix — 2026-06-24

**Status:** IMPLEMENTED + campaign relaunched. Uncommitted (commit-when-asked).
Supersedes the "KNOWN GAP" follow-up from `r2g-loop-closure-audit-2026-06-23.md` (timing/place A/B).

## Trigger
User: resume the nangate45 campaign on all CPUs, ensure DRC/LVS + best Fmax per design, and
**find the skill's bugs so the engineer-learning-loop is truly closed** (learns from fix
trajectories, promotes solutions). Found a stalled campaign (PGID 740935, load 9/96) hung on inert
`period_r` A/B arms.

## Audit (adversarial Workflow, 30 agents, 22 confirmed findings)
The loop was **partially closed**: learns→A/B→promotes for DRC(antenna,density)+route(route_relief),
but **INERT** — structurally unpromotable — for **timing(`period_relax`)** and **place
(`core_util_relief`)**. Mechanism: `_symptom_check` routed every non-route symptom to `--check both`
(DRC/LVS), so `R2G_FIX_EXCLUDE/RANK_FIRST` were no-ops on a timing/place recipe → arms byte-identical
→ permanent `inconclusive`, while each timing arm burned ~2715 s of full signoff (the stall).

## Fixes (TDD; pytest 744→771 green, +25 tests; 2 pre-existing techlib errors unrelated)
Ranked bugs → fixes:
1. **Inert-arm stall (#1)** — `engineer_loop._ab_coverage_gap` skips candidates whose arms cannot
   diverge (`_NONDIVERGENT_STRATEGIES={lvs_resolve_unknown}`, or ≥`AB_INCONCLUSIVE_MAX=3` inconclusive
   trials w/ 0 decisive); escalates `ab_coverage_gap`, **never demotes**.
2. **`inconclusive`→terminal `shadow` (#2)** — `ab_runner.record_trial` now calls **`judge_recipe`**:
   status from the FULL `ab_trials` corpus (net wins>losses→promote, losses>wins→shadow, else
   unchanged). Inconclusive carries no info; a later win can revive a shadow.
3. **timing/place structurally unpromotable (#3)** — `_symptom_check(conn, symptom_id, strategy)`
   routes by strategy: place→`place` (apply-then-flow backend arm, arm B resizes DIE_AREA→
   CORE_UTILIZATION via `_apply_recipe_strategy`/`_resize_to_core_util`); timing→`timing`
   (`fix_signoff.sh --check timing`, NEW, mirrors route; uses `check_timing.py`+`diagnose --check
   timing`). `_arm_metric(timing=True)` judges on `wns_ns`/`timing_tier` (a timing miss never aborts
   the flow → `is_success` ties both arms). **Load-bearing bug found:** `check_timing.py` wrote
   `wns`/`clock_period` but diagnose's timing plan reads `wns_ns`/`clock_period_ns` → `period_relax`
   produced no SDC edit; added aliases.
4. **jitter tiebreak (#4)** — `judge_repeated` success-tie now needs |Δwall| ≥ `COST_FLOOR=8%` of the
   combined mean AND sign-consistency (`max(cheaper)<min(dearer)`); `se==0` large-delta win preserved.
5. **last-trial UPSERT (#5)** — subsumed by `judge_recipe` corpus aggregation.
6. **Fmax wiring (#7/#8/#9)** — `engineer_loop fmax-drain [--max N] [--workers N]` proxy-searches each
   pending design (real `fmax_search.py` CLI) and STAMPS its `constraint.sdc` to the winner period;
   idempotency keys on the **SDC stamp** (not report existence) with a post-stamp verify. fmax_search
   phantom `--max-parallel` flag + clockless-SDC crash fixed.
7. **dead code (#11)** — deleted `learn_heuristics._fetch_rows`. Doc drift (#10 lifecycle, #6
   auto_demote) corrected in `recipe_lifecycle.py`/`engineer-loop.md`/`README.md`.

## Adversarial diff-review (Workflow) — caught a REAL blocker the unit tests masked
`fmax_drain` was **inert in production**: `_fmax_one` did `import fmax_model`, but `engineer_loop.py`
only put `knowledge/` on `sys.path` (not `scripts/reports/`); conftest injected it under pytest →
the SDC stamp silently did nothing off-test (the SAME fixture≠production class as the 22f3e67 fmax
pilot bug). Fix: `sys.path.insert(scripts/reports)` at module load + `import re` + stamp-verify +
**a subprocess production-path test** that doesn't rely on conftest. Also fixed L4-02 (idempotency
keyed on report existence made the un-stamped state unrecoverable) and L1-02 (journal the lifecycle
transition, not the raw verdict).

## Reconcile + relaunch
- One-time `judge_recipe` pass over all recipes with trials reconciled `recipe_status` to the new
  math; un-buried 4 inconclusive-demoted shadows → candidate (now re-validated with place/timing
  routing). recipe_status candidate 11→14, promoted 3 (held), shadow 8→5. Honesty 5/5 GREEN.
- Ledger rebuilt (708 real designs: 243 clean / 153 escalated / 312 pending), 80 stale arm entries +
  68 arm dirs dropped. Relaunched via `tools/nangate45_closed_loop_campaign.sh` (PGID recorded in
  `tools/_closed_loop_logs/driver.pgid`): per wave **fmax-drain --max 20 → run --max 20**, WORKERS=24
  NUM_CORES=4 FMAX_WORKERS=16 (host free ~96 cores), honesty snapshot per wave, pool.env hot-retune.

## VERIFY (open)
First wave: confirm a **timing and/or place** recipe transitions candidate→promoted (the
`ab_trials`-grows-but-promoted-flat-per-platform alarm must clear for nangate45 timing/place), real
SDCs stamped to Fmax periods, flows close + DRC/LVS pass, honesty stays GREEN.
