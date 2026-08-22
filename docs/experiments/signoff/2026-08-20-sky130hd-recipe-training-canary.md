# Sky130HD Recipe-Training Canary

## Executive Summary

This canary tested whether the frozen R2G Agent can turn stable, real Sky130HD
physical-design failures into bounded repair evidence without changing the registered
100 MHz task. The initial baseline cohort produced 10 fixed-target physical-signoff-clean
designs, five stable Repair Challenges, and two input-qualification failures. After their
compilation closures were corrected, both input failures were rematerialized into fresh
projects and passed new six-stage ORFS plus strict physical-signoff runs. The initial
classification remains part of the audit trail; the post-requalification cumulative count
is therefore 12 fixed-target-clean designs, not a retroactive rewrite of the baseline.

On the five stable challenges, existing Recipes resolved two designs: `density_relief`
cleared 20 `m3.2` DRC violations, and `route_relief` converted one route-timeout design
into a fixed-target physical-signoff-clean result. One severe setup-timing case and two
other route-timeout cases remained escalated. These are repair results at a fixed target,
not publication-clean graph results, because the campaign deliberately did not run or
stamp an Fmax winner.

The canary also exposed two P0 Agent defects and three lifecycle/experiment-efficiency
issues. First, the live loop could mark a DRC/LVS-clean but timing-failing repair globally
clean. Second, backend A/B arms could treat ORFS completion as success without rerunning
strict physical signoff. Both defects, plus a route-timeout A/B effect-mismatch, were
fixed locally and covered by regression tests. Invalid trials and artifacts were archived
and excluded before a corrected strict route A/B campaign was run to completion.

## Frozen Configuration

| Item | Frozen value |
|---|---|
| R2G commit | `56a0143a01afc8d607a7848cec8047ca00b7c957` |
| ORFS commit | `a5ff7ef7dac4338e6e5fad7710b85fc6c8f3503c` |
| Platform | `sky130hd` |
| Clock target | 100 MHz / 10 ns |
| Baseline | Unmodified Default ORFS |
| Knowledge start | Immutable `knowledge_before.sqlite` snapshot |
| Repair rule | `period_relax` excluded; no clock-target relaxation |
| A/B rule | Two subjects, two repeats per arm, global non-regression required |

Campaign evidence is under
`/home/yangao/r2g_recipe_training_sky130hd_2026_08_20_canary01`.

## Baseline Funnel

| Classification | Designs | Interpretation |
|---|---:|---|
| Fixed-target physical-signoff clean | 10 | DRC, LVS, route, RCX, and timing passed at 100 MHz |
| Stable Repair Challenge | 5 | Same protected failure signature reproduced twice |
| Input-qualification failure | 2 | Missing compilation closure; no valid physical attempt |
| Environment/toolchain failure | 0 | No failure was attributed to missing tools or platform setup |

Stable failure signatures were one setup-timing failure, one `m3.2` DRC failure,
and three route timeouts. Expander Wave 2 acquired 21 of 40 requested new revisions
and terminated honestly as `BLOCKED_LOOP_LIMIT`; no missing candidates were fabricated.

The two input-qualification failures were subsequently rematerialized with corrected
source closures under `input_requalification/`. Both produced fresh, distinct run
directories with successful synth, floorplan, place, CTS, route, and finish stages;
fresh DRC/LVS reports bound to those run tags; clean route and RCX evidence; and positive
setup/hold slack at the same 100 MHz target. Both are fixed-target physical-signoff clean.
They remain outside publication-clean accounting because this canary intentionally did
not run or stamp an Fmax winner.

## Existing-Recipe Results

| Failure | Recipe action | Outcome |
|---|---|---|
| Setup WNS about -5.07 ns | Timing utilization relief, 25% to 20% | Failed: WNS worsened to -5.10546 ns; escalated |
| 20 `m3.2` DRC violations | `density_relief`, utilization 25% to 17% | Passed fixed-target physical signoff |
| `pcs_rx` route timeout | `route_relief`, utilization 25% to 8% | Passed fixed-target physical signoff; WNS +4.51876 ns |
| Route timeout, `tsa_systolic_8x8_core_i8` | `route_relief`, utilization to 8% | Still timed out; escalated |
| Route timeout, `tsa_axi_lite_top_8x8_i8` | `route_relief`, utilization to 8% | Still timed out; escalated |

