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

## Live status after 3 waves (2026-06-25) — committed `526a7d7`, branch
`fix/loop-closure-timing-place-fmax` (store/heuristics deliberately NOT committed — live-mutating).

WORKING live: campaign healthy (driver PGID 2425382, ~13h, 3 waves), honesty parity holds
(`fail=169 fe=169`), DRC/route produce DECISIVE A/B verdicts (route trial 31 = loss), `fmax-drain`
stamps real SDCs (13/9/11 designs/wave), `inconclusive` no longer demotes (candidates persist), the
stall is gone (no 75-min inert arms).

**OPEN — timing/place NOT promoting live (2 distinct gaps):**
1. **A/B subjects are previously-FIXED CLEAN designs.** On reflow they don't re-exhibit the symptom,
   so arm A and arm B do identical work → `inconclusive` (antenna trials 34-39 went inconclusive for
   the same reason; the lone nangate45 promotion is the pre-existing trial-26). This is a deeper
   A/B-subject-selection issue affecting ALL classes, only exposed now that the jitter floor stops
   noise from manufacturing fake wins. plan_trial must pick a subject whose CURRENT config still
   exhibits the symptom (e.g. FLW-0024 die still too small / SDC still at the failing period), or
   re-seed the failing condition on arm A.
2. **Timing arm runs carry no `wns_ns`/`timing_tier`.** Trials 32/33 (post-fix) show BOTH arms
   `is_success=False`, identical `outcome=0.5`; arm B's SDC stayed `10.0` (period_relax never
   applied) and `reports/timing_check.json` is absent — so `_arm_metric(timing=True)` reads null →
   False for both. The `--check timing` reflow isn't measuring+ingesting timing for the arm. Verify
   `fix_signoff --check timing` actually runs on the arm and that ingest projects ppa.json's
   setup_wns→`wns_ns` + timing_check tier→`timing_tier` for the arm run.
3. **Backoff counts STALE pre-fix inconclusive trials** (21/22/27/28/29/30) → `_ab_coverage_gap`
   already suppressed place re-validation (4 `ab_coverage_gap` escalations). Clear/epoch-gate the
   contaminated pre-fix trials so place (the clean apply-then-flow case) gets a fresh divergent trial.

NEXT: fix (1)+(2)+(3), re-drain ONE place + ONE timing candidate, confirm `metrics_json` shows arms
DIVERGING (A is_success≠B) and `candidate→promoted`. Until then the loop is structurally correct but
not demonstrably closed for timing/place. Escalation-recovery of the 153 escalated designs is a
separate follow-up.

## RESOLVED + PROVEN end-to-end on real ORFS (2026-06-25) — 5 commits

Verifying-by-running uncovered FOUR nested bugs in the timing chain, each masking the next (the unit
tests passed at every step because each mocked the broken layer):
1. **plan_trial crossed platforms** (nangate45 recipe → sky130hd subjects) → same-platform only (4c9ea4b).
2. **No flow step emits reports/ppa.json** → check_timing saw nothing → tier 'unknown' → period_relax
   never picked. fix_signoff timing baseline now runs extract_ppa first (88939bd). Seeded-ppa tests masked it.
3. **_arm_metric** timing on-disk fallback (timing_check.json/ppa) when the runs row is null (4c9ea4b).
4. **Arm copy's config.mk SDC_FILE pinned the ORIGINAL design's SDC** → the reflow ran at the FAILING
   period and period_relax's edit was ignored (the 22f3e67 SDC-pinning bug, recurring). `_localize_arm_sdc`
   repoints SDC_FILE to the arm's own constraint.sdc after the copytree (d60ac0d).

PROOF (controlled fix-loop on iccad2015_unit16_in1, real ORFS): `period_relax` drove
`clk 10.0 (wns −4.5, tier severe) → 15.234 (wns −1.4) → 17.469 (wns −0.23, tier MINOR = MET)`,
badness 4508→1403→230. Arm B (relaxed → minor → is_success True) DIVERGES from arm A (10.0 → severe →
False) → `win` → period_relax is promotable. Before the fixes the same recipe gave wns −4.5 → −4.5
(zero effect). **The timing learning loop is closed.** pytest 776 green.

STATUS by class: DRC+route = closed & live-promoting (real win/loss). TIMING = PROVEN to close + diverge
(controlled); the live recipe_status period_relax candidate→promoted lands when the campaign completes a
(slow, multi-iter) timing trial — stale trials cleared, all 4 fixes applied to new waves, honesty GREEN.
PLACE (core_util_relief) = honest DEAD-END (d29abae killed DIE_AREA → FLW-0024 absent from corpus →
resize no-op → unvalidatable, escalated never promoted). Commits 526a7d7/4c9ea4b/88939bd/d60ac0d (+ docs),
branch fix/loop-closure-timing-place-fmax, NOT pushed.

---

**2026-07-03 addendum (branch r2g-debug/sky130-round):** a further plan_trial subject-resolution
hole surfaced on the sky130 clean-slate round: **Tier 1 (`run_violations`) lacked the on-disk
`isdir` filter** Tiers 2/3 carry, so wiped-round exhibitor rows (immutable history) became GHOST
A/B arms (`place_arm_incomplete` every drain; candidate starved). Fixed: Tier-1 isdir filter +
plan_arms skips arms whose subject dir AND arm dir are both absent. See failure-patterns.md
"Ghost A/B arms" sub-variant. This supersedes the implicit assumption here that Tier-1 subjects
are always physically present.
