# Experiment 2 Family-Contract Smoke Pilot Results

Status: development-only Pilot; not a paper result  
Date: 2026-08-11  
Campaign: `/home/yangao/r2g_exp2_family_contract_smoke_2026_08_11_run03`

## What Was Tested

The synchronized Experiment 2 runner was exercised on Sky130HD at a fixed 100 MHz
(10 ns) target. The four fixtures comprised two independently evidenced
`footprint_congestion` repair tasks and two Default-ORFS-clean sentinels. Each method
received the same frozen RTL closure, target, bounded action domain, four-flow budget,
four CPU cores, and sequential access to ORFS. A result counted only after a checkpoint
was locked and passed an independent strict-signoff rerun.

The tested Agent commit was `1915dc9b7fde2783637bde3d51fadeca44679c83`; ORFS was
`a5ff7ef7dac4338e6e5fad7710b85fc6c8f3503c`. The campaign manifest also records the
dirty-tree diff and the exact cohort, task-spec, evaluator, controller, model-route,
and knowledge-seed digests.

## Results

| Method | Strict-clean delivery | Repair recovery | Sentinel preservation | Provider tokens | Method wall time |
| --- | ---: | ---: | ---: | ---: | ---: |
| GPT-5.5 Vanilla | 4/4 | 2/2 | 2/2 | 188,951 | 27.65 min |
| Qwen3.7-Max Vanilla | 4/4 | 2/2 | 2/2 | 212,330 | 29.00 min |
| DeepSeek-V4-Flash Vanilla | 2/4 | 0/2 | 2/2 | 693,252 | 26.87 min |
| Full R2G | 3/4 | 1/2 | 2/2 | N/A | 19.80 min |

`Provider tokens = N/A` for Full R2G because this deterministic execution path did not
call an external LLM. It still consumed CPU and EDA runtime. The wall-time totals are
method execution time, while independent final revalidation is recorded separately.

All 13 locked checkpoints passed protected-input, complete-flow, route, full DRC,
LVS, setup/hold timing, antenna, RCX, and same-run provenance checks during independent
revalidation. Both sentinels were preserved by every method, so this Pilot found no
evidence of repair-induced regression on the clean controls.

## What The Failures Mean

GPT and Qwen recovered both repair fixtures, principally by reducing bounded
`CORE_UTILIZATION` to 15%. DeepSeek also produced a strict-clean Fuxi checkpoint at 15%,
but consumed its token budget before issuing the required lock call; it therefore
correctly counts as a delivery failure. On `can_fifo`, it spent the budget inspecting
the initial 10-DRC result and never attempted a legal repair.

Full R2G recovered Fuxi at 17% utilization with no external LLM call. On `can_fifo`,
the complete flow was healthy except for ten full-deck `m3.2` spacing violations; LVS,
route, timing, antenna, and RCX were otherwise valid. The engineer loop selected no
applicable live strategy and terminated with `catalog_exhausted`. Since the public
action domain demonstrates that a bounded utilization change can repair this fixture,
this is a real Recipe coverage or symptom-to-action matching gap, not an unavoidable
Sky130HD tool limitation.

## Pilot Conclusions

The revised protocol is executable and produces meaningful separation between methods.
It also verifies the desired safety behavior: fixed target and source inputs remained
protected, clean controls did not regress, unsuccessful methods could not claim a clean
result, and every claimed success survived an independent signoff rerun.

The main Agent improvement exposed by this Pilot is to make the generic
footprint/congestion recovery path available when a valid bounded action exists but no
live Recipe matches the exact symptom bucket. Any fallback must remain lifecycle-aware,
must compare the full signoff vector, and must stop or roll back on regression.

This Pilot does not establish broad repair generalization. Both repair fixtures belong
to one family and are development-seen. A formal Experiment 2 cohort must be frozen
prospectively across multiple repair families, repositories, sizes, and design families.

## Infrastructure Corrections After The Run

The run exposed one reporting-only error: rows without a locked checkpoint omitted the
fixture role, causing the generated summary to undercount repair-task denominators.
The raw per-fixture outcomes were correct. The future scorer now attaches role, family,
scope, token use, and wall time to both successful and failed rows. The batch runner now
defaults to and enforces one concurrent ORFS flow, the documented action name matches
the real `set_orfs_knob` interface, and Vanilla submissions explicitly state whether a
strict-clean checkpoint was locked before final evaluation. The synchronized test suite
passes 52 tests.

Experiment 3 remains intentionally blocked. Its task specification passes lint, but the
8+8 adaptation/evaluation cohort template fails closed because it has not yet been
populated with digest-bound, independently qualified fixtures spanning three stable
repair strata. Starting ablation now would test a hand-filled or underpowered cohort,
not continual learning.
