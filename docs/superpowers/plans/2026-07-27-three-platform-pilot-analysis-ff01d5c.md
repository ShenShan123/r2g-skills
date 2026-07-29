# Three-Platform R2G V1 Fixed-Pilot Analysis

## Scope

This report evaluates R2G Agent commit
`ff01d5ccd62fa53e167446bcf33dce9911bda288` with the unchanged fixed Pilot on
Nangate45, Sky130HD, and Sky130HS. Each campaign used the same four pinned
positive fixtures (GCD, WBUART32, AXI-Lite I2C, and Secworks SHA-256), two
negative fixtures, 11 gate types, and 49 applicable gate cells.

The production path was:

`RTL acquisition -> promotion -> Fmax search -> ORFS -> strict signoff -> graph conversion -> publication`.

This is a fixed-fixture regression experiment, not a held-out generalization
estimate. The three campaigns ran concurrently with one worker and four cores
per flow, so their wall times must not be compared as platform-performance
measurements.

| Component | Version |
|---|---|
| R2G Agent | `ff01d5ccd62fa53e167446bcf33dce9911bda288` |
| ORFS | `a5ff7ef7dac4338e6e5fad7710b85fc6c8f3503c` |
| OpenROAD | `26Q3-318-g6b9d7fb806` |
| KLayout | `0.29.12` |
| Magic / Netgen | `8.3.464` / `1.5.272` |

Before execution, the strict capability probe reported `STRICT-READY` for all
three platforms. The Pilot evaluator passed 13 unit tests and registry lint.
The focused production tests for result-vector comparison, effective resumed
stages, and capability environment binding passed 32/32; the complete
`eda-install` test suite passed 40/40.

## Scorecard

| Platform | Passed gate cells | Execution coverage | Attempted-cell pass rate | Strict-clean E2E |
|---|---:|---:|---:|---:|
| Nangate45 | **45/49 (91.8%)** | 46/49 (93.9%) | 97.8% | **3/4** |
| Sky130HD | **45/49 (91.8%)** | 46/49 (93.9%) | 97.8% | **3/4** |
| Sky130HS | **45/49 (91.8%)** | 46/49 (93.9%) | 97.8% | **3/4** |

All five applicable negative-control cells passed on all three platforms.
Every positive fixture passed ENV, ACQ, SYNTH, RTL2FLOW,
CONSTRAINT, and FLOW. Every strict-clean fixture produced five independently
verified graph views and one atomic publication.

Scorecards:

- [Nangate45 Pilot report](/home/yangao/r2g_v1_pilot_2026_07_27_ff01d5c_nangate45_run01/reports/pilot_report.md)
- [Sky130HD Pilot report](/home/yangao/r2g_v1_pilot_2026_07_27_ff01d5c_sky130hd_run01/reports/pilot_report.md)
- [Sky130HS Pilot report](/home/yangao/r2g_v1_pilot_2026_07_27_ff01d5c_sky130hs_run01/reports/pilot_report.md)

## Confirmed Repairs

### Global regression is no longer learned as a win

Sky130HS SHA-256 reproduced the previous adverse transition: applying
`density_relief` improved the target DRC count from 10 to 8, but route changed
from 0 to 32 violations and LVS became a `pin_pdn_short` mismatch. The updated
Agent recorded:

- `verdict="regression"`;
- `global_regressions=["route_regression:0->32"]`;
- restored configuration rather than accepting the intervention;
- no positive `fix_events` row for this live attempt.

The LEARNING gate passed and graph publication remained blocked. This directly
closes the former P0 failure in which the same local improvement was learned as
a win despite a worse global physical result.

### Resumed-flow status is consistent across consumers

Sky130HS SHA-256 used digest-bound resumed-stage evidence. FLOW accepted the
effective six-stage execution, PPA/ingestion agreed that ORFS was complete, and
LEARNING passed. The previous contradiction in which graph gating reported
complete while the learner reported partial was not reproduced.

### Capability metadata matches the signoff environment

