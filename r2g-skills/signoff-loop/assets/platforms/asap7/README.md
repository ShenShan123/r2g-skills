# ASAP7 apparatus and repair recipes

ASAP7 is graded only by the official Calibre r1p7 DRC/LVS decks. They are licensed and encrypted, so they are not
shipped here. Everything in this folder is derived from the open ASAP7 7.5-track RVT library and technology LEFs
(BSD-3-Clause, `ASAP7` from Arizona State University), plus scripts written for this repository.

## Frozen baseline apparatus (every ASAP7 flow uses these)

| File | sha256 (prefix) | What it is |
|---|---|---|
| `asap7_tech_asym.lef` | `c1cc22f0895272b4` | Technology LEF with VIA12 asymmetric M1 enclosure (5 nm / 2 nm) and the M4 end-of-line keep-out at database precision |
| `asap7sc7p5t_R_edge_pin_window.lef` | `f26bfe2b12d36dc3` | Cell LEF with M1 access windows narrowed at side-edge pins; the remaining metal is kept as OBS (the M1 pin+OBS union matches the cell GDS for all 212 cells) |
| `pdn_m1m2m3.tcl` | `6b65d4a7978f4e83` | PDN: M1/M2 followpins plus grid-aligned M3 straps |

The flow also uses an OpenROAD build with the pin-access fix (Y enclosure enforced at side boundaries when
via-in-pin is requested). The binary is not shipped. The patch is
`experiments/asap7_qualification_20261116/reference_diagnosis/strict_via_in_pin.patch` (local, not published).

## Repair recipes (`recipes/`)

These are router-input changes only. The GDS, rule deck and netlist are never touched. The fix loop offers each
recipe only when its rule family is present in the **current** Calibre DRC result (see
`scripts/reports/diagnose_signoff_fix.py`, `_asap7_drc_strategies`).

| Recipe id | Eligible when | Edit | sha256 | Evidence (recorded repair arms, 2026-11-17..19) |
|---|---|---|---|---|
| `asap7_upper_metal_skip` | only M5–M7 rules remain (no GATE) | `TECH_LEF` = `asap7_tech_um_skip.lef`: M5 SPACING 24→25 nm, M6/M7 32→33 nm (incl. spacing tables), so adjacent tracks are never both used. This fixes Mx.S.4/S.5 min-PRL/tip-to-tip rules the router LEF cannot express. | `b85075d3d0911598` | `um_skip_all`: 131 of 145 upper-metal-only designs clean; 14 DRC incomplete |
| `asap7_gate_pad1_um` | any GATE.* rule, padding < 1 | `CELL_PAD_IN_SITES_DETAIL_PLACEMENT=1` + the `um_skip` tech LEF | — | `combo_pad1_um`: 89 of 283 mixed designs clean (the rest mostly V1.S.1 or GATE.S.3 left); `gate_pad1`: GATE.S.3 = a lone FILLERxp5 in a one-site gap (50 of 54 markers) |
| `asap7_v1_obs_bloat` | any V1.* rule | `SC_LEF` = `asap7sc7p5t_R_pin_window_obsbloat10.lef`: the V1/M2 shapes 5 cells carry in GDS but not LEF are added as OBS (`tools/complete_obs.py`), and those V1 OBS are grown by 10 nm (`tools/bloat_v1_obs.py`) | `246c07f581b82297` | `v1_obsbloat10`: 97 of 107 clean, 9 DRC incomplete |
| `asap7_gate_pad2` | GATE.* remains after padding 1 | `CELL_PAD_IN_SITES_DETAIL_PLACEMENT=2` | — | `gate_pad2_um`: 12 of 64 clean |

Refuted and not offered:
- `v1_s27`: V1 spacing 27 nm creates V1.M2.EN.2.
- `gate_noxp5`: dropping FILLERxp5 breaks fill (DPL-0002).
- `gate_pad1nos`: `-disallow_one_site_gaps` has no effect, because CTS/GRT legalisation recreates the gaps.
- `v1_obsfull`: OBS completion without bloat cleared only 5 of 107.
- `lvs_feedthru_stack`: port-feedthrough buffers left LVS INCORRECT.

`tie_pad_candidate.tcl` is a **candidate**, not offered yet. It pads only `TIELOx1`/`TIEHIx1`. Residual GATE.S.3 poly
columns pass through a TIELO edge 7.6× more often than other columns. It is being validated as the `tie_pad2` arm,
together with `tie_sep4` (`TIE_SEPARATION=4`).

## Regenerating `obsbloat10`

```bash
klayout -b -rd GDS=asap7sc7p5t_28_R_220121a.gds -rd LEF_IN=asap7sc7p5t_R_edge_pin_window.lef \
        -rd LEF_OUT=obsfull.lef -rd REPORT=obsfull_report.json -r tools/complete_obs.py
python3 tools/bloat_v1_obs.py obsfull.lef asap7sc7p5t_R_pin_window_obsbloat10.lef 0.010 \
        FAx1_ASAP7_75t_R,NAND3x2_ASAP7_75t_R,NOR3x2_ASAP7_75t_R,XNOR2x1_ASAP7_75t_R,XOR2x1_ASAP7_75t_R
```

`complete_obs.py` patches exactly these 5 cells (its origin check passes for all of them), and they are the only
macros that differ from the base pin-window LEF.

The record of every run behind the evidence column is `experiments/bare_agent/asap7/RECIPE_DEVELOPMENT_LOG.md`
(local).
