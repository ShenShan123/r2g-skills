# ASAP7 Flow, Experiments, and Documentation Index

Updated: 2026-11-17, GMT+8. ASAP7 is undergoing isolated fourth-platform qualification. The default platform gate and UI do not yet expose ASAP7.

## End-to-end flow

1. **Inputs and technology:** RTL, functional tests, SDC; ASAP7 r1p7, 7.5-track RVT cells, FF Liberty, a 1 ns clock, and four implementation threads.
2. **Physical implementation on 208:** Yosys synthesis → OpenROAD floorplan/PDN → placement → CTS → global/detailed routing → final GDS, gate-level netlist, OpenRCX SPEF, STA/PPA. ORFS drives the flow. Technology LEF, pin-access LEF, and PDN overrides remain isolated in each project.
3. **Functional verification:** RTL and final gate-level simulation. GCD/UART also have completed formal equivalence and negative controls. AES formal proof is incomplete; PicoRV32 program simulation is not a full ISA proof.
4. **Signoff preparation:** Rename the DRC copy's top cell to TOPCELL and add LVS port text on the required layers. Verify unchanged geometry. Derive the transistor reference CDL from independently published official PEX models, removing parasitics while retaining active devices and their parameters.
5. **Official signoff on 216:** Calibre 2025.1 with the unchanged encrypted official DRC/LVS decks. DRC uses all 334 checks and unlimited results; LVS compares transistor connectivity and attributes. Download reports and logs to 208.
6. **Acceptance and archive:** Complete DRC report, LVS CORRECT, functional/PPA evidence, and SHA256 records for inputs, rules, and reports. Partial rule completion is not complete signoff.

| Design | RTL→GDS / final gate-level tests | Complete official DRC | Transistor LVS |
|---|---|---|---|
| GCD | Complete; formal equivalence PASS | 334 checks, 0 violations | CORRECT |
| Repaired UART | Complete; formal equivalence PASS | 334 checks, 0 violations | CORRECT |
| AES-128 | Complete; 35 vectors PASS | Full run incomplete | CORRECT |
| PicoRV32 | Complete; 40-instruction program, 3 wait-state modes PASS | 600 s timeout; no complete verdict | CORRECT |

## Methods investigated

| Area | Method and outcome | Evidence |
|---|---|---|
| Input consistency | Compared official/ORFS cell GDS, units, layer mapping, metal grids, and offsets; common library cells have identical geometry | [Reference checks](reference_diagnosis/REPORT.md) |
| Power and vias | Isolated M1/M2/M3 PDN; asymmetric 5+2 nm VIA12 enclosure; M4 EOL keepout quantized to 25 nm | [DRC repairs](drc_diagnosis/REPORT.md), [minimal passing configuration](drc_diagnosis/MINIMAL_CONFIG.md) |
| Pin access | Enabled via-in-pin; an all-pin window candidate lost access points, so only side-edge windows were restricted; pin+OBS unions verified unchanged | [Pin-access analysis](reference_diagnosis/PIN_ACCESS.md) |
| Router repair | Isolated same-version OpenROAD build checks Y enclosure at side-edge access points; UART improved from 10 violations to complete zero-violation DRC | [Multi-design report](MULTI_DESIGN_REPORT.md), [patch](reference_diagnosis/strict_via_in_pin.patch) |
| LVS reference | Original CDL topology did not always match GDS; used independent official PEX-derived models, with combinational, sequential-topology, bulk-error, and width-error controls | [LVS repair](lvs_diagnosis/REPORT.md), [physical-cell audit](lvs_diagnosis/physical_audit/REPORT.md) |
| LVS search budget | Rechecked GCD at 1024 and AES/Pico at 65536 to remove premature property-search warnings; tolerances unchanged | [Main report](REPORT.md), [multi-design report](MULTI_DESIGN_REPORT.md) |
| DRC representation | Flat/hier, full flattening, FIN merging, FIN/GATE merging; per-layer XOR verified. Small controls completed; large designs still timed out | [Performance audit](drc_diagnosis/performance_audit/REPORT.md) |
| DRC parallelism | Added -hyper: merged-input AES completed 328/334 checks in 60 s and reported 157 partial results; six checks unfinished | [First-round audit](drc_diagnosis/performance_audit/REPORT.md) |
| Original hierarchy + hyper | AES completed 330/334 in 120 s; four ACTIVE checks remained, with additional FIN representation differences | [Second-round experiments](drc_diagnosis/acceleration_round2/REPORT.md) |
| Individual rules | Original AES ACTIVE.W.1/W.2 completed in 6.338/5.981 s with zero results; full 334-check partitioned acceptance remains incomplete | [Second-round experiments](drc_diagnosis/acceleration_round2/REPORT.md) |
| ACTIVE shape reuse | Analysis found 54 translation-normalized shapes among 59,081 polygons; new input construction and Calibre tests remain pending | [Shape statistics](reference_diagnosis/active_geometry/multi_aes_patterns.json) |

Spatial clipping, skipped rules, grayboxing, and Recon have not replaced complete signoff. Shared PDK files, official encrypted rules, and the shared OpenROAD installation were not replaced.

## Reading order and locations

1. [Main report](REPORT.md): qualification results, passing GCD configuration, and raw signoff evidence.
2. [Multi-design report](MULTI_DESIGN_REPORT.md): four-design status, functionality, timing, and resource accounting.
3. [Reproduction guide](REPRODUCE.md): implementation, input preparation, signoff on 216, and summary commands. Copy instructions primarily target GCD; other designs require their own tests.
4. [First-round DRC audit](drc_diagnosis/performance_audit/REPORT.md) and [second-round experiments](drc_diagnosis/acceleration_round2/REPORT.md): bottleneck investigation and latest individual-rule results.

208 root: `/home/yangao/r2g-skills/experiments/asap7_qualification_20261116/`.

216 execution root: `/proj/workarea/yangao/asap7_qualification_20261116/`; original checker runs are in `calibre_runs/`. Downloaded copies on 208 are in `calibre_evidence/`. Project `backend/` directories contain implementation artifacts, `reports/` contain PPA/verification evidence, and `*_input/` contain signoff inputs. Rules and PDK materials are private and excluded from normal release packages.

The main summaries and evidence indexes cover the preceding formal qualification runs. Second-round evidence is archived separately; individual-rule checks are not presented as complete signoff.