Every completed Sky130HD, Sky130HS, and Nangate45 signoff manifest reports
`platform_capability.strict_signoff_ready=true`. Clean manifests no longer
claim that LVS capability is missing, and dirty manifests retain the same
truthful capability result. The previous metadata/execution contradiction was
not reproduced.

### Bounded full-deck DRC reaches an auditable terminal state

Nangate45 SHA-256 reached the configured 7,200-second KLayout limit and cleanup
grace period. The Agent recorded `status="stuck"`, `exit_code=124`,
`reason="klayout_polygon_op_no_progress"`, and
`stuck_at_rule="FreePDK45.lydrc:131"`, together with run identity, GDS/deck
digests, design scale, wall time, and peak RSS. The ledger transitioned to
`escalated` with `reason="signoff_stuck_scan"`, graph publication was blocked,
the campaign continued to grading, and no checker process remained afterward.
This confirms the bounded-process update contains the tool limitation without
misclassifying or hanging the Agent.

## Confirmed Production Defect

### P1: Re-running `eda-install` does not deterministically reuse deployed pins

**Evidence.** With no explicit `R2G_ENV_FILE`, the current bootstrap detector
selected `/opt/OpenROAD-flow-scripts` and the ambient
`/data2/eda/llmrc_align/pdks/open_pdks`, even though both deployed flow skills
already pinned:

- `ORFS_ROOT=/home/yangao/r2g_toolchain/OpenROAD-flow-scripts`;
- `PDK_ROOT=/home/yangao/.conda/envs/eda/share/pdk`.

The bootstrap consequently attempted strict platform-rule installation in the
wrong, read-only ORFS checkout and failed. Running the same detector with
`R2G_ENV_FILE` bound to the deployed pin file selected the intended toolchain;
strict installation and all three capability probes then passed.

**Root cause.** The `eda-install` copy of `_env.sh` has no local pin file and
does not consult the existing `signoff-loop` or `def-graph` pins during its
detect/plan phase. `write_env_local.sh` recalls those pins only after planning
and installation, which is too late to make repeat bootstrap idempotent.

**Impact.** A routine repair or upgrade can inspect or mutate a different ORFS
and PDK from the ones used by production runs. This undermines setup
reproducibility and can falsely report missing strict-signoff collateral.

**Required behavior.** Repeat bootstrap must resolve one canonical existing
toolchain before planning. If the two deployed pins disagree, or an explicit
environment conflicts with the selected pin, bootstrap should stop and require
an explicit choice instead of silently switching installations.

This setup defect did not affect the Pilot score: all three campaigns explicitly
bound the validated intended toolchain.

## Physical-Design and Tool Results

- **Sky130HD GCD:** the flow completed, but six reproducible `m3.2`
  minimum-M3-spacing violations remained. Strict signoff and publication were
  correctly blocked.
- **Sky130HS SHA-256:** baseline `li.3` violations remained. The only attempted
  repair was correctly rejected as globally regressive, after which the
  strategy catalog was exhausted. The design remained dirty and unpublished.
- **Nangate45 GCD, WBUART32, and I2C:** all are strict-clean. I2C cleared nine
  antenna violations through a route-stage repair and subsequently passed DRC,
  LVS, route, timing, RCX, graph verification, and publication.
- **Nangate45 SHA-256:** KLayout did not finish the full DRC deck within 7,200
  seconds. The Agent recorded the result as stuck, escalated, and blocked
  publication. This is a checker-scalability limit under the frozen budget,
  not a false clean result.

These outcomes must not be converted into passes by relaxing clock or area
constraints, weakening DRC/LVS, replacing the full deck with an advisory
checker, or publishing a dirty graph.

## Conclusion

The latest production update closes all three Agent defects reported by the
previous fixed Pilot: global-regression learning, resumed-stage inconsistency,
and false capability metadata. The completed HD/HS failures are currently
physical non-closure cases that the Agent handles fail-closed, not evidence of
incorrect publication or learning.

One new P1 setup defect is confirmed: repeat `eda-install` is not deterministic
unless the intended pin file or paths are supplied explicitly. All three
platforms otherwise reached the same 45/49 score and 3/4 strict-clean
end-to-end result, with each non-clean design handled fail-closed.
