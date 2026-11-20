# ASAP7 Initial Fourth-Platform Signoff Assessment

Historical test: 2026-11-16, GMT+8. **RTL→GDS works and official Calibre DRC/LVS execute, but this initial sample is not fully signed off. ASAP7 is not enabled as a default fourth platform.** Subsequent repairs are documented in the parent report.

| Item | Initial measurement |
|---|---|
| Rule package | User-supplied asap7.bz2, including r1p7 DRC/LVS/RCX |
| Design | ORFS 16-bit GCD; 5 functional cases PASS |
| Implementation | Synthesis, placement, CTS, routing, GDS, OpenRCX SPEF produced |
| Timing | 1 ns; setup WNS +0.387836 ns; TNS zero |
| Size | Cell area 47.9245 µm²; die 16.2 × 16.2 µm |
| Official DRC | All 334 checks; 14 violating categories; reported total 1339 (1815) |
| Official LVS | Transistor extraction/comparison completes, INCORRECT |
| Admission | DRC clean + LVS match not established; platform gate unchanged |

The two DRC totals are preserved as reported, not added together. No rule exclusions, result truncation, or encrypted-rule changes were used. Exit zero means execution completed, not clean signoff.

## Checker integration

ORFS runs on 208; Calibre 2025.1_16.10 and the existing license run on 216. Setting the four required metal-offset variables enables encrypted DRC compilation. The deck hardcodes test.gds and TOPCELL; rename the copied top without scaling. [Environment instructions](https://raw.githubusercontent.com/The-OpenROAD-Project/asap7_pdk_r1p7/main/README_ASAP7PDK_INSTALL_201210a.txt), [fixed-name issue](https://github.com/The-OpenROAD-Project/asap7/issues/18).

LVS uses final-ODB CDL and pinned [official RVT transistor CDL](https://github.com/The-OpenROAD-Project/asap7sc7p5t_28/blob/e5bd25da72cd297b8c0ec6f77600e0cbaa1124d0/CDL/LVS/asap7sc7p5t_28_R.cdl), not the layout-extracted netlist, with no logic blackboxes. Interface adaptations:

1. Copy 56 top labels from metal/2 LABEL to metal/251 PIN, preserving originals.
2. Use write_cdl -include_fillers to retain real decap transistors omitted by default export.
3. Omit 104 welltap and 300 transistor-free filler reference instances, matching extraction treatment; retain active logic, decaps, and bulk comparisons.

[Geometry check](gcd_calibre_input/geometry_verification.json) confirms 13871 unchanged non-text shapes/instances, including transforms/properties. [Checker](verify_geometry.py), [commands](recheck_calibre.sh).

Ports match 56/56; reduced nets 2269/2269 and instances 2632/2632. Of the nets, 2255 match and 14 per side do not. Device distributions, connections, and attributes differ. One reported comparison is layout W=162 nm/nfin=6 versus reference W=81 nm/nfin=3. A property-search-limit warning means this is not the full discrepancy set. Later diagnosis identifies incorrect device pairing under the original topology reference; do not interpret this example as proven wrong layout sizing.

Leading stored DRC categories are M4.AUX.1=480, V4.M5.AUX.2=318, V0.M1.AUX.3=138, plus FIN, M1/M3 spacing, and via enclosure. [Summary](qualification.json). This evidence neither makes all errors false positives nor establishes that ASAP7 cannot achieve complete signoff.

## Resources and follow-up at this stage

Historical integer-stage sum: 173 s; reported peak memory: 1096 MB; first GDS merge about 103 s. DRC wall time 13.36 s, final LVS 6.76 s; time -v peak RSS 113620/144364 KiB. These are small-design single-run measurements, not large-design predictions.

The follow-up at this stage was to align cell GDS/CDL connectivity, bulk pins, and sizes and repair routing constraints at actual violation locations. Default integration requires representative complete DRC/LVS passes. Criteria were not relaxed; Calibre xACT RCX was not qualified.

Evidence links below preserve the historical paths as originally recorded. Some resolve relative to the parent qualification root rather than this historical snapshot directory.

- [DRC report](calibre_evidence/gcd_full/drc/drc_report), [log](calibre_evidence/gcd_full/drc/run.log)
- [LVS report](calibre_evidence/gcd_full/lvs_device_complete/mask.lvs.rep), [log](calibre_evidence/gcd_full/lvs_device_complete/run.log)
- [Simulation](gcd_smoke/sim/simulation.log), [PPA](gcd_smoke/reports/ppa.json), [flow](gcd_smoke/backend_driver.log)
- [Input provenance](gcd_calibre_input/input_provenance.json), [CDL adapter](gcd_calibre_input/cdl_adapter_provenance.json), [hashes](evidence_sha256.json)

Native timing is ps; this report converts to ns. The old generic timing_check.json wns_ns field retains raw ps and is not unit evidence. Rules remain isolated and Git-ignored; shared PDK files are not overwritten.
