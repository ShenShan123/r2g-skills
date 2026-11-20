# UART corner20 diagnostic variant

Completed full flow (62.82 s from 19 exact tool elapsed records, peak 1145.20 MiB) and transistor LVS CORRECT. The unchanged full 334-rule DRC reports 25 violations: 2 V0.M1.AUX.3 and 23 M1.S.2. This is retained as failed qualification evidence.

The experiment enlarges die/core from 16.20/14.04 µm side lengths to 18.36/16.20 µm and represents the official 20 nm M1 corner-spacing constraint in the candidate technology LEF. RTL, standard cell GDS, timing target and official Calibre decks are unchanged. The original eight M1.S.6 results disappear; new M1.S.2 results occur around routed minimum-area patches next to NAND2/OAI22 pins. The two NAND3 pin-access V0 results remain. Coordinates and physical source shapes are in `signoff/marker_geometry.json`.

This variant motivates enforcing actual full via enclosure at pin access, independently of placement size; it is not counted as a DRC-clean design.
