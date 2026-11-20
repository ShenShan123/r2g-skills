# ASAP7 DRC Diagnosis and Passing Configuration

The gcd_pin_window_v2 project completes RTL→GDS and full unchanged official Calibre signoff: **334 DRC checks, zero violations, and transistor LVS CORRECT**. Changes are isolated flow/LEF abstractions. Official rules, native cell GDS, CDL, Liberty, and production platform files remain unchanged; no rules are excluded.

| Metric | Result |
|---|---:|
| Complete flat DRC | 334 checks / zero results |
| Complete transistor LVS | CORRECT |
| Clock / setup margin | 1 ns / 0.438514 ns |
| Hold margin / setup and hold TNS | 41.2595 ps / zero |
| Cell area, including taps and excluding fillers | 47.9245 µm² |
| Die / core area | 262.44 / 197.122 µm² |
| Standard cells / all instances | 499 / 1402 |
| Final STA estimated power | 0.309148 mW |
| Historical integer-stage sum / reported peak memory | 53 s / 1079 MB |
| DRC / LVS wall time | 4.481 / 7.042 s |

PPA comes from [6_report.json](../gcd_pin_window_v2/backend/RUN_2026-11-16_06-00-47_2002719_0f7f/logs/6_report.json). RVT FF Liberty uses 1 ps; timing is converted to ns. The integer-truncated stage sum above is not end-to-end wall time; the main report uses the precise 65.48 s sum. See [summary](pin_window_v2_summary.json). Initial LVS property limit was 100000; an independent 1024-budget recheck also returns CORRECT and is the accepted result.

## Minimal passing configuration

See [reproduction configuration](MINIMAL_CONFIG.md), [config.mk](../gcd_pin_window_v2/constraints/config.mk), [PDN](../gcd_pin_window_v2/constraints/pdn.tcl), and [SDC](../gcd_pin_window_v2/constraints/constraint.sdc). No global M1 spacing/EOL tightening is needed:

1. [Technology LEF](asap7_tech_asym_v1_m4_only_candidate.lef): VIA12 M1 enclosure is the permitted 5+2 nm in both orientations; M4 EOL side extension changes from 24.25 to 25 nm for correct representation at 1 nm DBU.
2. [Side-edge access LEF](asap7sc7p5t_R_edge_pin_window_candidate.lef): only signal pins within 54 nm of cell sides are restricted to local y=0.054..0.216 µm. Removed access metal remains OBS. This changes 248 pins in 188 cells; central pins are unchanged.
3. VIA_IN_PIN_MIN_LAYER=M1 and VIA_IN_PIN_MAX_LAYER=M1.
4. Sparse M1/M2/M3 PDN: 18 nm M1/M2 followpins; 18 nm M3 straps, pitch 5.4 µm, spacing 2.682 µm, offset 0.297 µm.

## Evidence and geometry consistency

- [DRC report](../calibre_evidence/gcd_pin_window_v2/drc/drc_report), [DRC receipt](../calibre_evidence/gcd_pin_window_v2/drc/invocation.json), [LVS report](../calibre_evidence/gcd_pin_window_v2_property1024/lvs/mask.lvs.rep), [LVS receipt](../calibre_evidence/gcd_pin_window_v2_property1024/lvs/invocation.json).
- [Input geometry verification](../gcd_pin_window_v2_input/geometry_verification.json): normalizing top names and excluding text yields identical 13744 geometry/instance records; SHA256 a36a123448f21b1ed71046bb956df77221620eb8575d9fc2063d312648de015e. DRC uses TOPCELL; LVS uses gcd and required pin text.
- [Pin+OBS union check](edge_pin_window_geometry_verification.json): all 212 M1 cell unions match. [Generator](prepare_edge_pin_window.py), [pin provenance](edge_pin_window_provenance.json), [checker](verify_edge_pin_window.py). Signal pins are contained in native GDS M1; no pin overapproximation was found.
- [Archive manifest](pin_window_v2_archive_manifest.json) records configurations, inputs, reports, and geometry checks.

## Pin-access diagnosis

The preceding gcd_asym_via already had LVS CORRECT but 115 flat DRC results: V0.M1.AUX.3=48, M1.S.2=23, M1.S.6=44. See [report](asym_evidence/drc_report). Pins are contained in native M1; oversized LEF pins are not the cause.

OpenROAD FlexPA::viaMaxExt() skips Y enclosure for access points within three M1 widths (54 nm) of cell sides. Every residual V0 marker, **48/48**, matches this exception and an M1 landing protruding beyond native metal. See [coordinate proof](asym_evidence/side_exception_proof.json). Side-edge windows avoid these points while preserving central access, yielding full zero-violation DRC.

The [all-pin window candidate](asap7sc7p5t_R_pin_window_candidate.lef) lost FAx1/SN access and was not selected. Global M1 spacing/EOL tightening was also not selected. Historical proposals below are retained as diagnosis; use the configuration above for reproduction.

## Initial findings

