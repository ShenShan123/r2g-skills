# Signoff Repair Log Remediation

**Date:** 2026-08-12  
**Base commit:** `e5d3c477dd03c48840bf0f829f7d87f8c34100a4`  
**Branch:** `fix/signoff-repair-policy-and-classification`

## Scope

This patch addresses two production-Agent defects exposed by the completed Experiment-2
V2 campaign. It also adds one geometry-derived, A/B-gated repair candidate. It does not
relax any DRC, LVS, timing, route, RCX, constraint, or provenance gate.

## Confirmed defects

1. **Current ORFS runs were classified as `*/unknown`.** Live diagnosis searched only
   `project/synth/synth.log`, while current ORFS stores synthesis statistics under the
   latest `backend/RUN_*/reports_orfs/synth_stat.txt`. This caused can_fifo to miss an
   existing promoted `logic/small / sky130hd / density_relief` Recipe and stop without
   attempting a repair.
2. **The repair loop did not consume a task-level action contract.** On the fixed-footprint
   SDRAM task, Full R2G changed `CORE_UTILIZATION` from 25 to 17. The evaluator correctly
   rejected the result, but the Agent should have blocked the action before spending an
   ORFS rerun.

All three residual Sky130HD `m3.2` cases also placed every coordinate-bearing marker at
the right die edge. Controlled probes had already shown that excluding right-side pin
placement clears GCD (6 to 0) and SDRAM (60 to 0) without changing die area, clock, or
the check set. This is used only to create an A/B candidate, not a live promotion.

## Changes

- Resolve legacy and current ORFS synthesis-stat layouts, including the tabular
  `synth_stat.txt` format, before assigning the Recipe design class.
- Add an optional `R2G_REPAIR_ACTION_POLICY_FILE` contract. Once configured, undeclared,
  out-of-range, malformed, or unreadable actions fail closed. With it unset, normal
  autonomous behavior is unchanged.
- Detect a unique edge-localized Sky130HD `m3.2` cluster from `6_final.def` plus
  `6_drc.lyrdb` and offer `pin_side_rebalance` with `requires_ab_promotion=true`.
- Allow an A/B-gated strategy to become live only when its exact lifecycle row is
  explicitly `promoted`; `--rank-first` remains the controlled A/B-arm override.
- Register the strategy with the engineer-loop apply domain and document the contract.

## Validation

- Signoff-loop suite with the active toolchain bound explicitly:
  `1176 passed, 1 skipped`.
- New log-derived regression group plus lifecycle tests: `34 passed`.
- Historical read-only replay:
  - can_fifo now resolves to `logic/small`, retrieves the promoted density Recipe, and
    chooses `density_relief` rather than `catalog_exhausted`;
  - fixed-footprint GCD and SDRAM policies reject `density_relief` before execution;
  - all three edge-localized cases emit `pin_side_rebalance`, but normal live selection
    stops because the strategy is not promoted.
- Fresh physical reruns on copied projects:
  - can_fifo: promoted `density_relief`, `m3.2` DRC **10 to 0**;
  - GCD: forced A/B-style `pin_side_rebalance`, `m3.2` DRC **6 to 0**;
  - both new runs: DRC clean, Netgen LVS clean, route clean, timing clean, RCX complete,
    with DRC/LVS bound to the same new run tag.

The copied Experiment-2 fixtures did not contain a completed `fmax_search` artifact, so
their strict manifest remains blocked only on that pre-existing constraint-provenance
requirement. No Fmax evidence was fabricated. At this validation stage the shipped
knowledge database was unchanged and `pin_side_rebalance` still required formal A/B
promotion; the subsequent promotion campaign is recorded below.

## Eight-RTL controlled rerun

The complete eight-design Experiment-2 development cohort was rerun from a clean campaign
at Agent commit `d24a2cf45dee35e3bce6b4c9812d6b3e59b3a626`. The cohort and task-spec
digests remained `c84583ca...` and `bbeb3019...`; Sky130HD, 100 MHz, source closures,
footprint limits, action allowlists, strict gates, and the frozen knowledge seed were
unchanged. Only Full R2G was rerun because the three completed Vanilla runs are frozen
controls and the patch does not affect them.

