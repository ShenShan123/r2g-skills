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

### 2026-06-28 iteration 4 — re-queues recovering live + LVS writer-crash false-fail fixed

**Campaign live-confirms the synth recovery:** wave 15 (new code) picked up the 4 re-queued memcap
designs — `verilog_axis_axis_fifo` + `_fifo_adapter` both reached **CTS (status 0)** with
`SYNTH_MEMORY_MAX_BITS=65536` + a CORE_UTILIZATION die in config (cleared synth AND got past place,
no FLW-0024 = the recovery + die-pairing working on real campaign designs). They're slow (FF-expanded
route, like the validation), so the `synth_memory_relax` A/B drain (and promotion) is pending their
ingest — inevitable, just behind the slow tail. honesty 5/5; promo_ng 7.

**New bug found + fixed (commit `6f29bf3`): LVS match-then-writer-crash false `fail`.**
`PicoRV32_Based_SoC_fifo_basic` was `lvs=fail` with **0 mismatches** and lvsdb `text_match_found` —
the COMPARE matched, but KLayout crashed in the post-compare lvsdb WRITER (`net2id.end()` assert /
`Internal error ... Executable::cleanup`), emitting a spurious "Netlists don't match". `extract_lvs`
checked `log_status='mismatch'` before its crash case → false fail. Fix: `_CRASH_RE` recognizes the
writer-crash signature; the status decision classifies *lvsdb-matched + 0-mismatch + crash* as
`crash` (retry-fixable), never `fail`; `run_lvs.sh` retries on it for a clean survivor. Validated on
the real artifacts (re-extract → `crash`, was `fail`); regenerated PicoRV32's live report. TDD
test_extract_lvs.py (+2); suite 823→**825**. The earlier doc claim that "the extractor handles the
net2id writer error" was only half true (it covered a clean verdict co-existing with the error, not a
spurious mismatch verdict) — now closed.

**Bug-hunt context (no false-fail epidemic):** scanned all `catalog_exhausted` `lvs=fail` designs —
63 `real_connectivity` (genuine net/device mismatches, mostly iccad2017 contest designs — flow-hard,
not false), 6 `symmetric_matcher` (known KLayout noise), 1 `generic`, and the 1 PicoRV32 false-fail
(now fixed). So unlike the sky130 wrong-tool epidemic, nangate45's LVS-fail bucket is mostly genuine.

**Open (iter-5):** confirm wave 15's re-queued designs reach a terminal verdict + the
`synth_memory_relax` A/B trial records a WIN → `promoted(nangate45) > 7`; scale the re-queue to the
remaining 11 memcap. Tail-blocking (FF-expanded flows are slow) still bounds throughput.

### 2026-06-28 iteration 5 — found WHY synth_memory_relax never promoted: planning-loop fragility

Across iters 1-4 the `synth_memory_relax` candidate kept "about to promote next wave" but stayed at
**0 A/B trials** for 5+ hours of wave 15. Root cause (commit `ce13f97`): `plan_arms_for_candidates`
called `ab_runner.plan_trial` with **no try/except**. `plan_trial` reads state that races the
campaign's concurrent `heuristics.json`/ingest writes and throws TRANSIENTLY (caught in the wild as
an intermittent `KeyError 'design'`; a clean re-run resolves the candidate's 2 subjects fine). One
crashing candidate aborts the WHOLE planning loop → every candidate after it is never planned.
`synth_memory_relax` is the **LAST of 33** pending candidates, so any transient crash earlier in the
list stranded it on every drain. A *new* shape of the "ab_trials grows but a recipe never promotes"
alarm — planning fragility, not arm non-divergence. Fix: isolate each candidate (skip + log a crash,
stay `candidate`, re-plan next drain, never demote). TDD `test_plan_arms_isolation.py`; suite 826→827.
The diagnostic that nailed it: the candidate IS in `pending_candidates`, `plan_trial` returns OK
standalone, yet 0 ledger arm entries and 0 planning log lines (success appends silently, skips log →
neither trace = the loop never reached it).

**Second finding (deferred to iter-6): synth symptom is over-coarse.** Keyed only `{orfs_stage,
synth}`, it conflates memcap/timeout/missing-header, so `plan_trial` resolves a timeout subject
(`verilog_ethernet_arp`) for the memcap recipe → both arms time out (7200s) → inconclusive + ~8h
wasted per drain. In-loop application is signature-gated (correct); only A/B subject selection is
coarse. Fix = memcap-specific symptom predicate. Deferred (careful symptom-keying).

