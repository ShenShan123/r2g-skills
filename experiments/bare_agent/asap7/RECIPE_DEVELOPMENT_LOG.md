# ASAP7 recipe development log

**Independence rule (user, 2026-11-20).** Recipe development uses only my own exploration: baseline runs,
repair arms, cell/tech geometry, and DRC/LVS evidence from those runs. **Results of the DeepSeek or GPT
experimental arms (LLM-only, Recipes + LLM, native harness) are never used to choose, design, tune or
select recipes**, including their transcripts, the edits they made, their pass/fail patterns, and the
violations they left. Every entry below names its evidence. All entries so far use repair-arm and baseline
runs only.

A running record of every recipe change, run, and result, newest first. Repair arms live in
`216:/proj/workarea/yangao/asap7_baseline/repairs/<arm>/`: `extra.mk` and `stage` overlays, `runs/`, and
Calibre `signoff/`. Every verdict is the official r1p7 Calibre DRC (flat) plus LVS.

## 2026-11-20: GATE.S.3 residual analysis (recorded runs only, no flows)

Data: the 137 `combo_pad1_um` / `gate_pad2_um` runs that still fail GATE.S.3. There are 1248 markers, compared
column by column with the final DEF and the cell GDS. Scripts are `/tmp/yg_gate{,2,3,4}.py` on 208; to be
moved into this folder once they settle.

- **Each marker is a whole merged poly line.** It is 20 nm wide and sits at a site centre (multiples of 54
  nm). Its height is 3.5–148 µm, median 25 µm. Every ASAP7 cell draws poly at every site centre, running the
  full cell height and extending about 5 nm past the top and bottom. So poly merges vertically across rows,
  Calibre reports the whole merged line, and the marker does not locate the offending row.
- **Tie cells are involved.** A violating column passes through the edge site of a `TIELOx1` in 74% of cases,
  against 10% for all other columns (7.6× over-represented). It passes through a `TIELOx1` interior site in
  29% of cases, against 6% (4.7×). No other master is over-represented by more than 1.7×. Empty sites no
  longer appear after padding.
- **Necessary-looking, not sufficient.** Only about 20% of `TIELOx1` cells in these designs lie on a
  violating column. Things that do *not* separate the two groups:
  - horizontal neighbours: all abutting fill or decap in both groups, in the same mix;
  - orientation: N/FS split 50/50 in both;
  - vertical neighbours: about 20% are stacked `TIELOx1` in both groups.
- **Cell geometry.** `TIELOx1` poly runs from -22 to 1100 (GDS units of 0.25 nm). Most other cells run from
  -20 to 1100 or 1102. The x positions match, so this sub-nm offset does not obviously make a notch.
  The deck is encrypted, so the exact meaning of GATE.S.3 is not visible.

Candidate recipes, to be run as arms after the DeepSeek arm finishes. Each changes only placement and
tie-cell choices; never GDS or deck.

1. `tie_pad`: `set_placement_padding -masters {TIELOx1 TIEHIx1}` 2 sites each side, on top of combo.
2. `tie_sep`: larger tie separation in `repair_tie_fanout` (fewer tie cells stacked in a column).
3. `tie_hi_only`: map constant-0 to TIEHI plus an inverter, i.e. no `TIELOx1` at all. This tests the tie-cell
   hypothesis directly; the area/timing cost is to be measured.
4. If none of these works: build a marker-localising probe (re-run DRC on per-row clips of a violating
   column), so the violating row is pinned before more recipes are tried.

## 2026-11-20: state at the start of the replay design

Tallies are over each arm's own designs. The arms were selected by baseline symptom, so they are not
comparable rates.

| Arm | Content | Designs | DRC clean + LVS correct | Other |
|---|---|---|---|---|
| `um_skip_all` | M5–M7 SPACING raised (router LEF only) | 145 | 131 | 14 `unknown` DRC |
| `combo_pad1_um` | pad 1 + um_skip | 286 | 89 | 193 violations, 1 timeout, 3 flow fail |
| `v1_obsbloat10` | pad 1 + um_skip + SC LEF V1/M2 OBS completed, bloated 10 nm | 107 | **97** | 9 `unknown` DRC |
| `v1_obsfull` | pad 1 + um_skip + SC LEF OBS completed (no bloat) | 107 | 5 | 102 violations (superseded by bloat10) |
| `v1_samenet` | pad 1 + um_skip, same-net V1 variant | 24 | 0 | refuted |
| `gate_pad2_um` | pad 2 + um_skip | 68 | 12 | 51 violations, 1 timeout, 4 flow fail |
| `gate_pad1nos_um` | pad 1 + `-disallow_one_site_gaps` + um_skip | 68 | 1 | refuted (DPL in CTS/GRT recreates gaps) |
| `lvs_feedthru_stack` | v1_obsbloat10 + port-feedthrough buffers | 3 | 0 | 2 LVS INCORRECT; refuted |
| `fe_transfer_full` / `_combo` | nangate45 frontend repairs transplanted | 24 / 11 | 9 / 2 | rest violations / timeouts |
| `grt_skipincr` | `SKIP_INCREMENTAL_REPAIR=1` (RSZ-0074) | 2 | 2 | |
| `ppl_fix` | spec dies corrected from PPL-0024 refusals | 6 | 0 | spec correction, not repair credit |

Earlier refutations (see memory `asap7-repair-recipes`): `v1_s27` (V1 spacing 27 nm creates V1.M2.EN.2),
`gate_noxp5` (dropping FILLERxp5 breaks fill, DPL-0002), `gate_noonesite`.

### Open problems (from the 300-case sample's recorded runs)

- **GATE.S.3: open.** It remains in 39 sampled cases after the best recorded recipe (median 4 violations).
  Root cause recorded earlier: a FILLERxp5 alone in a one-site gap. Padding 1 or 2 removes most gaps, but
  CTS/GRT legalisation recreates some. Next candidates:
  - a post-route filler pass that refuses a lone xp5;
  - a detail-placement pass after GRT;
  - padding only around the cells whose gap neighbours produce GATE.S.3.
  Each is a new arm, run only after the DeepSeek arm finishes (user priority, 2026-11-20).
- **V1.S.1 has a fix, but it hasn't been applied everywhere.** 22 sampled cases still carry V1.S.1 because
  they only got step 1. `v1_obsbloat10` has not run on them yet.
- **Incomplete DRC (`unknown`).** 14 sampled cases. Cause to be checked before re-signing.

### Compute note

Recipe flows wait until the DeepSeek LLM-only arm finishes (user, 2026-11-20). Analysis and the replay
builder use no EDA compute and can proceed now.
