# ASAP7 Fourth-Platform Signoff Qualification

Updated: 2026-11-16, GMT+8.

**ASAP7 completed RTL→GDS and full official-deck DRC/LVS on a 16-bit GCD: all 334 DRC checks, zero violations, and transistor-level LVS CORRECT.** The GCD flow uses the existing OpenROAD binary. Adaptations are limited to technology configuration, pin-access abstractions, and an independent transistor reference. This provides a fourth-platform integration basis; the default platform gate and UI remain disabled for ASAP7.

**UART also completed full DRC/LVS with zero violations. AES-128 and PicoRV32 completed implementation, final gate-level functional tests, and transistor LVS; their complete DRC runs exceeded the 600 s budget.** UART uses an isolated pin-access repair build of OpenROAD, without replacing the shared installation. See the [multi-design report](MULTI_DESIGN_REPORT.md).

A subsequent AES -hyper probe completed 328/334 checks and reported 157 partial results, without a complete DRC verdict. See the [performance audit](drc_diagnosis/performance_audit/REPORT.md) and [latest experiment index](STATUS_AND_DOCS.md).

| Item | Final GCD measurement |
|---|---|
| Configuration | ASAP7 r1p7, 7.5-track RVT, FF corner; 16-bit GCD, 1 ns clock, four threads |
| Physical flow | Synthesis, placement, CTS, routing, final GDS, and OpenRCX SPEF produced |
| Official Calibre DRC | **All 334 checks, zero violations**, flat mode, result limit ALL |
| Official Calibre LVS | **CORRECT**; initial counts match: 56 ports, 2939 nets, 12463 MOS |
| RTL↔final gate netlist | **All 18 output bits proved**, including reset/first-request base case and an incorrect-output negative control |
| Timing | Setup WNS **+0.438514 ns**, hold WNS +0.0412595 ns; both TNS values zero |
| Area | Standard-cell area 47.9245 µm²; die 16.2 × 16.2 µm |
| Size | 395 logic cells including 35 flip-flops; 104 additional taps; 1402 total instances |
| Resources | Sum of ORFS tool-stage elapsed times **65.48 s**; peak RSS 1079.56 MiB; DRC **4.48 s**; LVS recheck **7.04 s** |

Resource figures are single-run measurements for this small design. ORFS time sums 19 tool timing records and excludes wrapper overhead; peak RSS is the maximum, not a sum. The first LVS run took 5.99 s; a property-search-budget recheck took 7.04 s. Both returned CORRECT. See [qualification.json](qualification.json).

## Key adaptations

1. **Power and vias meet official geometry rules.** Use M1/M2 followpins and grid-aligned M3 straps. VIA12 uses asymmetric 5 nm/2 nm M1 enclosure; M4 EOL keepout is represented as 25 nm at the database precision. See [configuration](gcd_pin_window_v2/constraints/config.mk), [PDN](gcd_pin_window_v2/constraints/pdn.tcl), and [technology LEF](drc_diagnosis/asap7_tech_asym_v1_m4_only_candidate.lef).
2. **Constrain access at side-edge pins.** This OpenROAD version skips Y enclosure checks for access points near cell side boundaries. Narrow those M1 access windows and retain all remaining real metal as OBS. Native cell GDS is unchanged; M1 pin+OBS unions match for all 212 cells. See [access LEF](drc_diagnosis/asap7sc7p5t_R_edge_pin_window_candidate.lef), [geometry verification](drc_diagnosis/edge_pin_window_geometry_verification.json), and [root-cause analysis](reference_diagnosis/PIN_ACCESS.md).
3. **Match the transistor reference to the published cell layout.** Remove parasitic RC from a fixed official [xACT PEX netlist](https://github.com/The-OpenROAD-Project/asap7sc7p5t_28/blob/e5bd25da72cd297b8c0ec6f77600e0cbaa1124d0/CDL/xAct3D_extracted/asap7sc7p5t_28_R.sp), retaining every MOS, bulk pin, model, and L/W/nfin parameter. The reference comes from an independent published library, not this design's extracted netlist. See [reference derivation and validation](lvs_diagnosis/REPORT.md) and the [complete reference library](lvs_diagnosis/asap7_R_official_pex_full.cdl).

The final LVS recheck raises only the property-matching search limit to 1024, without changing device tolerances. The final report has no search-limit warning; symmetric-network ambiguity notices remain. Empty tap/filler instances are omitted from the source CDL; all active logic and decoupling devices remain. Independent audits show identical MOS connectivity and attributes before and after omission. Intentionally incorrect bulk and width references both return INCORRECT. See [audit and negative controls](lvs_diagnosis/physical_audit/REPORT.md).

DRC uses the complete encrypted deck in flat mode, with no excluded checks or truncated results. The input copy is renamed TOPCELL to satisfy the deck's fixed name; LVS copies 56 top-level labels to the required PIN text layer. All **13744 non-text geometry/instance records** match the original GDS; there is no scaling or geometry modification. See [input verification](gcd_pin_window_v2_input/geometry_verification.json).

## Qualification scope and reproduction

GCD and UART have real complete DRC/LVS passes in one RVT/FF configuration. Large-design DRC, other corners, and larger power networks require further qualification. OpenRCX SPEF is available; Calibre xACT RCX is not qualified. Default platform gates remain unchanged.

- [Reproduction guide](REPRODUCE.md), [final GDS](gcd_pin_window_v2/backend/RUN_2026-11-16_06-00-47_2002719_0f7f/results/6_final.gds)
- [DRC report](calibre_evidence/gcd_pin_window_v2/drc/drc_report), [DRC log](calibre_evidence/gcd_pin_window_v2/drc/run.log)
- [LVS report](calibre_evidence/gcd_pin_window_v2_property1024/lvs/mask.lvs.rep), [LVS log](calibre_evidence/gcd_pin_window_v2_property1024/lvs/run.log)
- [Final formal verification](lvs_diagnosis/formal/gcd_pin_window_v2/RUN_2026-11-16_06-00-47_2002719_0f7f/verification.json), [PPA](gcd_pin_window_v2/reports/ppa.json), [flow log](gcd_pin_window_v2/backend_driver.log)
- [Summary](qualification.json), [evidence SHA256](evidence_sha256.json), [historical initial assessment](initial_assessment/REPORT.md)

The initial 162 nm versus 81 nm width example resulted from mismatched device pairing under the wrong topology reference; it is not evidence of incorrect GDS transistor dimensions. Independent official PEX references and complete LVS resolved it. Native ASAP7 timing units are ps; this report converts them to ns. Old generic timing_check.json field names must not be treated as unit evidence.

The rule package, shared PDK, and original OpenROAD installation are unchanged. Encrypted rules remain in an isolated Git-ignored directory.
