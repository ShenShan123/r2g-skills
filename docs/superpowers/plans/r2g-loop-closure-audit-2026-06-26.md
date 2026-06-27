# r2g engineer-learning-loop: closure audit + fixes (2026-06-26)

Session goal: resume the nangate45 signoff campaign (parallel, Fmax-searched, DRC/LVS),
**find skill bugs, prove the engineer-learning-loop is genuinely closed** (learns from
both failure and success, promotes real solutions), and prove effectiveness/robustness.

## TL;DR

The loop was **honest but STALLED**, not lying: 8 campaign waves ran flow→fix→ingest→learn
→A/B→judge, `ab_trials` grew, but `promoted(nangate45)` was **flat at 1 for all 8 waves**
— the exact 2026-06-24 "`ab_trials`-grows-but-`promoted`-flat-per-platform" alarm. Two root
causes, both now fixed + proven:

1. **Place A/B arms never diverged** (`core_util_relief` no-op) → every place trial
   `inconclusive` → place class never promoted. **FIXED + PROVEN** (a real divergent WIN).
2. **The lone `promoted(nangate45)` was a stale wall-clock-noise promotion** from the
   pre-2026-06-25 judge; `judge_recipe` counts frozen verdict strings and never re-judges.
   **FIXED** by a reconciliation tool → `promoted(nangate45)=0` (honest), real promotions
   (`density_relief` sky130hd `2w0l`) preserved, **all honesty gates green**.

A 6-finder × 2-skeptic adversarial audit (56 agents) independently confirmed both and found
10 more confirmed bugs; the cheap honesty one (Fmax `%g` stamp-verify) is also fixed.

## Commits + final verified state (2026-06-27)

Two commits on `main`:
- **`931932a`** `fix(skill): close the place A/B class + reconcile stale-noise promotions`
  — Fixes 1+2+3 below (`_lower_core_util`, `knowledge/reconcile_ab_verdicts.py`, `_period_stamped`).
- **`44b0b64`** `fix(skill): pin-aware PPL-0024 place recovery (+ register residual escalation reasons)`
  — the PPL-0024 handler (`_is_ppl0024`/`_relieve_pin_overflow`), the missing
  `place_density_residual`/`pin_overflow_residual` REASONS fix, and `tools/reenqueue_ppl0024.py`.

Verified after both commits + the reconcile + the re-enqueue:
- **honesty.py 5/5 GREEN.** `promoted`: nangate45 **0** (HONEST — was a fake 1), sky130hd **1**
  (real `density_relief` `2w0l`) + 2 shadow (real) + candidates.
- **pytest 787 passed**, 13 skipped, 2 errors — the 2 are the pre-existing `techlib`
  regen-baseline failures only (unrelated; the live campaign contends for the ORFS toolchain
  the regen subprocess needs).
- **Re-enqueue APPLIED:** 40 PPL-0024 `unseen_crash` escalations → `drained`; campaign ledger
  normal-pending 152 → **172**; the campaign (PID 2425382, wave 9, fixed code live) re-attempts
  them with the pin-aware handler (the re-enqueued rows sit early in the ledger, so the small
  pin-gap ones — e.g. `iscas85_c2670`, gap 5 — recover within the next wave or two).
- `knowledge.sqlite` + `heuristics.json` LEFT uncommitted by design — live campaign state;
  committing a SQLite file mid-write risks a torn page, so it is committed at a campaign
  checkpoint, not here.

## Diagnosis (the data)

`waves.log` (8 waves, 2026-06-24→26): `promo_ng=1` flat; `ab_trials` 39→41; `cand` 13→21
(candidates pile up, never promote). nangate45 `ab_trials`: 19 inconclusive / 3 win / 2 loss
— and **every** win/loss had arms with IDENTICAL `is_success`+`outcome_score`, differing
only by 2-11s of `wall_s`. Place arms (`core_util_relief`) all 10 inconclusive: arm A and
arm B both carried `CORE_UTILIZATION=20` — the arm "apply" was a no-op.

