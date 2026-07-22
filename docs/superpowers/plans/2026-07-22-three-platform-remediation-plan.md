# R2G V1 Three-Platform Remediation Plan

## 1. Baseline and Goal

This plan converts the findings in
`2026-07-22-three-platform-pilot-analysis.md` into implementation and regression
work. The frozen baseline is Agent commit
`202573701fdc8faa882846d71f409c540c726080`, tested on the same four positive
fixtures and two negative controls on `nangate45`, `sky130hd`, and `sky130hs`.

The work must improve the implementation without weakening the fixed Pilot
registry, Gate definitions, signoff policy, or graph publication policy.
Designs with real DRC, antenna, timing, or LVS failures must remain blocked.

No production Agent code was changed while preparing this plan. The proposed
DRC restage change was tested only in an isolated runtime copy under
`/home/yangao/r2g_drc_restage_experiment_2026_07_22`.

## 2. Exit Criteria

The remediation round is complete when all of the following hold:

1. A DRC entry point never executes synthesis, floorplan, placement, CTS,
   routing, or finish.
2. DRC and LVS reports are bound to an explicit backend run and immutable
   artifact digest, rather than inferred from `latest_run`.
3. A requested production platform must pass strict capability checks before a
   Pilot or production campaign starts.
4. Sky130HS cannot be declared ready unless DEF-derived routing and top-pin
   geometry survive GDS construction and a fresh LVS is executable.
5. A caller-supplied Pilot registry can be prepared, executed, and graded using
   the same registry identity.
6. The unchanged three-platform Pilot and all negative controls are rerun.
   Any remaining failure must represent a real design or explicitly unsupported
   platform capability, not an orchestration or evidence-binding defect.

## 3. Priority Summary

| ID | Priority | Work item | Reliability status | Estimated scope |
|---|---|---|---|---|
| RMD-P0-01 | P0 | Make DRC a frozen-layout, checker-only operation | Experimentally validated | Medium |
| RMD-P0-02 | P0 | Bind signoff reports to an explicit run and artifact digest | Partially validated; digest integration pending | Medium |
| RMD-P0-03 | P0 | Make target-platform readiness fail closed | Validated design with an environment-resolution precondition | Small to medium |
| RMD-P0-04 | P0 | Enforce and validate the Sky130HS GDS import repair | Experimentally validated on a real GCD layout and LVS | Medium |
| RMD-P1-01 | P1 | Remove the Pilot grader's canonical-registry hard coding | Isolated implementation validated | Small |
| RMD-P1-02 | P1 | Complete or explicitly delimit Nangate45 strict signoff | Policy/collateral decision, not a validated code fix | Large, collateral-dependent |
| RMD-P1-03 | P1 | Diagnose the Sky130HD GCD `m3.2` violation as a design case | Failure reproduced; repair recipe not validated | Small to medium |

These grades are supported by
`2026-07-22-remediation-reliability-audit.md`. A proposal marked partial,
conditional, or diagnostic must not be reported as fixed until its listed
acceptance tests pass in the production implementation.

## 4. RMD-P0-01: Frozen-Layout DRC

### Evidence and root cause

All 12 Pilot DRC invocations rebuilt the implementation before KLayout. The
dependency chain is deterministic:

1. `_restage_for_signoff.sh` copies preserved ORFS files into a new workspace.
2. Its timestamp policy marks every result not named `[1-6]_*` as newest.
3. This set includes `results/.../clock_period.txt`.
4. ORFS declares `clock_period.txt` as `SDC_FILE_CLOCK_PERIOD` and includes it
   in `YOSYS_DEPENDENCIES`.
5. It therefore becomes newer than `1_1_yosys_canonicalize.rtlil`.
6. `make drc` follows the stale dependency chain from Yosys through finish,
   then runs KLayout.

The first isolated patch moved non-stage result prerequisites before stage 1.
That removed synthesis through routing but still ran `final_report`, because
`logs/.../6_report.log` is itself an ORFS Make target and had been stamped older
than `6_1_fill.odb`. Stamping each numbered log at the same time as its matching
numbered result removed this final rebuild too.

Validated observations:

- Unmodified GCD: actual Yosys, floorplan, placement, CTS, detailed route, and
  final report commands ran before DRC.
- Patched restage, all 12 preserved Pilot projects: Make reported
  `5_route.odb`, `6_final.def`, `6_final.v`, and `6_final.sdc` up to date.
- Patched GCD DRC: only KLayout ran, completed in 7.39 seconds, and reproduced
  the same six violations.
- Patched medium Sky130HD I2C DRC: only KLayout ran, completed in 52.40 seconds,
  and reproduced zero violations.
- The I2C SHA256 values of `5_route.odb`, `6_final.def`, `6_final.odb`,
  `6_final.gds`, `6_final.v`, `6_final.sdc`, and `6_final.spef` were identical
  before and after DRC.
- A direct KLayout invocation on the preserved backend GDS completed in 7.00
  seconds and reproduced the GCD count of six, proving that ORFS physical-stage
  dependency evaluation is not required for DRC.

### Implementation

Use a checker-only path as the primary fix:

1. Resolve one backend `RUN_*` exactly once and select its preserved
   `results/6_final.gds`.
2. Compute the GDS SHA256 before execution.
3. Resolve the platform DRC deck using the current full, BEOL-only, and approved
   Sky130 sibling-deck policies.
4. Resolve and export `KLAYOUT_CMD` through the existing environment helper,
   then invoke ORFS `scripts/klayout.sh` directly with absolute GDS, deck, log,
   and report paths. Do not invoke the dependency-building `make drc` target.
5. Write DRC output first under the selected backend run, then mirror it to the
   project-level report directory.
6. Record the run tag, GDS digest, deck digest, KLayout version, mode, start
   time, end time, exit code, and violation count in `drc_result.json`.

Also correct the shared restage policy because LVS and compatibility paths still
need restored ORFS state:

- design inputs are oldest;
- non-stage result prerequisites, including `clock_period.txt`, are older than
  stage 1;
- objects are older than their consuming stage results;
- numbered results increase monotonically from stages 1 through 6;
- numbered logs receive the same epoch as their matching stage results;
- no blanket rule labels all non-stage results as newest.

If a Make-based compatibility path remains, add a fail-closed preflight using
`make --question` on `5_route.odb`, `6_final.def`, `6_final.v`, and
`6_final.sdc`. A non-zero result must emit `physical_rebuild_required` and stop
before signoff. The existing single-GDS post-run guard must be expanded to a
digest set covering route ODB, final DEF/ODB/GDS/netlist/SDC/SPEF.

### Files

- `r2g-skills/signoff-loop/scripts/flow/run_drc.sh`
- `r2g-skills/signoff-loop/scripts/flow/_restage_for_signoff.sh`
- `r2g-skills/signoff-loop/tests/test_restage_for_signoff.py`
- New frozen-layout DRC unit and integration tests

### Acceptance tests

1. A fixture with `clock_period.txt` newer than restored Yosys outputs is
   normalized without any physical rebuild.
2. A fixture with an older `6_report.log` is normalized without running finish.
3. Deliberately missing or stale physical artifacts make the DRC wrapper stop
   with `physical_rebuild_required`; they are not silently regenerated.
4. Two consecutive DRC invocations both run a fresh checker and neither runs a
   physical stage.
5. All 12 fixed Pilot projects show zero physical-stage commands in DRC logs.
6. Full artifact digests are unchanged before and after every DRC run.

## 5. RMD-P0-02: Strong Signoff Provenance

### Evidence and root cause

All 12 Pilot `drc.json` files used `provenance.source=latest_run`. The restage
identity marker is written in the ORFS results workspace, while `report_io.py`
searches for a marker under project backend runs. The extractor receives no
explicit run, cannot see the ORFS marker, and falls back to the newest run.

### Implementation

1. Introduce one shared backend-run resolver used by DRC, LVS, RCX, report
   extraction, and graph publication.
2. Create a run-local signoff provenance record before each checker starts.
3. Include `run_tag`, resolved `run_dir`, platform, design, flow variant,
   relevant artifact SHA256, checker deck SHA256, and toolchain fingerprint.
