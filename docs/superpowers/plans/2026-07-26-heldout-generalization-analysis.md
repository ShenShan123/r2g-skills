# R2G Agent Three-Platform Held-Out Analysis

## Scope

This experiment evaluates R2G Agent commit
`74a0113286ffa6b0e890b3f87125f07bc282206d` on four RTL designs absent from the
fixed Pilot cohort:

| Fixture | Top module | RTL files | Design class |
|---|---|---:|---|
| PicoRV32 | `picorv32` | 1 | RV32I CPU |
| Secworks AES | `aes` | 7 | cryptographic core |
| SERV | `serv_synth_wrapper` | 18 | bit-serial RISC-V CPU |
| Secworks ChaCha | `chacha` | 3 | stream-cipher core |

Sources were pinned by repository revision and compilation-input digest before
scoring. Each platform used an isolated runtime and knowledge database. The
same 11 gate types, 49 applicable gate cells, and two negative fixtures were
used on Nangate45, Sky130HD, and Sky130HS.

This is a held-out design test of processing, qualification, physical
implementation, signoff, and graph publication. Because candidates were
supplied through a fixed list, it does not test autonomous Internet discovery.
Because the four designs share one online-learning campaign per platform, it
also does not estimate fully independent per-design success without within-
campaign learning.

An initial, unscored Nangate45 qualification attempt used a ZipCPU DMA source
whose declared RTL closure omitted `wbarbiter`. The Agent correctly rejected
it during synthesis qualification. That incomplete fixture was replaced with
ChaCha before the scored run; only Nangate45 `run02` is included below.

## Results

| Platform | Gate cells | Coverage | Attempted pass rate | Strict-clean E2E |
|---|---:|---:|---:|---:|
| Nangate45 | **37/49 (75.5%)** | 40/49 | 92.5% | **1/4 (25%)** |
| Sky130HD | **49/49 (100%)** | 49/49 | 100% | **4/4 (100%)** |
| Sky130HS | **44/49 (89.8%)** | 46/49 | 95.7% | **3/4 (75%)** |
| Combined | **130/147 (88.4%)** | 135/147 | 96.3% | **8/12 (66.7%)** |

All 15 negative-control cells passed. All 12 platform-design pairs passed ENV,
ACQ, SYNTH, RTL2FLOW, CONSTRAINT, and FLOW. Eight pairs reached strict-clean
publication with all five graph views independently verified.

| Fixture | Nangate45 | Sky130HD | Sky130HS |
|---|---|---|---|
| PicoRV32 | DRC stuck; blocked | Published | Published |
| AES | DRC stuck and LVS dirty; blocked | Published | Residual DRC; blocked |
| SERV | Published | Published | Published |
| ChaCha | DRC stuck; blocked | Published | Published |

Primary scorecards:

- [Nangate45 scorecard](/home/yangao/r2g_v1_heldout_2026_07_25_74a0113_nangate45_run02/reports/pilot_report.md)
- [Sky130HD scorecard](/home/yangao/r2g_v1_heldout_2026_07_26_74a0113_sky130hd_run01/reports/pilot_report.md)
- [Sky130HS scorecard](/home/yangao/r2g_v1_heldout_2026_07_26_74a0113_sky130hs_run01/reports/pilot_report.md)

## Positive Generalization Evidence

The Agent accepted four previously unscored source closures, preserved their
byte-level provenance, generated nonempty synthesis graphs, promoted them into
platform projects, searched and stamped platform-specific clock constraints,
and completed all six ORFS stages on every platform-design pair.

Sky130HD is the strongest positive control: all four designs passed routing,
antenna, DRC, LVS, timing, and RC extraction, then produced verified atomic
graph publications. Sky130HS independently published three designs. These
results show that the production path is not limited to the original fixed
Pilot fixtures.

Every dirty or incomplete result was blocked before graph publication. The
experiment therefore produced no known false-clean dataset.

## Confirmed Agent Defects

### P1-HO-01: Resume completion remains inconsistent across consumers

