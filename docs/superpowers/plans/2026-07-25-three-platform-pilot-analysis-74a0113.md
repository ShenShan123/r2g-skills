# Three-Platform R2G V1 Fixed-Pilot Analysis

## Scope and Interpretation

This report audits R2G Agent commit
`74a0113286ffa6b0e890b3f87125f07bc282206d` using the fixed four-design Pilot
executed on July 24-25, 2026. The production path under test was:

`RTL acquisition -> promotion -> Fmax search -> ORFS -> strict signoff -> graph conversion -> publication`.

Each platform used the same four pinned positive fixtures: GCD, WBUART32,
AXI-Lite I2C, and Secworks SHA-256. The registry also contains two negative
fixtures that exercise five applicable negative-control gate cells. There are
11 gate types and 49 applicable cells per platform.

This is a regression and acceptance Pilot, not a held-out generalization
experiment. Its 3/4 result must not be interpreted as a population success
rate for arbitrary open-source RTL.

| Component | Version |
|---|---|
| R2G Agent and Pilot | `74a0113286ffa6b0e890b3f87125f07bc282206d` |
| ORFS | `a5ff7ef7dac4338e6e5fad7710b85fc6c8f3503c` |
| OpenROAD | `26Q3-318-g6b9d7fb806` |
| KLayout | `0.29.12` |
| Magic / Netgen | `8.3.464` / `1.5.272` |

The strict-install capability probe reported `STRICT-READY` for Nangate45,
Sky130HD, and Sky130HS before execution. The Pilot evaluator's 13 unit tests
and registry lint passed. The Nangate45 campaign was later resumed after an
orphaned I2C `fixing` state was reclaimed; this intervention is disclosed
because that campaign is not a single uninterrupted timing sample.

## Scorecard Results

| Platform | Passed gate cells | Execution coverage | Attempted-cell pass rate | Strict-clean E2E |
|---|---:|---:|---:|---:|
| Nangate45 | **45/49 (91.8%)** | 46/49 (93.9%) | 97.8% | **3/4** |
| Sky130HD | **45/49 (91.8%)** | 46/49 (93.9%) | 97.8% | **3/4** |
| Sky130HS | **44/49 (89.8%)** | 46/49 (93.9%) | 95.7% | **3/4** |

Execution coverage is lower than 49 because a failed strict-signoff cell
blocks downstream graph and publication cells. The attempted-cell pass rate
therefore must not be presented alone as overall capability.

All five negative-control gate cells passed on all three platforms. Every
positive fixture passed ENV, ACQ, SYNTH, RTL2FLOW, CONSTRAINT, and FLOW. Every
strict-clean fixture produced five independently verified graph views and an
atomic publication.

The published clean sets are:

- **Nangate45:** GCD, WBUART32, and AXI-Lite I2C.
- **Sky130HD:** WBUART32, AXI-Lite I2C, and SHA-256.
- **Sky130HS:** GCD, WBUART32, and AXI-Lite I2C.

Primary scorecards:

- [Nangate45 Pilot report](/home/yangao/r2g_v1_pilot_2026_07_24_74a0113_nangate45_run01/reports/pilot_report.md)
- [Sky130HD Pilot report](/home/yangao/r2g_v1_pilot_2026_07_24_74a0113_sky130hd_run01/reports/pilot_report.md)
- [Sky130HS Pilot report](/home/yangao/r2g_v1_pilot_2026_07_24_74a0113_sky130hs_run01/reports/pilot_report.md)

## Confirmed Improvements

### Strict platform collateral is available

The direct capability probe found DRC, LVS, antenna, timing, and RC extraction
support for all selected platforms. Missing platform collateral was not the
cause of the three current non-clean fixture outcomes.

### Digest-bound lineage is enforced by the graph gate

Sky130HS SHA-256 passed the FLOW cell because the graph-side resolver verified
its digest-bound parent lineage and final outputs. This confirms that the
previous null-digest graph-gate defect is no longer reproduced. It does not
prove that every downstream consumer interprets resumed execution correctly;
the LEARNING failure below demonstrates that they do not.

Nangate45 I2C eventually reached strict clean in a fresh six-stage backend run
after the ledger reclaimed its stranded `fixing` state. It is valid positive
evidence for full-flow publication, but it should not be cited as proof of
partial-resume consistency.

### Bounded full-deck DRC reaches a terminal state