Aggregate existing-Recipe recovery was 2/5. By strategy, `density_relief` was 1/1,
the timing action was 0/1, and `route_relief` was 1/3. No failed or inconclusive
attempt is counted as a successful Recipe.

## Agent Findings

### P0: DRC/LVS-Only Success Could Be Marked Globally Clean

The first repair pass marked the severe timing case clean after DRC and LVS succeeded,
even though `timing_check.json` still had negative WNS and `signoff_manifest.json`
correctly reported `strict_clean=false`. The invalid pass was stopped before A/B and
archived under `state/invalid_false_clean_2026-08-20/`.

The local fix makes the live-loop clean decision use one global vector containing DRC,
LVS, route, RCX, and timing; contradictory negative-WNS evidence fails closed. A timing
repair now receives a checker-only DRC/LVS recheck before acceptance.

### P0: Backend A/B Could Promote ORFS Completion Without Signoff

The first corrected-effect route A/B drain still revealed a separate causal-validation
defect. Route and place arms were marked successful when their ORFS stage returned zero,
without running DRC, LVS, RCX, and timing checks. Their trial metrics therefore had null
global state, yet the judge could treat them as evidence for a Recipe win. Trial 454 and
its no-signoff arm artifacts were declared invalid and archived under
`state/invalid_backend_ab_without_signoff_2026-08-21/`.

The local fix runs a measurement-only strict-signoff pass after every successful route or
place A/B arm and requires full DRC clean, LVS clean, route clean, RCX complete, timing
clean with non-negative WNS, strict platform capability, and confirming-run consensus.
The judge independently rechecks the same evidence before accepting an arm. This gate is
deliberately a fixed-target *physical-clean* gate: the protected 10 ns clock is checked for
non-regression, while Fmax search remains outside this Recipe-training experiment and is
still required separately for publication-clean status.

### P1: Inconclusive-Only Evidence Can Enqueue an A/B Candidate

The failed `bus_heavy/large` route attempt produced one `inconclusive` `route_relief`
episode with zero successes and zero wins. The learner still emitted a new candidate for
that scope. This cannot directly promote without A/B evidence, but it spends full-flow
compute validating a strategy/scope pair with no positive prior evidence.

Recommended fix: retain inconclusive and failed episodes as ranking/negative evidence,
but require at least one independent `cleared` or `win` episode before automatic candidate
enqueue. A human-authored candidate may still enter through an explicit review path.

### P1: Class-Scoped Candidates Duplicate the Same Pooled A/B Work

The `crypto/large` and `bus_heavy/large` candidates share the same symptom and physical
effect. Because each lacked two exact-class subjects, both fell back to the same two
pooled Sky130HD subjects. This generated 16 arms for only eight distinct logical
subject/action/repeat combinations.

Recommended fix: key physical experiments by normalized effect fingerprint plus subject,
platform, protected task digest, and repeat. Reuse one causal experiment as pooled-transfer
evidence without treating it as exact-class evidence; exact and pooled confidence must
remain separately reported.

### P1: Route-Timeout A/B Replaced the Learned Effect With a Weaker Action

The successful live `pcs_rx` repair applied the route-timeout policy and changed
`CORE_UTILIZATION` from 25% directly to the 8% floor. The initial A/B harness discarded
the immutable `ROUTE_TIMEOUT` signature and seeded arm B as a generic route `fail`.
Consequently, diagnose selected the gentler residual-route action, 25% to 17%, and the
trial no longer tested the action that generated the positive evidence. At 17%, detailed
routing began with about 70,503 violations; continuing that trial could only produce a
false-negative or irrelevant verdict.

