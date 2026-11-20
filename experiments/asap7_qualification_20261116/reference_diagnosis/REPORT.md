# ASAP7 Reference Materials and Input Consistency

2026-11-16, GMT+8. This investigation reads documentation and isolated copies; shared PDK files, official rules, and production flow are unchanged.

## Standard-cell GDS/CDL versions

CDL is pinned to official commit e5bd25da72cd297b8c0ec6f77600e0cbaa1124d0. The [official RVT GDS](https://raw.githubusercontent.com/The-OpenROAD-Project/asap7sc7p5t_28/e5bd25da72cd297b8c0ec6f77600e0cbaa1124d0/GDS/asap7sc7p5t_28_R_220121a.gds) and ORFS library have **zero geometry or text differences in common cells**, verified by recursive per-layer/datatype XOR and text/coordinate comparison. The official file adds one unused TBUFx1_ASAP7_75t_R.

- ORFS SHA256: f43d5b33df635cf9e66cbca66b6bdd05364f824d8040cd21528b9e12808b9263.
- Official SHA256: afd997a75d81c0d7373bab199018ef3c6473b63f131975cb14ca1dd9cbe628da.
- Both database units: 0.00025 µm.
- [Comparison script](compare_library_geometry.py), [results](library_geometry_comparison.json).

Replacing ORFS GDS with the downloaded library therefore cannot fix the current GCD LVS mismatch. Common-cell geometry equality does not prove that every official CDL transistor topology matches its GDS.

## Metal grids and power network

The [official DRM](https://raw.githubusercontent.com/The-OpenROAD-Project/asap7_pdk_r1p7/main/docs/asap7_drm_201207a.pdf) requires horizontal M4 edges on a 24 nm grid and M6 edges on a 32 nm grid. M4/M5 and M6/M7 origins may shift by multiples of 12 and 16 nm. Environment variables use 0.1 nm units: 24 nm is 240. Offsets cannot hide conflicting phases within one layer.

ORFS make_tracks.tcl places M4/M5 centers at 12 nm with 24 nm width, giving edge origin zero; M6/M7 centers at 16 nm with 32 nm width also give edge origin zero. **Do not multiply center offsets by ten and use them as deck offsets.**

An initial M4 violation at y=15.108..15.132 µm has half-grid edge phase. Wide PDN stripes and cross-layer landings require independent checks. Existing M5/M6 power widths are 120/288 nm; V4.M5.AUX.2 requires the V4 transverse width to equal M5 width, which a generic multi-cut array need not satisfy.

Isolated experiments should use grid-aligned PDN widths, pitches, offsets, and suitable vias, or a reduced-layer narrow-rail network. Every change requires connectivity, timing, and full DRC/LVS revalidation. If retaining M2→M5 topology, add_pdn_connect -ongrid {M3 M4} -min_width_layers {M3 M4} can constrain intermediate landings but does not solve M5/V4 equality by itself.

## FIN hierarchy

DRM page 18 requires equal FIN polygon X lengths for hierarchical DRC. FIN.S.1 markers spanning rows cannot be dismissed based on correct cell-internal 27 nm pitch alone. Compare the same GDS under full flat versus hierarchical DRC. If only hierarchy reports FIN.S.1, investigate representation or engine settings; if both report it, inspect actual pitch, flips, and abutment. Do not exclude FIN.S.1. The [upstream FIN issue](https://github.com/The-OpenROAD-Project/asap7/issues/12) does not provide a maintainer-validated general fix.

## Known LEF issues

[Issue 22](https://github.com/The-OpenROAD-Project/asap7/issues/22) concerns missing V3/V5 EOL widths in 4x LEF. This test uses 1x LEF with the relevant 0.02425 µm value already present; neither the 4x fix nor global scaling applies. The [historical 4x M6 spacing issue](https://github.com/The-OpenROAD-Project/asap7/issues/3) likewise does not justify changing official rules.

## Calibre version

[Official usage instructions](https://raw.githubusercontent.com/The-OpenROAD-Project/asap7_pdk_r1p7/main/Calibre_Usage_Instructions.txt) validate 2017.4 and describe an xACT incompatibility in 2018.2. Installed 2025.1 has completed all 334 DRC checks and transistor LVS on small designs. This does not support attributing all remaining errors to version incompatibility. xACT qualification remains separate.