## Fixes landed this session (TDD, all green)

### Fix 1 — place A/B arms now diverge (loop-closing). `scripts/loop/engineer_loop.py`
`_apply_recipe_strategy`→`_resize_to_core_util` only handled the FLW-0024 fixed-die→
`CORE_UTILIZATION=30` conversion and **no-opped when `CORE_UTILIZATION` was already set**
(the common case on the resumed corpus). So arm B (relief) == arm A (control) → inconclusive
forever. Added `_lower_core_util()` (`_CORE_UTIL_RELIEF_FACTOR=0.6`, `_CORE_UTIL_FLOOR=10`):
when the subject already auto-sizes, arm B LOWERS the existing util (bigger die → easier
place/route). Tests: `test_apply_recipe_strategy_place_lowers_existing_util`,
`test_lower_core_util_floor_is_honest_noop`.

This also incidentally addresses the dominant nangate45 "place fail" = **PPL-0024 (IO pins
exceed die perimeter)** — lowering util enlarges the die perimeter, fitting more pins.

### Fix 2 — reconcile stale A/B verdicts (honesty). `knowledge/reconcile_ab_verdicts.py` (new)
`judge_repeated` was hardened 2026-06-25 (COST_FLOOR=0.08 + strict `max(wb)<min(wa)`) so
jitter can't decide a success-tie, but `judge_recipe` counts the FROZEN `verdict` strings and
never re-judges, so old noise verdicts kept a recipe `promoted`. New tool re-derives each
verdict from its stored `metrics_json` via the CURRENT `judge_repeated` (only for trials with
full A/B samples — never invents from missing data), re-runs `judge_recipe`, and EXPLICITLY
reverts a now-evidence-less `ab_corpus` promotion/demotion to `candidate` (judge_recipe can't
self-heal that). Ran on the real store: **9 noise verdicts → inconclusive, 6 recipes reverted
to candidate**; honest survivor = sky130hd `density_relief` (`2w0l`, real divergent wins);
real negative evidence (route_relief shadows on real B-failures) preserved. Honesty gates 5/5
green. Tests: 4 in `test_reconcile_ab_verdicts.py`.

### Fix 3 — Fmax stamp-verify `%g` (honesty). `scripts/loop/engineer_loop.py`
`_fmax_one` wrote the winner via `rewrite_clk_period` (`{period:g}`, 6 sig-figs) then verified
with `abs(cur-period) < 1e-9` against the FULL-precision winner → a correct stamp like
`0.69180034→'0.6918'` failed by 3.5e-7 and returned None (uncounted; ~28% of stamps). Added
`_period_stamped()` comparing the read-back to the `%g`-formatted value. Tests:
`test_period_stamped_is_g_format_aware`, `test_fmax_drain_counts_high_precision_stamp`.

## PROOF the loop is closed (controlled, real ORFS, scratch DB)

Subject `iscas85_c2670` (nangate45, fails place at util=25 with PPL-0024). Drove the REAL loop
path (`_apply_recipe_strategy`→`run_orfs.sh`→`ingest_run`→`_arm_metric`→`judge_repeated`):

| | arm A (control, util=25) | arm B (core_util_relief, util=15) |
|---|---|---|
| place | **FAIL (PPL-0024)** | pass |
| flow | abort @ place | synth→…→**finish** (full GDS) |
| `is_success` | False | True |
| **judge** | colspan | **WIN** |

Before Fix 1: both arms util=20 → identical → inconclusive. After: a real quality divergence
(fail vs sign-off) → WIN → the place class can promote on real evidence. The loop learns from
BOTH the failure (arm A) and the success (arm B).

## Adversarial audit — 25 findings (12 both-skeptics-confirmed, 3 one, 10 refuted)