Nangate45 SHA-256 reached the 7,200-second KLayout limit and the 60-second
cleanup grace period. Its result records `status=stuck`, `exit_code=124`, and
`reason=klayout_polygon_op_no_progress` at `FreePDK45.lydrc:131`. The engineer
ledger reached terminal `escalated` state with `signoff_stuck_scan`, graph
publication was blocked, and host-side process inspection after termination
found no surviving checker process. This is the expected fail-closed result.

## Confirmed Agent Defects

### P0: A globally regressive live repair is learned as a win

**Evidence.** On Sky130HS SHA-256, `density_relief` reduced the target DRC
count from 10 to 8. The pre-repair detailed-route report had zero route
violations; the repaired run had 32 route violations and an LVS
`top_pin_mismatch`. Nevertheless, `reports/fix_log.jsonl` recorded
`verdict="applied"`, and `ingest_run.py` maps an improving `applied` event to
`win`. The publication gate correctly rejected the design, but the learning
path still created positive evidence for the intervention.

**Impact.** Recipe memory can reinforce a change that improves one local
metric while making the complete physical result worse. Later ranking and
promotion can therefore be biased by false-positive evidence.

**Root cause.** The live repair loop accepts target-check improvement without
using the global non-regression policy already required by the A/B judge.

**Required behavior.** A live intervention may become positive evidence only
when route, DRC, LVS, timing, RCX, ORFS completion, and protected design
constraints remain non-regressive. Otherwise it must be recorded as
`regression` or `inconclusive` and excluded from positive Recipe evidence.

### P1: Resumed-flow completion has conflicting consumer semantics

**Evidence.** For Sky130HS SHA-256, the FLOW gate accepted digest-verified
parent lineage and resolved ORFS as complete. The LEARNING gate then failed
because the knowledge record reported `orfs_status="partial"` despite
`disk_ok=True`. The local rerun stage log did not contain the inherited
upstream stages, and PPA extraction/ingestion did not apply the same effective
lineage semantics as the graph gate.

**Impact.** Graph publication logic, PPA metadata, diagnostics, and learner
statistics can describe the same physical execution differently.

**Root cause.** Effective stage evidence is independently reconstructed by
multiple consumers instead of being resolved through one versioned contract.

**Required behavior.** Graph gating, PPA extraction, and ingestion must use one
resolver that admits only digest-verified, identity-matched parent stages and
returns the same effective ORFS status.

### P1: Signoff capability metadata contradicts actual execution

**Evidence.** All eight examined final Sky130HD/HS signoff manifests reported
`strict_signoff_ready=false` and `missing=["lvs"]`. Six of those manifests also
reported `strict_clean=true`, and Netgen LVS actually ran successfully. A
direct capability probe under the resolved environment reported both
platforms `STRICT-READY`.

**Impact.** The manifest is internally contradictory and cannot serve as
trustworthy environment/provenance evidence, even when the physical checks
themselves are valid.

**Root cause.** Parent signoff entrypoints invoke the manifest builder without
first resolving the same shared environment used by child DRC/LVS scripts.
Ambient stale paths can therefore affect metadata but not the checks.

**Required behavior.** Capability metadata must be generated from the exact
resolved environment used for signoff and must be consistency-checked before a
manifest can declare `strict_clean=true`.

## Physical-Design and Tool Limits

- **Nangate45 SHA-256:** full-deck KLayout DRC did not complete within the
  frozen budget. The Agent now records and contains this correctly. Improving
  checker scalability is separate from Agent correctness.
- **Sky130HD GCD:** six reproducible `m3.2` minimum-M3-spacing violations remain.
  Publication was correctly blocked.
- **Sky130HS SHA-256:** the attempted intervention left 8 DRC violations,
  introduced 32 route violations, and produced an LVS mismatch. Its physical
  non-closure is a design/Recipe-coverage result; learning that intervention as
  a win is the Agent defect.

None of these cases should be converted into a pass by weakening the DRC deck,
LVS requirement, protected constraints, or publication gate.

## Audit Conclusion

The logs support three production-Agent defects: one P0 learning-safety defect
and two P1 consistency/provenance defects. No additional production defect was
confirmed from this fixed cohort. The original report was not fully rigorous
because it counted six rather than five negative-control cells, overstated the
Nangate45 I2C result as resume validation, omitted the interrupted-campaign
qualification, and did not distinguish regression coverage from held-out
generalization. Those points are corrected here.

The scorecards establish strong fixed-fixture regression performance and
correct fail-closed publication of dirty designs. They do not yet justify the
claim that Agent-layer behavior is defect-free or that arbitrary unseen RTL
will achieve a 75% strict-clean rate.