The mismatched arms, ledger, runtime database, and ORFS artifacts were archived under
`state/invalid_ab_effect_mismatch_2026-08-21/` and excluded from lifecycle evidence. The
local fix reads the arm's immutable repair-family probe and preserves `ROUTE_TIMEOUT`
when seeding route diagnosis, so arm B applies the same floor-utilization policy as live
repair. Arm B consequently changed utilization from 25% to the demonstrated 8% action,
while the protected 10 ns clock remained unchanged.

## Strict A/B Result

The corrected campaign used two pooled Sky130HD subjects and two repeats per arm. Every
arm was judged on `backend+strict_signoff`; all eight run IDs were distinct and the judge
recorded `provenance_complete=true`.

| Subject | A result | B result | Verdict | Interpretation |
|---|---:|---:|---|---|
| `pcs_rx` (`exp_d_043da80b3f8e273a3519`) | 0/2 successful | 2/2 physical-clean | Win | Both B repeats passed DRC, LVS, route, RCX, and timing at 10 ns; WNS was +4.51876 ns |
| `tsa_axi_lite_top_8x8_i8` (`exp_d_352a0f5399d17ef9c74d`) | 0/2 successful | 0/2 successful | Inconclusive | Both arms remained route failures; no positive evidence was assigned |

Trial 457 contains the valid win. Its A run IDs are
`6ff0e4c3898e10effd707efe9587d58fba09842f` and
`3b9983176caa57393ca00f37be03a5793a787dc4`; its B run IDs are
`127f595c21f5752f161861f1e1e954ddd187968a` and
`0e975fcef500b8a98770ac79e99263e49050deb1`. Trial 456 contains the independent
inconclusive result. Its A run IDs are `a72be2bc6188710622b4bf7cad00de5d410c72e8`
and `fe701744e8a3c72efe978ef425f02a183408c390`; its B run IDs are
`c653b99c67d043f8800a697f05c69388aaca46c5` and
`27ec3fafac93691d98c932ac1269fab422abda0b`.

The `crypto/large`, Sky130HD `route_relief` lifecycle transitioned to `promoted` with
provenance `ab_corpus:1w0l`. The inconclusive-only `bus_heavy/large` candidate was parked
and did not receive promotion credit. The valid positive evidence therefore supports one
class-scoped promotion; it does not claim that `route_relief` cures every route timeout.

## Regression Verification

Focused route/A/B/global-gate tests passed 27/27. The complete signoff-loop and cohort
suite was run in four stable shards because the monolithic pytest process was terminated
by the external execution limit at 88%. All shards passed, for an aggregate result of
1,222 passed and one skipped test, with no assertion failures.

## Knowledge Publication

The 18 campaign runs, 11 bound failure events, seven fix events, seven trajectories,
and two valid A/B trials were merged additively into the tracked knowledge store. The
merge gate rejected inherited dangling rows from the isolated snapshot; those stale
derived rows were removed only from a temporary interchange copy, while the original
campaign database remains unchanged. The sanitized transfer then passed all honesty and
foreign-key gates.

After import, the canonical lifecycle judge independently resolved both arm run IDs and
their ownership before transitioning Sky130HD `crypto/large/route_relief` to `promoted`
with `ab_corpus:1w0l`. The inconclusive-only `bus_heavy/large` scope remains inert. The
tracked heuristics were regenerated from the updated canonical database rather than
copied from the isolated campaign runtime.

## Scientific Interpretation

This canary shows that the current catalog can repair some real Sky130HD DRC and route
failures under a protected task, and that one route action can survive repeated strict
causal validation on an applicable subject. Severe timing and capacity-limited routing
remain outside the demonstrated action space: the timing action worsened WNS, and two
designs still timed out at the 8% utilization floor. No new Recipe was invented for those
cases because no bounded, globally non-regressive action was demonstrated.

The next scale-up may use this canary as development evidence, but should first evidence-gate
automatic candidate enqueue and deduplicate pooled experiments by normalized effect,
subject, platform, protected-task digest, and repeat. The two source-closure cases have
received their required real rematerialize-and-rerun qualification. Publication experiments
must additionally run Fmax search and graph publication gates; this fixed-target training
campaign must not be cited as publication-clean graph evidence.
