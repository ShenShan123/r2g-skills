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

## Update 2026-06-27 — PPL-0024 perimeter-targeted die (closes the util-lever undershoot)

The 2026-06-26 PPL-0024 handler (above) used the cell-area `CORE_UTILIZATION` lever, proven only
on the SMALL-pin-gap c2670 case. A fresh-session audit of the live store found it still STALLED
on the corpus: **39/39 nangate45 A/B trials inconclusive, `promoted(nangate45)=0`** (the wave-9
reconcile correctly reverted the lone fake nangate45 promotion — so no LEGITIMATE one had ever
happened). Root cause = **wrong lever** (failure-patterns.md "Pattern 13"): the dominant place
subjects are cell-tiny/pin-huge PPL-0024 (ip_demux 1521 pins, dma_controller 3089); util scales
the die by CELL AREA, but PPL-0024 is a PERIMETER constraint. A 0.6× util step grew ip_demux's
perimeter 490→631um where the placer demanded **851.76um**, so BOTH arms PPL-0024-aborted
identically → permanent tie (`metrics_json`: both arms `is_success=false, outcome_score=0.333`).

**FIX (`engineer_loop.py`, applied to main):** parse the placer's stated target
(`_ppl0024_required_perimeter`: `... die perimeter from <A>um to <B>um`) and size an explicit
square `DIE_AREA`/`CORE_AREA` whose CORE perimeter ≥ `B×1.15` (`_set_explicit_die`), preferred
over the util lever (which stays the FLW-0024 fallback). The A/B arm copy excludes the subject
backend, so `plan_arms_for_candidates` stamps the SUBJECT's `pin_perimeter_target` onto each
place arm and `_apply_recipe_strategy`(place) hits it.

**PROVEN end-to-end** on `verilog_ethernet_ip_demux` (util 12, demands 851.76um): arm A aborts at
place (`PPL-0024`); arm B (`DIE_AREA 0 0 265 265`) runs synth→…→finish to a final `6_final.gds`
(RC=0) → a DECISIVE WIN — the first legitimate nangate45 divergence. Suite 787→797 (new
`tests/test_ppl0024_perimeter_die.py`), honesty 5/5.

**Loop re-opened:** the 13 pre-fix `core_util_relief` inconclusive trials were known-contaminated
(broken lever forced the ties) and had pushed 3/4 place candidate keys past `AB_INCONCLUSIVE_MAX`
→ `_ab_coverage_gap` would skip them forever. Deleted ONLY those 13 (0 decisive to lose; genuine
non-divergent antenna inconclusives left gapped); all 4 place keys now re-plannable, `ab_trials`
54→41, honesty 5/5. `recipe_status` was NOT hand-edited — the next drain decides the promotion.
Also retuned `pool.env` (24/4/20 → 32/3/32) for CPU utilization. **VERIFY next iteration:**
`promoted(nangate45)` > 0 after the campaign's next A/B drain. NOT committed (working tree).

---

## 2026-06-28 follow-up — synth-abort misclassification (commit `329c450`)

**Verified the loop is closed + promoting** (waves.log): `promo_ng` climbs 1→2→7 across waves
11–13, `ab_trials` 46→86, honesty 5/5 every wave — the 2026-06-26/27 fixes landed and the loop
learns from real divergent arms. The wave-13 jump is the incremental-judge + perimeter-die +
stale-judged fixes on real data.

