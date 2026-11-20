# Empty Physical Cells and Signoff-Strength Audit

**PASS.** Omitting empty tap/filler hierarchy from reference CDL removes no MOS and changes no MOS pin binding or attribute.

| Design | Omitted empty instances | MOS before/after | Complete MOS/connectivity/attribute sequence |
|---|---:|---:|---|
| gcd_asym_via | 416 | 12421 / 12421 | Identical SHA256 |
| gcd_pin_window_v2 | 410 | 12463 / 12463 | Identical SHA256 |

Actual extracted netlists confirm TAPCELL_ASAP7_75t_R, FILLER_ASAP7_75t_R, and FILLERxp5_ASAP7_75t_R contain no devices; their reference definitions are also empty. Fully flattened references match every MOS name, D/G/S/B net, model, and parameter before/after omission. Top ports and other instances are unchanged. Native well/tap/metal geometry still participates in extraction and full DRC.

The final design retains **395 logic cells and 597 decaps**. Logic types/counts match the original 432-case combinational truth audit and 35-DFF topology audit. [Final audit](gcd_pin_window_v2.json), [intermediate audit](gcd_asym_via.json), [checker](../audit_physical_cells.py).

Actual Calibre negative controls retain layout and rules:

| Intentional reference error | Actual outcome |
|---|---|
| Change one INVx1 NMOS bulk from VSS to VDD | **INCORRECT**, connectivity/device differences |
| Change the same NMOS W from 81 to 162 nm | **INCORRECT**, explicit 81 nm width difference |

Each mutation affects all instances of that model in an independent copy; originals are unchanged. [Bulk report](negative_bulk/mask.lvs.rep), [width report](negative_width/mask.lvs.rep). Effective rules retain LVS IGNORE PORTS NO and LVS SOFT SUBSTRATE PINS NO, with zero absolute L/W/nfin tolerance and no ignored device pins.

Reference processing therefore preserves active-device, bulk, and dimension checks. Empty-cell instance counts are not transistor counts; their actual geometry and connectivity are covered by DRC and extraction.
