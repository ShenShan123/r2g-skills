# Experiment 2 Historical Repair-Candidate Audit

Date: 2026-08-09  
Status: development evidence; not a frozen experimental cohort

## Purpose

This audit mines earlier R2G runs for designs that may provide genuine repair-needed
fixtures for the ORFS/signoff experiment. Historical outcomes are used only to nominate
candidates. A design is not admitted to a paper-facing cohort until the current frozen
toolchain reproduces the baseline failure twice and an independently validated legal
intervention reaches strict clean without relaxing the task or signoff gates.

## Historical Evidence Found

The retained knowledge database contains 65 Sky130HD `cleared` events across 44 unique
design names for `density_relief` or `route_relief`. This is substantially richer than
the current neutral smoke screen, whose completed fixtures were Default-ORFS clean.

The following six designs were selected for the retrospective Pilot because their old
records show a concrete before/after physical failure, a named configuration effect,
and a reconstructable pinned public source:

| Candidate | Historical baseline | Historical repair | Outcome | Current disposition |
|---|---|---|---|---|
| `axil_reg_if` | 34 `m3.2` DRC violations | `CORE_UTILIZATION` 20 -> 12 | DRC 34 -> 0 | Rejected: current 20% baseline is strict-clean |
| `eth_mac_mii` | 6 `m3.2` DRC violations | `CORE_UTILIZATION` 25 -> 17 | DRC 6 -> 0 | Current repeatability screen running |
| `can_fifo` | 20 `m3.2` DRC violations | `CORE_UTILIZATION` 20 -> 12 | DRC 20 -> 0 | Pinned and queued |
| `axis_switch` | 32 `m3.2` DRC violations | `CORE_UTILIZATION` 25 -> 17 | DRC 32 -> 0 | Pinned and queued |
| `picorv32_axi` | 33 route residuals | `CORE_UTILIZATION` 25 -> 17 | route completed clean | Pinned and queued |
| `wbsafety` | route timeout with 28 residuals | `CORE_UTILIZATION` 25 -> 17 | route completed clean | Pinned and queued |

Additional historical candidates include `eeprom_top`, `aximrd2wbsp`, `axi_dma`,
`sha256_stream`, `axis_stat_counter`, `spi_controller`, and `memory_controller`. They
should be considered reserves after source identity and repeatability checks.

Two Nangate45 transfer candidates were also verified historically: `i2c_master_axil`
reduced nine antenna violations to zero, and `ftdi_bridge` reduced three to zero with
`antenna_diode_repair`. They are not substitutes for the Sky130HD main cohort.

## Important Protocol Finding

The current neutral screen requires a lower-frequency Default-ORFS strict-clean anchor
before admitting a repeated failure at a higher frequency. That rule is useful for
timing-bound fixtures, but it systematically excludes placement, congestion, DRC, and
antenna failures that do not disappear when frequency is lowered.

There is a second mismatch: Experiment 2 currently freezes die/core area and does not
allow `CORE_UTILIZATION`, while nearly all of the strongest historical R2G repairs use
a bounded `CORE_UTILIZATION` change. The present action space therefore removes the
very repair mechanism that the experiment is intended to evaluate.

Before using these candidates, the protocol should choose one of two defensible scopes:

1. Keep area immutable and test only repairs expressible through the existing common
   allowlisted knobs. In this scope, the six candidates above are mostly ineligible.
2. Treat physical footprint as a bounded optimization variable, expose the same
   `CORE_UTILIZATION` action and limits to every method, preserve source/clock/platform/
   signoff as immutable, and report area together with clean frequency and cost.

The second scope is better aligned with R2G's actual Recipe capability, provided the
same action is available to Vanilla LLM baselines and area growth is explicitly bounded
and scored rather than hidden.

## Admission Procedure

For Pilot mechanics, the six historical designs may be used as a retrospective repair
cohort after exact source reconstruction. For paper claims, retain a separate prospective
cohort selected without consulting Full-R2G outcomes.

Each admitted repair-needed fixture must satisfy all of the following:

1. Pinned repository commit, production top, complete source closure, clock, constraints,
   initial physical configuration, and toolchain digests are frozen.
2. Default ORFS reproduces the same non-environment physical symptom in two independent
   namespaces.
3. The failure has at least one preregistered action available equally to all methods.
4. An independent feasibility run reaches strict clean without changing protected task
   goals or disabling checks.
5. Before/after configuration digests, full outcome vectors, and effect fingerprints are
   retained; target improvement with any global regression is rejected.

## Current-Toolchain Execution Notes

The first reconstructed `axil_reg_if` closure initially omitted `axil_reg_if_rd.v` and
`axil_reg_if_wr.v`. Its digest was internally consistent, but Yosys correctly rejected
the undeclared instantiated modules. The closure was corrected and rebound in a fresh
campaign; all six candidates now pass byte/provenance checks, and five pass an explicit
Yosys hierarchy check. `eth_mac_mii` passes the actual ORFS synthesis path, whose
parameterized `lfsr` elaboration takes about 171 seconds.

The corrected `axil_reg_if` baseline reached strict clean at 100 MHz and 20% target core
utilization in both independent namespaces. Its 12% feasibility arm was also clean, so
the historical failure is not reproducible under the current toolchain and the design is
rejected from the repair-needed cohort. During this smoke test, a detector also falsely
treated the normal path component `r2g_toolchain` as an environment error; the evaluator
was corrected and the immutable validation artifacts were rescored without rerunning EDA.

The active campaign is
`/home/yangao/r2g_exp2_retrospective_repair_screen_2026_08_09_v18c`. Screening is
sequential and fail-fast: a strict-clean first baseline, a synthesis/input failure, or an
inconsistent second failure stops that fixture before the feasibility arm. This preserves
the formal admission rule while avoiding unnecessary physical runs.

## Conclusion

Historical experiments do contain enough promising repair-needed RTL to stop blind
screening. They do not yet constitute a valid formal cohort. The immediate next step is
to reconstruct and independently repeat the six Sky130HD candidates under a corrected,
common action domain; only repeatable candidates should enter Experiment 2 or the
Experiment 3 adaptation/evaluation split.
