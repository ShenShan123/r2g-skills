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
requirement. No Fmax evidence was fabricated. The shipped knowledge database is also
unchanged: `pin_side_rebalance` still requires a formal A/B promotion before blind live use.

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