1. FIN.S.1's 83 stored markers were hierarchy-dependent. Flattened/merged FIN has 520 stripes, all 7 nm wide, and 519 adjacent center pitches of 27 nm. Full flat DRC reports FIN.S.1=0 and 1732 results, exactly 1815 hierarchical expanded results minus 83. No check was excluded. Some hierarchical markers represent derived geometry, not native FIN edges.
2. Default PDN causes most M4/V4/M6 errors. M2→M5 connections place M4 landings on M2 followpin positions rather than 48 nm M4 tracks. A 120 nm M5 stripe with 24 nm V4 cuts violates equal-width self-aligned-via rules. Wide M6 edges miss the 32 nm grid.
3. All initial 138 V0.M1.AUX.3 markers touch routing-added M1 landing metal. Use via-in-pin before considering native transistor geometry changes.
4. Default VIA12 M1 is 18×22 nm around 18×18 nm V1, giving 2 nm per end; official V1.M1.EN.1 requires opposite enclosures of 5 and 2 nm. Default M1 corner spacing is 18 nm versus 20 nm required.

ORFS passes M1 via-in-pin to pin access and detailed routing. A narrow three-layer PDN avoids higher-layer landing conflicts, but each candidate still needs power connectivity, timing, and full signoff. Denser M3 can block placement. If retaining M2→M5, -ongrid {M3 M4} and -min_width_layers {M3 M4} constrain intermediate landings but do not resolve M5/V4 equality alone.

## Isolated LEF candidates and numerical details

The selected asymmetric VIA12/M4-only candidate preserves other original rules and parses as 30 layers/10 vias. Read-back M4 side/backward/forward extensions are 25/48/48 nm at DBU=1000. Earlier [symmetric V1-enclosure](asap7_tech_v1_enclosure_candidate.lef) and [additional M1-spacing](asap7_tech_v1_m1_spacing_candidate.lef) candidates parse but fail actual pin access; they are not passing configurations. The latter uses tip-to-side 25 nm, tip-to-tip 31 nm, and corner 20 nm, conservatively covering a 27 nm-only case. [Generator](prepare_tech_candidates.py).

OA211x2 pin C is 18×58 nm at y=0.106..0.164 µm. M2 centers are 0.117 or 0.153 µm: a symmetric 28 nm pad protrudes 3 nm, whereas a 25 nm asymmetric 5+2 pad fits. The two pad orientations span -0.011..0.014 and -0.014..0.011 µm around the via; V1 remains 18×18 nm.

The original M4 EOLKEEPOUT 0.025 EXTENSION 0.048 0.02425 0.048 CORNERONLY quantizes side extension to 24 nm at 1000 DBU/µm, exactly neighboring-track spacing. Exclusive-boundary intersection can miss a 1 nm corner overlap. Using 25 nm covers the boundary. Relevant implementation: FlexGC_eol.cpp getEolKeepOutQueryBox and checkMetalEOLkeepout_helper.

## Initial rule categories

Counts below are stored hierarchical markers, not flat expanded counts; coordinates are µm.

| Rule | Stored count | Requirement / diagnosis |
|---|---:|---|
| FIN.S.1 | 83 | FIN pitch 27 nm; same GDS flat count zero |
| V0.M1.AUX.3 | 138 | V0/M1 transverse widths equal; markers touch new landings |
| M1.S.2 | 77 | Tip-to-side ≥25 nm; example x=2.388..2.412 gives 24 nm |
| M1.S.4 | 12 | Narrow-tip gap ≥31 nm; example 22 nm |
| M1.S.5 | 5 | Mixed-width tip gap ≥31 nm |
| M1.S.6 | 80 | Corner-to-corner ≥20 nm |
| M3.S.2 | 13 | Tip-to-side ≥25 nm; PDN landing example 21 nm |
| V1.M1.EN.1 | 82 | Opposite enclosure ≥5/2 nm; default 2/2 |
| V2.M3.AUX.2 | 3 | V2/M3 transverse widths equal; wide PDN landings; flat expanded count 477 |
| M4.S.5 | 3 | Adjacent-track parallel overlap ≥44 nm; example x=9.634..9.635, only 1 nm |
| M4.AUX.1 | 480 | Horizontal edges on 24 nm grid; chiefly PDN landings |
| M4.AUX.2 | 21 | Minimum-width M4 on valid 48 nm tracks/offset |
| M6.AUX.1 | 24 | Horizontal edges on 32 nm grid |
| V4.M5.AUX.2 | 318 | V4/M5 transverse widths equal; actual 24/120 nm |

Do not use track-center offsets directly as Calibre edge offsets: normal M4 centers 12+48N nm with width 24 nm already give edges on the 24 nm grid.

Primary evidence: [marker geometry](marker_geometry.json), [V0 classification](v0_classification.json), [geometry script](inspect_markers.py), [V0 checker](classify_v0.py). Requirements are from the private local official DRM sections 3.3 and 3.11–3.16; OpenROAD options were checked against the pinned drt/pdn READMEs.

## Additional preserved references

- [pdn_m1_m2_m3_candidate.tcl](pdn_m1_m2_m3_candidate.tcl)