CONFIRMED (beyond the 3 fixed above), as prioritized FOLLOW-UPS:
- **escalation honesty (high):** ~93/142 open `unseen_crash` are mislabeled-recoverable —
  48 synth missing-`#include`, **35 place PPL-0024 (no pin-aware die handler)**, 10
  `SYNTH_MEMORY_MAX_BITS`; **33 orphaned** escalations (project dir gone) inflate the queue;
  stale `unseen_crash` never resolved after a recovery completes the flow. `catalog_exhausted`
  + `route_congestion_residual` are honest.
- **ingest (medium):** `knowledge_db.is_success` relaxed path can admit an `orfs_status='fail'`
  run as a learnable success when signoff reports are stale.
- **ingest (low):** learner synthesizes literal `unknown/unknown` design_class that never
  matches `plan_trial`'s exact filter (strands candidates — the deferred #9b); `backfill()`
  updates ALL rows of a project, not just the latest.
- **fmax (medium/low):** no in-loop timing-closure gate at the stamped Fmax (a design can be
  marked clean on DRC/LVS without validating it actually meets the Fmax clock);
  `confirm_grid` edge computed but not stamped.

REFUTED (latent / correct-by-design): the `run --workers N` NUM_CORES oversubscription (the
launcher caps `NUM_CORES=4`, so it is not biting — but worth a programmatic guard later); the
Fmax SDC-repoint bug is genuinely fixed; multi-run immutability, partial exclusion, Gate-A,
and the knowledge/journal firewall all verified correct.

## Honesty / verification snapshot (after fixes + reconcile)
- honesty.py 5/5 GREEN (fail_event parity 206=206, ab_trials non-empty, no event on non-fail).
- recipe_status: nangate45 19 candidate / 0 promoted (HONEST); sky130hd 1 promoted
  (`density_relief`, real) / 2 shadow (real) / 8 candidate.
- fix_trajectories: 427 resolved + 938 abandoned (negative learning recorded).
- pytest: place+fmax+reconcile suites green; full suite green except 2 pre-existing
  techlib regen-baseline errors (unrelated to the loop; ORFS-contention during the live run).

## What the running campaign does next
`tools/nangate45_closed_loop_campaign.sh` (PID 2425382) re-invokes `engineer_loop.py` fresh
each wave, so it picks up Fixes 1+3 on its next `run`/`fmax-drain` phase with no restart. New
place-failing designs in the 152 pending (many PPL-0024) will now produce **divergent place
trials → honest nangate45 promotions** organically. The reconciled antenna/core_util
candidates that are genuinely non-divergent stay coverage-gapped (honest, not re-promoted).

## Update (same session) — PPL-0024 pin-aware recovery + escalation reconcile

Implemented the top escalation follow-up. The dominant mislabeled-`unseen_crash` class is
**PPL-0024 (IO pins exceed die perimeter)** — `process_one` had no handler (only FLW-0024).
Added `_is_ppl0024()` + `_relieve_pin_overflow()` (enlarge the die: lower `CORE_UTILIZATION`
for an auto-sized subject, or convert a fixed `DIE_AREA` to util=15) wired into `process_one`
parallel to the FLW-0024 recovery, with an honest `pin_overflow_residual` label when the pin
gap survives enlargement. TDD (3 tests) **also caught a latent crash**: `place_density_residual`
(emitted since 2026-06-23) was never in `escalations.REASONS`, so the rare FLW-0024 residual
would have raised `ValueError`; both residual reasons are now registered. Identified **40**
open `unseen_crash` escalations that are really PPL-0024 and re-enqueued them via
`tools/reenqueue_ppl0024.py` (drained their stale rows) so the campaign re-attempts them with
the new handler. Mechanism proven in the Fix-1 c2670 trial (util 25→15 → place→GDS). Remaining
escalation follow-ups (synth `#include`, `SYNTH_MEMORY_MAX_BITS`, 33 dir-gone orphans) + the
ingest `is_success` relaxed path stay open.
