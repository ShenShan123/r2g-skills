# Three-Platform R2G V1 Pilot Analysis

## 1. Scope

This analysis compares the fixed R2G V1 Pilot on `nangate45`, `sky130hd`, and
`sky130hs` at Agent commit `202573701fdc8faa882846d71f409c540c726080`.
All campaigns used the same four pinned positive RTL fixtures, the same fixed
negative controls, the same 11 Gate definitions, and 49 applicable Gate cells.
Platform-specific Fmax search was rerun rather than reusing constraints from
another technology.

The three scorecards are:

- Nangate45: `/home/yangao/r2g_v1_pilot_2026_07_22_run02/reports/pilot_report.md`
- Sky130HD: `/home/yangao/r2g_v1_pilot_sky130hd_2026_07_22_run02/reports/pilot_report.md`
- Sky130HS: `/home/yangao/r2g_v1_pilot_sky130hs_2026_07_22_run01/reports/pilot_report.md`

## 2. Comparative Results

| Platform | Gate cells | Strict E2E | FLOW | Physical signoff observation | Verified graph views |
|---|---:|---:|---:|---|---:|
| nangate45 | 37/49 (75.5%) | 0/4 | 4/4 | No fixture was strict-clean | 2/4 |
| sky130hd | 39/49 (79.6%) | 0/4 | 4/4 | UART, I2C, and SHA256 were physically strict-clean | 3/4 |
| sky130hs | 33/49 (67.3%) | 0/4 | 4/4 | Route/DRC/timing/RCX were clean, but LVS was invalid for all fixtures | 0/4 |

The total measured execution times were approximately 4.00 hours for
Nangate45, 1.21 hours for Sky130HD, and 0.81 hours for Sky130HS. These times are
not pure platform performance measurements because failure modes, DRC behavior,
and platform-specific Fmax priors differ.

## 3. What Is Common Across All Platforms

The front half of the Agent is stable in this Pilot. `ENV`, `ACQ`, `SYNTH`,
`RTL2FLOW`, `CONSTRAINT`, `FLOW`, and `LEARNING` received full credit on all
three platforms. All four pinned RTL designs were acquired, source-verified,
synthesized, promoted, constrained by platform-specific Fmax search, and taken
through all six ORFS stages.

Three cross-platform defects were reproduced:

1. **DRC implicitly rebuilds the implementation.** Every one of the 12 DRC
   invocations restarted Yosys synthesis and then rebuilt floorplan, placement,
   CTS, route, and finish before running KLayout. The immediate cause is the
   signoff restage timestamp policy: `clock_period.txt`, which is a Yosys input,
   is treated as a newest non-stage output and becomes newer than the restored
   synthesis artifacts. ORFS therefore considers synthesis stale. This wastes
   runtime and can make the signoff workspace internally diverge from the frozen
   backend run.

2. **DRC report provenance is weak.** All 12 `drc.json` reports were stamped
   with `source=latest_run`. The restage identity marker is written inside the
   ORFS results directory, while report extraction either searches the project
   backend or receives no explicit backend run. The graph signoff gate therefore
   records `report_binding=weak`, even when the selected run tag is correct.

3. **The target platform capability is not a mandatory ENV postcondition.**
   `check_env.sh` prints platform readiness but treats it as advisory unless
   `R2G_STRICT_PLATFORMS` is explicitly set. Consequently the Pilot awards ENV
   credit even when strict signoff is unreachable. The Sky130HS capability probe
   also checks only the presence of Magic, Netgen, and PDK files; it does not
   verify that `sky130hs.lyt` uses the required modern LEF/DEF import options.

The fixed negative controls passed on all three platforms. In particular, the
graph gate refused dirty or incomplete signoff evidence instead of publishing it
as a clean dataset.

## 4. Platform-Specific Findings

### 4.1 Nangate45

Nangate45 is not currently strict-signoff ready in this installation. GCD and
UART reached clean routing and full DRC, but LVS was skipped. I2C retained four
DRC/antenna violations. SHA256's full KLayout DRC became stuck and antenna status
remained unknown. The two loadable graph datasets are therefore research-tier
outputs, not strict clean publications.

This is mainly a platform collateral and signoff capability problem, in addition
to the common report-binding and DRC-rebuild defects. It cannot be solved only by
changing graph publication policy.

### 4.2 Sky130HD

Sky130HD is the strongest of the three current paths. UART, I2C, and SHA256 have
strict-clean physical signoff manifests: route and antenna are clean, full DRC
and Netgen LVS pass, timing is met, and RC extraction is complete. GCD alone has
six `m3.2` minimum met3 spacing violations.

Nevertheless, the formal Pilot reports 0/4 strict end-to-end results because the
three physically clean designs retain weak DRC report binding. Their five graph
views pass independent verification but are published only as `research`, not
`r2g_clean`. Sky130HD is therefore primarily blocked by the common provenance
defect, plus the design-specific GCD DRC violation.

### 4.3 Sky130HS

All four Sky130HS fixtures completed ORFS and produced clean routing, full DRC,
timing, and RCX evidence. However, the installed `sky130hs.lyt` still contains
legacy LEF/DEF reader options. KLayout's DEF-to-GDS merge drops routing and top
pin geometry. Magic consequently extracts a top-level SPICE subcircuit with zero
ports, so Netgen cannot perform a valid LVS comparison.

The failure reproduced on all four fixtures and is therefore a platform setup
defect, not four independent RTL failures. The Agent correctly records
`lvs:error` with no mismatch count and blocks all graph publication. The repo
already contains `tools/patch_sky130hs_lyt.py`, but the active toolchain had not
applied it and the ENV Gate did not detect that postcondition failure.

## 5. Interpretation

The three platforms do **not** have the same primary problem:

- Nangate45 is limited by missing or insufficient strict-signoff collateral and
  by design-level DRC/antenna behavior.
- Sky130HD mostly completes physical signoff, but trustworthy publication is
  blocked by weak report provenance; one design also has a real DRC failure.
- Sky130HS has a systematic GDS construction defect that makes every LVS result
  invalid before graph publication.

The shared Agent defects are the DRC-triggered implementation rebuild, weak DRC
report binding, and non-mandatory platform readiness. The identical 0/4 strict
E2E headline therefore hides substantially different physical-flow outcomes.

## 6. Recommended Fix Order

1. Make target-platform strict capability a mandatory, fail-closed ENV Gate.
   Extend the Sky130HS capability probe to validate the `.lyt` LEF/DEF options.
2. Apply and verify the Sky130HS `.lyt` repair during `eda-install`, then rerun
   the same fixed campaign from fresh GDS generation.
3. Bind DRC/LVS reports to an explicit backend run or an artifact-digest-bearing
   signoff manifest instead of `latest_run` inference.
4. Correct restage timestamps so `clock_period.txt` and every other true input
   are older than restored synthesis outputs. Add a dry-run dependency check
   that rejects DRC execution if `make drc` would rebuild physical stages.
5. Complete or explicitly narrow Nangate45 strict-signoff support before treating
   it as a V1 production platform.
6. Repair the Pilot grader so a caller-supplied registry is used consistently;
   Sky130 platform variants currently require an isolated registry mirror for
   grading because the registry hash check is tied to the canonical file.

After these common and platform-specific fixes, the same pinned Pilot should be
rerun without changing its Gate definitions. Only then can score improvements be
attributed to Agent fixes rather than to moving the acceptance criteria.