Also cleaned up the orphaned iter-2 pilot `*_synth_me_*` arm dirs (8 dirs, no backend, from the
pilot's separate ledger). **Open (iter-6):** with the planning isolation fixed, the campaign's next
drain (wave 16+) plans + judges `synth_memory_relax` → expect a WIN (arm A memcap-abort loses to arm
B) → `promoted(nangate45) > 7`; do the coarseness fix first to avoid the arp-timeout waste.

### 2026-06-28 iteration 6 — synth_memory_relax PROMOTED (promo_ng 7→8) — the arc closes

**The "new solutions promoted" proof finally LANDED.** A recipe that did not exist at the start of
this session is now `promoted` in the LIVE store via a genuine A/B trial. Two changes this iteration:

1. **`fffc157` synth A/B arm runs SYNTH-ONLY.** A synth backend-abort arm is judged on 'synth
   cleared' (`_arm_metric synth=True`), so it does NOT need place/route. `_run_flow` sets
   `ORFS_STAGES=synth` + a bounded `ORFS_TIMEOUT` for a `kind=ab_arm/check=synth` entry. arm B clears
   synth in ~3.5 min instead of the HOURS the FF-expanded memory takes to place/route — which it
   route-fails anyway (this iteration confirmed the re-queued memcap designs escalate
   `route_congestion_residual` AFTER a clean synth: the recipe clears synth, the FF array doesn't
   route — exactly why the metric is synth-cleared, not full-signoff). The bounded timeout also caps
   the symptom-coarseness arp subject at minutes. TDD; suite 827→828.

2. **Proactive drain → PROMOTION.** With planning unblocked (iter-5) and the arm fast (synth-only),
   drove a real A/B trial for `synth_memory_relax` on a memcap subject (`verilog_axis_axis_async_fifo_adapter`,
   default cap, avoiding the arp timeout subject): both arm-A controls memcap-aborted in **4 s** (synth
   NOT cleared), both arm-B copies cleared synth in **207–208 s** (recipe: raise cap + die-pair) → a
   decisive **WIN** → `judge_recipe` → **`recipe_status` = promoted**. `promoted(nangate45)` **7 → 8**,
   `ab_trials` 101 → 102 (verdict=win, `A_samples is_success=false` confirms the real divergence),
   **honesty 5/5 green** after. A second `synth_memory_relax|bus_heavy/large` candidate also exists now
   (the re-queued designs' fix_events generalized the recipe across design classes).

**The full arc (iters 1–6):** found the synth-abort misclassification → auto-recovered the memcap case
→ proved the loop learns it live → paired the die so it reaches place → wired it as an A/B arm → fixed
the verdict metric → fixed the catalog_exhausted + LVS false-fails → unblocked the planning loop
(crash-isolation) → made the arm synth-only → **PROMOTED**. 14 commits; suite 828; honesty 5/5
throughout. **Remaining follow-up (iter-7):** the symptom-coarseness (memcap predicate) so the
campaign's natural drain doesn't resolve the arp timeout subject; scale the re-queue to the remaining
11 memcap; the campaign (wave 16+) will now promote `synth_memory_relax` itself off the live evidence.

### 2026-06-28 iteration 7 — tail-blocking root cause: gate synth_memory_relax by memory size

Investigated the wave-15 tail-block (16h on 2 designs): the LVS children were at **99.7% CPU** with
a **4h timeout** — genuinely computing on a large design, NOT a hang/bug (memory note confirmed). The
ROOT is that `synth_memory_relax` FF-expands these memcap designs' 17408 / 18944 / 40960-bit memories
into 17-41 K flops -> ~153 Kum^2 designs whose route TIMES OUT (all 4 re-queues escalated
`route_congestion_residual` after a CLEAN synth) and whose KLayout LVS legitimately runs ~4h. For
memories this large a **fakeram hard macro** is the right fix, not FF expansion (the skill's intent is
FF for "register files and FIFOs").

**Fix (commit `256b1b1`): size gate.** `process_one`'s in-loop recovery now gates on
`_synth_memory_ff_expandable` (`_synth_largest_memory_bits` parses `Largest single memory instance:
N bits`): N <= `_SYNTH_MEM_FF_LIMIT` (16384) -> FF-expand as before; larger -> escalate
`synth_memory_residual` with a note routing it to a fakeram macro, never FF-expand into a 4h-LVS /
route-timeout design. Unparseable size keeps the prior FF-expand default (no regression). The A/B arm
(`_apply_recipe_strategy`) is UNCHANGED — `synth_memory_relax` still validly clears synth (what it is
judged on) and stays **promoted**; only the APPLICATION policy is refined to stop creating
tail-blocking designs. TDD test_synth_abort_classify.py (+4); suite 828→**832**. Validated:
`verilog_axis_axis_async_fifo_adapter` (40960b, default cap) now -> fakeram residual, not FF-expand.

**Net iters 1–7:** 10 real fixes, `synth_memory_relax` promoted, honesty 5/5 throughout, suite 832.
**Open (iter-8):** the synth symptom-coarseness (memcap predicate) for the campaign's natural drain;
the FF-expanded re-queues already in flight will finish + escalate honestly; the structural
barrier-wave tail-blocking (large designs are inherently slow) remains the only non-fixed item, now
mitigated at its synth root.

### 2026-06-28 iteration 8 — pushed all 17 commits; re-queued STALE pin_overflow escalations

Pushed iters 1–7 (17 commits, 996b0ed..3cfe16f) to origin/main. Then a fresh bug-hunt in the
`pin_overflow_residual` bucket (30) found they are all **STALE**: created 2026-06-26 23:xx, BEFORE
the perimeter-targeted die fix `a359a2c` (2026-06-27 22:43), so they escalated under the OLD
util-only PPL-0024 handler (their configs still show `CORE_UTILIZATION`, not an explicit perimeter
die). The perimeter fix sizes a die to the placer's exact demand (iccad2015_unit04: 6761 pins →
3786um perimeter → a ~1109um die that fits them) and is tested + proven-live on
`verilog_ethernet_ip_demux`. So these are recoverable by re-queuing — the same
stale-escalation-predating-a-fix pattern as the synth re-queue. Re-queued a pilot of 8 real-IP
`verilog_*` designs (smaller/standard, less tail-block risk than the 6761-pin contest designs);
wave 16 (fresh process, all fixes loaded) recovers them via the perimeter die. **VERIFY iter-9:** the
8 reach clean/honest-residual; scale to the 12 iccad2015 + 10 others if clean. Wave 15's tail is
down to 1 design (wb2axip_axivdisplay LVS hit its 4h timeout); wave 16 imminent.

### 2026-06-29 iteration 9 — loop promoting BROADLY; scaled pin_overflow re-queue; synth_timeout triaged

**Loop robustness confirmed:** `promoted(nangate45)` grew 8→**9** during wave 15's long drain —
`core_util_relief` is now promoted across **8 design classes** (logic tiny/small/medium/large,
bus_heavy small/medium, crypto small/large) + `synth_memory_relax`×1. The loop generalizes recovery
recipes across the whole corpus, not one case. Wave 15 (~20h) is NOT stuck — it's a heavy productive
A/B drain (12+ arms: core_util_relief on DSP ifft, route_relief on wb2axip) at **load 54** — CPUs
well-used, recipes promoting. honesty 5/5.

**Scaled the pin_overflow re-queue:** the remaining 22 stale `pin_overflow_residual` designs all have
SANE die demands (≤1466um: iccad2017_unit20 5028um→1466um die, iccad2015 3786um→1109um, down to
1749um→523um), so all are recoverable by the perimeter fix. Re-queued all 22 (after the iter-8 pilot of
8) → 30 total pending for wave 16 to recover via `_set_explicit_die`.

**synth_timeout bucket triaged (15) — no recoverable batch:** 11 time out at the yosys `HIERARCHY`
pass = AST-elaboration pathology (verilog_ethernet MACs, lfsr), genuinely unfixable (raising the
timeout does not help). 4 time out at `DEMUXMAP` = the re-queued memcap designs (cap=65536)
FF-expanding their 40960-bit memories -- so FF-expansion of a LARGE memory is slow enough to time out
IN SYNTH, strongly validating the iter-7 memory-size gate (a fresh such design now escalates fakeram
instead). Honest characterization; no fix (AST-pathology is unrecoverable; the FF-expansion case is
prevented going forward by the iter-7 gate). **VERIFY iter-10:** wave 16 starts + recovers the 30
pin_overflow re-queues via the perimeter fix; the memory-size gate routes fresh large memcap to fakeram.

### 2026-06-29 iteration 13 — pin_overflow recovery outcome + iter-12 Fmax fix PROVEN live

**pin_overflow re-queue (30) — honest outcome:** the perimeter fix did its job. 4 fully CLEAN;
**18 advanced from the stale place-abort to `catalog_exhausted`** (iccad2015_unit03_in1 reached
`finish`/GDS with an explicit DIE_AREA → PPL-0024 cleared, then a DRC/LVS residual — the next genuine
hurdle for pin-heavy contest designs); 8 in flight. So 22 designs moved past the stale abort to a
later stage, honestly reclassified.

**iter-12 Fmax fix PROVEN live on r8051_core** (a clean design that previously errored). BEFORE:
`error` (aborted at the floorplan probe, ws=null). AFTER: trace `floorplan(null) -> place(null,
inconclusive)` — it FELL BACK to the place probe (the fix) and honestly reported `inconclusive`, not
a misleading `error`.

**Deeper follow-up surfaced (iter-14): the Fmax PROXY under-reports timing.** r8051_core's FULL flow
has real timing (clean run `wns=4.826ns`) yet its proxy returns null slack at BOTH floorplan and place.
So the iter-12 fix makes the 26 clean error designs HONEST (inconclusive/unconstrained), but recovering
their actual Fmax needs the root fix -- the `place_fast` probe / clone STA not emitting `setup_wns` at
the place stage. Documented; not chased now (Fmax is secondary; the committed fix is verified).
honesty 5/5. 22 commits this session, all pushed.

### 2026-06-29 iteration 14 — Fmax-proxy investigation: iter-13 follow-up was a MISREAD; iter-12 fix is complete

Investigated the iter-13 "Fmax proxy under-reports timing for clean designs" follow-up by reproducing
real proxy probes (kept variants, inspected stage metrics + flow.log). Conclusion: **no additional Fmax
bug** -- the 73 fmax 'error' designs fall into three HONEST classes, all already handled correctly by
the iter-12 fix:
1. **incomplete (missing header)** -- r8051_core (the design I first checked) errors because its synth
   FAILS on `Can't open include file 'instruction.v'` (absent from the source repo; metadata
   status=incomplete_missing_headers; its wns=4.826 'clean' run is STALE history from when the include
   was present). 47/73 are escalated/unflowable -> iter-12 fix -> honest `inconclusive`.
2. **genuinely unconstrained** -- the 26 ledger-'clean' ones (15 VTR/odin benchmarks like a dlatch +
   simple_i2c_slave/uart16550) report floorplan ws=1e+39 AND a FULL-FLOW wns=1e+39 with ZERO real-wns
   runs: they have no clock-constrained critical path (latches / single-cycle logic), so there is no
   Fmax to search. iter-12 fix -> honest `unconstrained` (a place fallback would be wasted -- the full
   flow is unconstrained too), proven on the dlatch and simple_i2c_slave.
3. **recoverable sequential** -- a design whose floorplan stage merely fails to emit a slack (null, not
   1e+39) falls back to the place root-find and gets a real Fmax (the iter-12 fallback path; proven it
   reaches `place` on r8051_core before bottoming out honestly).

So the iter-12 commit (f5ee684) is COMPLETE: error -> unconstrained/inconclusive/ok, all honest. No code
change this iteration -- a thorough investigation that correctly concluded there was no further bug
(the honest outcome). honesty 5/5; kept variant cleaned up; 23 commits this session, all pushed.

---

**2026-07-03 addendum (branch r2g-debug/sky130-round):** the place-class A/B path gained a
subject-existence guard: plan_trial Tier 1 now isdir-filters exhibitors (wiped-round ghost dirs
no longer become `core_uti` arms), and plan_arms_for_candidates refuses to ledger an arm with no
subject dir on disk. A candidate whose exhibitors are ALL ghosts now escalates
`unvalidatable_insufficient_subjects` honestly instead of spawning `place_arm_incomplete` ghosts
every drain. See failure-patterns.md "Ghost A/B arms" (2026-07-03).