**New bug found (escalation honesty):** `engineer_loop.process_one` collapsed EVERY early synth
abort into `reason='unseen_crash'`. Auditing the 79 nangate45 `unseen_crash` escalations by their
real synth-log signature: **48** missing-`include header (incomplete upstream RTL), **15** inferred
RAM > Yosys 4096-bit `SYNTH_MEMORY_MAX_BITS` (a MECHANICAL, documented fix), **10** yosys synth
timeout, **~6** genuine downstream crashes. So ~73/79 were deterministic, log-diagnosable synth
conditions misfiled as mysteries — blinding the learner and skipping a documented recovery.

**FIX (`engineer_loop.py`, commit `329c450`):** mirror the FLW-0024/PPL-0024 recover-then-retry
pattern — `_is_synth_memory_cap` + `_raise_synth_memory_cap` (cap→65536 in config.mk) re-flows
ONCE and records a learnable `fix_log` row (`strategy='synth_memory_relax'`, check `orfs_stage`,
class `synth`) → ingest projects it into a Tier-3 recipe. The rest escalate under honest reasons
`synth_memory_residual` / `incomplete_missing_header` / `synth_timeout`, never `unseen_crash`.
**PROVEN** on `verilog_axis_axis_fifo`: default-cap synth aborts in 3 s (status 2); cap=65536 →
synth passes (status 0, 211 s, `1_2_yosys.v` produced). Suite **814 passed** (2 pre-existing
techlib golden errors from the `design_cases` wipe). Docs: `failure-patterns.md` (both synth
buckets). **Superseded invariant:** "an early synth abort escalates `unseen_crash`" is now
"the synth log is parsed → memory-cap recovers in-loop, the rest get honest reasons."

**Resume action:** re-queued a 4-design pilot (smallest memcap FIFOs) to the live ledger
(`state=pending`) so the next wave recovers them with the fixed loop + learns the recipe.
Also retuned `pool.env` 32/3/32 → 24/2/24 (finesim co-tenant returned, ~42 procs; good-neighbour
per the hard rule, NUM_CORES halved not WORKERS). **VERIFY next iteration:** the 4 re-queued
designs reach `clean`/honest-residual, a `synth_memory_relax` fix_event/recipe appears in
knowledge, and `promoted(nangate45)` keeps growing. Tail-blocking (barrier waves vs a slow
large-design tail) remains the open structural follow-up.

### 2026-06-28 iteration 2 — live recipe-learning PROVEN + 3 more fixes

Wave 14 was still grinding with the *old* code (its `run` process predates the iter-1 commit),
so the re-queued pilot + fix wouldn't be exercised until wave 15 (hours away). Drove the proof
proactively instead: ran one pilot (`verilog_axis_axis_fifo`) through the fixed loop against the
LIVE store (dedicated 1-design ledger, no campaign-ledger race). **Full closed loop on a recipe
that did not exist this morning:** fix_log `synth_memory_relax verdict=cleared` → live
`knowledge.sqlite` gained a `synth_memory_relax|synth|cleared` **fix_event** → `learn_cycle` put
the recipe in `heuristics.json` AND auto-enqueued an **A/B candidate** (`recipe_status`:
`nangate45|crypto/large|candidate`, Gate A). honesty 5/5 throughout. That IS "learn from the fix
trajectory → record it → promote new solutions," demonstrated live.

Three more fixes (all TDD; suite 814→**818**):
1. `e99a7f6` — **synth_memory_relax verdict = synth cleared, not whole-flow clean.** The cap raise
   expands RAM→flops, so a memcap design can clear synth yet over-pack at place; tying `cleared`
   to `result=='clean'` recorded a downstream place failure as the synth fix FAILING (false
   negative). Now `cleared = _fail_stage != 'synth'` after the retry.
2. `cbcad40` + `813825a` — **catalog_exhausted escalation records the POST-fix residual.** It used
   the PRE-fix `status` snapshot (usually `{drc:unknown,lvs:unknown}` before any report exists), so
   all 184 escalations read identical while their residuals were diverse. Re-read after `_run_fix`;
   reconcile tool fixed the existing 195 rows in place → real split surfaced (88 drc=stuck/lvs=clean,
   67 drc=clean/lvs=fail, 29 both, 10 lvs=incomplete; **0 stale false-escalations**). honesty 5/5.
3. `0773f95` — **pair the cap raise with die auto-size.** Live pilot: cap=65536 cleared synth but
   the FF-expanded design hit 3072% util → FLW-0024 at place. Now the recovery also converts a
   fixed DIE_AREA → CORE_UTILIZATION=20. Re-validated live: config gets cap+util both set, DIE_AREA
   dropped, and the re-flow clears synth AND passes floorplan (no FLW-0024).

**Superseded invariant:** "catalog_exhausted notes carry the signoff status" → "…carry the POST-fix
residual (the unclearable symptom), not the pre-fix snapshot."

**Open follow-up (iter-3):** wire a **synth backend-abort A/B arm** so `synth_memory_relax` can
formally PROMOTE (today its auto-enqueued candidate routes to `--check both`, can't diverge on a
synth-aborting design → accrues ≤`AB_INCONCLUSIVE_MAX` cheap inconclusive trials → `_ab_coverage_gap`
skips it, never demotes — bounded + honest, but not promoted). The wiring mirrors the place arm:
`_symptom_check`(synth→'synth'), add 'synth' to the `process_one` backend-abort arm set,
`_apply_recipe_strategy`(synth → raise cap + die-pair), `_arm_metric`(synth → is_success = synth
cleared). Deferred deliberately — A/B-arm wiring is where subtle loop bugs hide; it deserves
dedicated TDD, not a rushed end-of-iteration addition.

### 2026-06-28 iteration 3 — synth A/B arm wired (`synth_memory_relax` now PROMOTABLE) + campaign live

**Campaign caught up:** wave 14 (old code, 7h — tail-blocked on two legit-slow iccad2015_unit18
period_relax timing arms, NOT frozen, so left to finish per the "legit large-design slowness"
rule) completed at 18:36Z; **wave 15 started with the 24/2/24 pool.env** → a fresh process that
loaded ALL iter-1/2/3 commits + the 4 re-queued memcap designs. The fixes are now live in the
campaign. honesty 5/5; ab_trials 86→101; promo_ng 7.

**Synth A/B arm wired (commit `1a90928`).** `synth_memory_relax` is now a first-class backend-abort
A/B arm (mirrors place/route apply-then-flow + the timing metric): `_SYNTH_STRATEGIES`,
`_symptom_check`→'synth', `process_one` routes check='synth' → `_process_backend_ab_arm`,
`_apply_recipe_strategy`(synth)=raise cap + die-pair, and — the key judgment — `_arm_metric(synth=True)`
judges on **'synth cleared'** (`_synth_cleared_ondisk` reads the arm's stage_log), NOT generic
is_success. Arm A control memcap-aborts at synth → not cleared; arm B clears it → decisive WIN.
TDD `tests/test_synth_ab_arm.py` incl. a flows-free end-to-end pair → records 'win' (is_success
stubbed True for both, proving the synth metric breaks the tie). Suite 818→**823**.

**Why the synth-cleared metric (not is_success) is the right call — validated live.** The
die-pairing full-flow validation (`verilog_axis_axis_fifo` copy, fresh default cap) confirmed the
recovery carries the design *past place and through global route* (it FLW-0024'd at place BEFORE
0773f95), but the FF-expanded ~64K-flop array then grinds detail_route with ~145 DRC violations —
i.e. it clears synth yet may NOT reach clean signoff. Judging on is_success would tie both arms;
the synth-cleared metric correctly credits the recipe for the symptom it fixes. (Honest caveat: for
large memories a fakeram macro — the `synth_memory_residual` escalation — is the real answer; the
65536-bit cap bounds the FF blow-up.)

**Open follow-up (iter-4):** confirm wave 15 recovers the 4 re-queued memcap designs + the
synth_memory_relax candidate now reaches a decisive verdict (win) on a real drain → `promoted`;
THEN scale the re-queue to the remaining 11 memcap. Tail-blocking (legit-slow large-design arms
holding a wave barrier) remains the structural CPU-utilization follow-up; the 24-design waves
shorten the tail but don't eliminate it.