4. Pass that record or an explicit `--run-dir` to `extract_drc.py` and
   `extract_lvs.py`; do not rediscover the run by directory sorting.
5. Require the graph signoff gate to match the report's run tag and artifact
   digest against the selected DEF/GDS provenance manifest.
6. Treat a mismatch or unreadable provenance record as a hard publication
   failure. Legacy evidence may be migrated manually, but must not auto-certify
   an `r2g_clean` dataset.

### Files

- `r2g-skills/signoff-loop/scripts/extract/report_io.py`
- `r2g-skills/signoff-loop/scripts/extract/extract_drc.py`
- `r2g-skills/signoff-loop/scripts/extract/extract_lvs.py`
- `r2g-skills/signoff-loop/scripts/flow/run_drc.sh`
- `r2g-skills/signoff-loop/scripts/flow/run_lvs.sh`
- `r2g-skills/def-graph/scripts/signoff_gate.py`

### Acceptance tests

- Every fresh DRC/LVS report uses `source=explicit` and names the actual run.
- A report from run R1 cannot certify DEF or GDS from run R2, even when design
  and platform names match.
- Copying foreign DEF/GDS bytes into the expected R1 path cannot bypass the
  gate; the recorded artifact digest must differ and publication must fail.
- Moving or adding a newer empty `RUN_*` directory does not alter attribution.
- The 12 Pilot DRC reports receive strong binding and the three physically clean
  Sky130HD designs become eligible for strict graph publication.

## 6. RMD-P0-03: Fail-Closed Platform Readiness

### Evidence and root cause

`check_env.sh` prints strict-signoff capability, but readiness is advisory unless
`R2G_STRICT_PLATFORMS` is manually exported. The Pilot can therefore award ENV
credit and spend hours in ORFS even when the selected platform cannot satisfy
the required signoff policy.

### Implementation

1. Make the requested campaign platform an explicit input to ENV validation.
2. Production and Pilot entry points must first load the normal R2G environment
   resolver and then call `platform_capability.py --strict` for that platform
   with the resolved flow, tool, and PDK paths. Calling the probe from an empty
   shell can falsely report missing installed tools.
3. Separate `installed`, `research_ready`, and `strict_signoff_ready`; only the
   last state may enter a strict V1 campaign.
4. Extend Sky130HS capability checks to verify modern `.lyt` LEF/DEF options and
   the GDS geometry postcondition described below. Tool presence alone is not a
   sufficient readiness oracle.
5. Store the capability result and evidence digest in the campaign manifest.

### Acceptance tests

- An unpatched Sky130HS toolchain is rejected before Fmax search or ORFS.
- A Nangate45 installation without a valid LVS path is rejected before flow.
- A fully prepared Sky130HD installation passes without operator environment
  overrides.
- ENV status cannot be changed after campaign preparation without invalidating
  the campaign manifest.

## 7. RMD-P0-04: Sky130HS GDS Geometry and LVS

### Evidence and root cause

The installed `sky130hs.lyt` contains legacy LEF/DEF reader options. KLayout
maps DEF routing and top-pin geometry to legacy layer/datatype numbers during
GDS merge. Magic treats those layers as unknown, effectively losing the
electrical top-level geometry and extracting a zero-port subcircuit, so all
four original Netgen LVS runs are invalid.
The repository already contains `tools/patch_sky130hs_lyt.py`, and
`install_platform_rules.sh` calls it, but the active installation did not
satisfy the postcondition and ENV did not catch the failure.

### Implementation

1. Make the `.lyt` repair a required, idempotent `eda-install` action whenever
   Sky130HS is selected.
2. Run `patch_sky130hs_lyt.py --check` after installation and fail setup if it
   reports legacy options.
3. Add a small geometry canary that imports a DEF with signal pins, vias, and
   special routing, writes GDS, and verifies those classes are non-empty.
4. Run Magic extraction and Netgen LVS on the canary as the final platform
   postcondition.
5. Invalidate all Sky130HS GDS and LVS evidence produced before the repaired
   toolchain fingerprint; regenerate from finish.

