# ASAP7 Transistor-Level LVS Reference Repair

**The original GCD layout returns CORRECT under the complete official LVS deck.** The repair aligns reference-cell transistor topology with published GDS; it changes neither tested layout, logic connectivity, nor comparison tolerances. [Full report](gcd_official_pex_all/mask.lvs.rep), [log](gcd_official_pex_all/run.log), [runset](gcd_official_pex_all/run.svrf).

| Reference | Unmatched nets per side | Unmatched reduced devices per side | Outcome |
|---|---:|---:|---|
| Original official CDL/LVS models | 14 | 30 | Connectivity and attribute mismatches |
| OAI21x1 replaced using official PEX | 0 | 0 | Connectivity differences remain; attribute errors disappear |
| Official PEX transistor models for all 44 used cell types | 0 | 0 | **CORRECT** |

Initial counts match: 56 ports, 2969 nets, 12427 MOS (6203 NMOS, 6224 PMOS). Reduced counts match: 2269 nets and 2632 devices. All active logic and decoupling cells remain.

## Cause and reference source

Ten OAI21x1_ASAP7_75t_R instances expose the mismatch: original CDL uses Y—(A1∥A2)—B—VSS, whereas GDS and official PEX use Y—B—(A1∥A2)—VSS. Both implement Y = !((A1 | A2) & B), but internal diffusion nodes and series ordering differ. PMOS also differ as one 6-fin branch versus two 3-fin branches. Other differences include symmetric-input transistor ordering, such as XOR2x2 PMOS series inputs.

The 162 nm versus 81 nm width example pairs layout OAI21x1 X3562 against reference HAxp5 X_633_. Fixing OAI21 topology removes attribute errors; this is not evidence of wrong GDS device sizing. See [diagnosis](diagnosis.json).

The independent source is the [official xACT 3D PEX netlist](https://github.com/The-OpenROAD-Project/asap7sc7p5t_28/blob/e5bd25da72cd297b8c0ec6f77600e0cbaa1124d0/CDL/xAct3D_extracted/asap7sc7p5t_28_R.sp), pinned to commit e5bd25da72cd297b8c0ec6f77600e0cbaa1124d0; OAI21 is dated 2022-01-22. Derivation does not read this GCD's extracted netlist.

## Derivation and verification

Collapse parasitic-resistor connected components and remove parasitic capacitors, retaining every MOS, bulk pin, model, L/W/nfin. Verify no external ports collapse together; retain original CDL names and port order. The GCD top CDL and its 1007 instances remain unchanged. [44-cell provenance and hashes](official_pex_all_cells_provenance.json).

For 38 combinational cell types and 360 instances, exhaustively compare **432 input combinations** across original CDL, de-parasitized PEX switch-level logic, and official ORFS Verilog. All agree. Five decap types have no Boolean function. This proves static logic, not electrical or timing equivalence. [Truth tables](combinational_truth_verification.json), [checker](verify_combinational_truth.py).

For all 35 DFFHQNx1_ASAP7_75t_R instances, original CDL and PEX-derived models are strictly transistor-network isomorphic: CLK/D/QN/VDD/VSS and gate/bulk roles fixed; source/drain exchange and internal-node renaming allowed; all 24 MOS models and L/W/nfin compared. Parallel-device merging is supported but unnecessary here. Gate-error and doubled-width negative controls are rejected. [Mappings](sequential_topology_verification.json), [checker](verify_sequential_topology.py).

Separate [RTL-to-final-netlist formal verification](../reference_diagnosis/formal/REPORT.md) uses original Liberty semantics. It complements transistor-topology checks and does not directly prove sequential PEX transistor behavior.

Reusable artifacts:

- [Complete RVT reference](asap7_R_official_pex_full.cdl): 209 official cell types, 3826 MOS, plus three original empty physical-cell definitions.
- [Library generator](derive_full_library.py), [provenance](full_library_provenance.json).
- [GCD reference](gcd_official_pex_all_cells.cdl), [generator](derive_official_pex_reference.py).

LVS CORRECT is independent of DRC. This reference repair does not change layout DRC. This historical run retains the default property-search-limit warning in its full report.

## Subsequent candidates

The [project-specific formal runner](run_gcd_formal.py) compares saved RTL with each completed final netlist and archives independent evidence. gcd_asym_via and gcd_pin_window_v2 both pass all 18 output points, the reset/first-request base case, and the inverted-output negative control. [Final formal report](formal/gcd_pin_window_v2/RUN_2026-11-16_06-00-47_2002719_0f7f/REPORT.md).

The [physical-cell/signoff audit](physical_audit/REPORT.md) proves that omitting empty tap/filler instances leaves all 12463 final-design MOS connections and attributes unchanged. Actual Calibre negative controls for wrong bulk and wrong width return INCORRECT. Rules do not ignore bulk pins or relax size tolerances.