**Evidence.** Sky130HS AES initially had 30 `li.3` DRC violations. The Agent
applied `density_relief`, resumed from floorplan, and reduced the count to 10
while route and LVS remained clean. The graph-side FLOW evaluator verified the
digest-bound resume and classified ORFS complete. The LEARNING gate failed
because ingestion stored `orfs_status="partial"` for the same execution.

**Impact.** The graph gate, PPA/knowledge path, and learner can assign different
completion states to one valid digest-bound execution. This distorts learning
statistics and diagnostics even though publication remains blocked here by
real residual DRC.

**Status.** This reproduces the stage-evidence consistency defect previously
seen on the fixed Sky130HS SHA-256 fixture. It is not a new defect, but the
held-out reproduction shows that it is not design-specific.

### P1-HO-02: Capability manifests remain internally contradictory

**Evidence.** All eight final Sky130HD and Sky130HS signoff manifests declare
`platform_capability.strict_signoff_ready=false` and `missing=["lvs"]`.
Seven of those manifests simultaneously declare `strict_clean=true`, and the
bound Netgen reports show clean LVS. Direct preflight reported both platforms
`STRICT-READY`.

**Impact.** Physically valid publications carry false environment/provenance
metadata. Sky130HD still scored 49/49, so the score must not be interpreted as
proof that every production manifest field is truthful.

**Status.** This is the same environment-resolution defect reproduced by the
fixed Pilot. It remains a production-Agent issue rather than a physical-tool
failure.

No new P0 Agent defect was confirmed. In particular, the Sky130HS AES repair
was incomplete but did not create a route or LVS regression. The previously
reported P0 case in which a globally regressive repair is learned as a win was
neither reproduced nor invalidated by this cohort.

## Physical and Tool Limits

### Nangate45 full-deck DRC scaling

SERV completed full KLayout DRC in approximately 187 seconds with zero
violations. PicoRV32, AES, and ChaCha each reached the 7,200-second bound at
`FreePDK45.lydrc:131` and were recorded as
`klayout_polygon_op_no_progress`, `exit_code=124`. The campaign continued to a
terminal state and blocked all three publications.

| Fixture | Synth cells | DRC | LVS |
|---|---:|---|---|
| PicoRV32 | 9,446 | stuck at bound | clean |
| AES | 21,261 | stuck at bound | 20 mismatches |
| SERV | 962 | clean | clean |
| ChaCha | 20,058 | stuck at bound | clean |

Nangate45 signoff consumed 23,828.9 seconds, compared with 4,814.8 seconds on
Sky130HD and 5,959.5 seconds on Sky130HS. The three deterministic DRC timeout
windows dominate the difference. This is a checker/deck scalability result,
not evidence of repeated upstream ORFS execution.

### Platform-specific physical closure

Sky130HS AES improved from 30 to 10 DRC violations but did not become clean.
The Agent exhausted its applicable Recipe catalog and correctly blocked graph
generation. Nangate45 AES also retained 20 LVS mismatches. These are valid
physical or extraction outcomes until a controlled, globally non-regressive
repair proves otherwise.

## Experimental Limits

- Four designs are sufficient for a strong smoke test, not a statistically
  precise population estimate.
- AES and ChaCha share a source organization and domain, so the cohort is not
  maximally diverse.
- Fixed candidate URLs test unseen-source ingestion, not autonomous discovery.
- Designs within one platform run share online memory. A paper should separate
  continual-agent evaluation from a no-update frozen-memory evaluation.
- Platform yields must remain separate; the combined 66.7% figure must not hide
  Nangate45's 25% strict-clean yield.

## Conclusion

The held-out campaign demonstrates real cross-design transfer: all four unseen
designs traversed acquisition through ORFS on all three platforms, Sky130HD
published 4/4, Sky130HS published 3/4, and every unsafe result failed closed.
It also reproduces two known Agent consistency defects and exposes a strong
platform dependence in strict-signoff yield. The defensible claim is therefore
successful held-out feasibility with trustworthy publication blocking, not
universal RTL-to-graph success.