An isolated real-layout experiment validated the proposed repair. The same
preserved Sky130HS GCD DEF produced zero top ports through the legacy template
and 56 top ports through the patched template. With a power-aware netlist
generated from the matching ODB, Netgen reported `Circuits match uniquely`.
The active production toolchain was not modified by this experiment.

### Acceptance tests

- Two consecutive installs are idempotent and leave `--check` green.
- The geometry canary retains top ports, routing, vias, and power geometry.
- All four fixed Sky130HS fixtures regenerate fresh GDS and produce valid LVS
  verdicts rather than `error` or zero-port extraction.

## 8. RMD-P1-01: Pilot Registry Identity

### Evidence and root cause

`tools/run_v1_pilot.py` loads the caller-supplied `--registry`, but
`r2g_validation/pilot.py::grade_campaign` compares the campaign digest against
the hard-coded canonical `pilot_registry.yaml`. Platform-specific registry
mirrors therefore require an isolated workaround during grading.

### Implementation and acceptance

Pass the resolved registry path or expected digest into `grade_campaign`, and
compare the campaign manifest against that exact digest. Add tests for the
canonical registry, a valid custom registry, a mismatched registry, and a
registry modified after campaign preparation. The mismatch cases must fail
closed; valid custom registries must grade normally.

This implementation shape was validated in an isolated repository copy: the
Sky130HS custom registry graded normally, the canonical registry remained
compatible, and grading the Sky130HS campaign with the Nangate45 registry was
rejected with exit code 2. Production Agent code remains unchanged.

## 9. RMD-P1-02: Nangate45 Strict-Signoff Scope

Nangate45 currently lacks a complete reliable strict path in this installation:
LVS is skipped, I2C has four DRC/antenna violations, and SHA256 full DRC becomes
stuck. This is not fixed by changing graph policy.

This item is deliberately a support decision, not a promised remediation.
The current capability probe independently confirms missing LVS and antenna
capability. Installing collateral is only a candidate path and must still pass
the unchanged strict Pilot before Nangate45 can be declared supported.

The V1 owner must choose and record one of two outcomes:

1. Complete support by installing and validating LVS collateral, antenna rules
   and diodes, and a bounded full-DRC path on the fixed fixtures; or
2. Declare Nangate45 research-only for this V1 and remove it from the strict
   production platform list without changing historical Pilot results.

If strict support remains required, acceptance is a valid DRC, LVS, antenna,
timing, and RCX verdict on the unchanged fixed campaign. BEOL-only DRC must not
be reported as full strict DRC.

## 10. RMD-P1-03: Sky130HD GCD Design Violation

The six GCD `m3.2` spacing violations are a real design result, not a common
Agent failure. Preserve the failing case as a regression fixture. Diagnose the
coordinates and routing context, apply only legal route or density
interventions, and rerun from the earliest affected stage. The case passes only
when full DRC is clean without relaxing the deck, clock target, or signoff Gate.
The same six violations were reproduced by the original DRC, the isolated
checker-only path, and direct KLayout. No route or density intervention has yet
been validated, so this item must remain an investigation rather than a claimed
fix.

## 11. Execution Order

1. Add failing regression tests for restage ordering, frozen-layout DRC, and
   explicit report provenance.
2. Implement direct checker-only DRC plus the corrected restage ordering.
3. Implement explicit run and digest binding for DRC/LVS and graph publication.
4. Make strict platform capability mandatory and add the Sky130HS postcondition.
5. Repair the Pilot registry binding.
6. Reinstall or repair Sky130HS, regenerate GDS, and rerun its Pilot.
7. Resolve the Nangate45 support decision and rerun the relevant campaign.
8. Rerun all three unchanged Pilots and archive scorecards, logs, manifests,
   artifact digests, and toolchain fingerprints.

## 12. Non-Goals

- Do not relax DRC, LVS, timing, antenna, provenance, or graph validation Gates
  to improve the score.
- Do not mark BEOL-only checks as full DRC.
- Do not regenerate a layout inside a signoff checker.
- Do not infer report ownership from the newest directory when explicit run
  identity is available.
- Do not treat a real design violation as an orchestration regression.