| Outcome | Before (`e5d3c47`) | After (`d24a2cf`) |
|---|---:|---:|
| Overall strict-clean | 5/8 | **6/8** |
| Repair-needed recovery | 3/6 | **4/6** |
| Clean-sentinel non-regression | 2/2 | **2/2** |

- can_fifo changed from fail to strict-clean: the Agent parsed the current ORFS
  `synth_stat.txt`, applied the already-promoted `density_relief` action within the
  registered utilization range, and cleared full-deck DRC from 10 to 0. Independent
  evaluation found DRC, LVS, route, timing, antenna, RCX, and provenance clean.
- SDRAM kept `CORE_UTILIZATION=25`; the previous out-of-contract 25-to-17 mutation did
  not recur. It remained fail-closed with 60 DRC violations rather than claiming an
  invalid improvement.
- GCD remained fail-closed with 6 DRC violations. GCD and SDRAM require formal promotion
  of the geometry-derived pin-side action before autonomous use; this rerun deliberately
  did not seed that evidence or bypass the lifecycle gate.
- The remaining six designs passed all strict gates. No external LLM tokens or human
  repair interventions were used.

Two startup-only attempts were excluded before scoring: one omitted the locally pinned
Magic/Netgen environment, and one selected Python 3.7. Both failed before a valid campaign
could be evaluated. The counted campaign used the pinned EDA tools and Python 3.10, ended
with runner return code 0, and produced the independent result file at
`r2g_exp2_v2_repair_d24a2cf_2026_08_12_run01/reports/experiment2_pilot_results.json`.
Wall time is not compared because the rerun used a shared server under different load.

## Formal A/B promotion

The two residual designs were retained in the original held-out cohort and then reused as
post-evaluation remediation evidence because naturally occurring, same-class repair-needed
RTL is scarce. This promotion campaign is therefore not reported as an additional untouched
held-out score. It tests whether the proposed action causes the improvement under controlled
A/B execution.

The clean campaign was
`/home/yangao/r2g_ab_pin_side_2026_08_12_run05`. It used the code tree captured by commit
`5fb9fb2`, Sky130HD at the frozen 10 ns period, two independent subjects (GCD and SDRAM),
two repeats per arm, and two Recipe lifecycle keys (`logic/small` and `logic/medium`). This
produced 16 physical arm runs and four provenance-complete trials.

| Recipe key | GCD | SDRAM | Independent decisive evidence | Final state |
|---|---|---|---:|---|
| `logic/small / sky130hd` | win | inconclusive | 1 win, 0 losses | promoted |
| `logic/medium / sky130hd` | win | win | 2 wins, 0 losses | promoted |

For both GCD trials, the two control repeats retained the `m3.2` failure while both treated
repeats cleared it and completed DRC, LVS, route, and timing without a global-regression
veto. SDRAM was less deterministic: one lifecycle-key trial had neither arm complete and
was correctly recorded as `both_arms_never_succeed`; the other produced two clean treated
repeats against two failed controls. No inconclusive row was counted as a win and neither
Recipe key accrued a loss.

The formal run exposed and fixed two additional Agent defects before its evidence was
accepted:

1. `engineer_loop` queried the isolated `R2G_KNOWLEDGE_DB`, but its ingest subprocess used
   an import-time default and wrote arm runs into the shipped database. The CLI and caller
   now resolve and pass the same explicit database path.
2. The first completed subject promoted the Recipe and incremented `status_version`; the
   staleness guard then canceled the remaining subject from the same planned A/B corpus.
   A lifecycle move produced by that same A/B corpus now preserves the remaining evidence,
   while operator, regression, generation, and Recipe-content changes remain fail-closed.

The isolated evidence was merged through `knowledge_sync.py`, not copied over the existing
store. The merge added 18 runs, four trials, and their ownership/lineage records (108 rows
in total). The tracked lifecycle rows now read `ab_corpus:1w0l` and `ab_corpus:2w0l`.
All five production honesty gates remained green after the merge. The toolchain-bound
signoff-loop suite passed with **1181 passed, 1 skipped**; the focused database-isolation
and incremental-judge group passed **24/24**.

Formal A/B promotion here is a Recipe-efficacy decision, not a graph-publication waiver.
The original fixtures still lack frozen Fmax provenance, so they remain ineligible for a
strict published graph until that independent constraint gate is satisfied.
