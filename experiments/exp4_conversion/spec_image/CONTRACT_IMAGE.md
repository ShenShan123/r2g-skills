# Image-conversion contract (public spec, version 1.0, frozen 2026-11-07)

You are asked to write a converter that turns one placed-and-routed design into GCell-raster
**images**: five input feature maps computed from the placement layout and two congestion
label maps computed from the routed layout, exactly as specified below. This document says
WHAT every array holds. How you compute it is up to you.

## 1. Interface

```
bash <converter_dir>/run.sh <design_dir> <platform_dir> <out_dir>
```

- `<design_dir>` (read-only) contains exactly two files of one design:
  - `3_place.def` — the layout after detailed placement, before clock-tree synthesis and
    routing (DEF 5.8, `UNITS DISTANCE MICRONS 2000`). It has no routing.
  - `5_route.def` — the final routed layout of the same design (routing, filler cells).
- `<platform_dir>` (read-only): `NangateOpenCellLibrary.tech.lef` (routing layers, pitches),
  `NangateOpenCellLibrary.macro.mod.lef` (cell sizes, classes, pin shapes),
  `NangateOpenCellLibrary_typical.lib` (Liberty; not needed by this contract).
- `<out_dir>` (writable, exists, empty) must receive two NumPy archives:
  - `features.npz` with exactly the arrays `cell_density`, `macro_region`, `rudy`,
    `pin_rudy`, `pin_density`;
  - `labels.npz` with exactly the arrays `congestion`, `congestion_raw`.
  Every array is `float32`, 2-D, shape `(nx, ny)` (§2), loadable with
  `numpy.load(path, allow_pickle=False)` (write them with `numpy.savez` or `savez_compressed`).
- Exit code 0 on success. No network, at most 3,600 s wall time per design. Available:
  Python 3.13 with numpy, pandas, torch (CPU), torch_geometric; OpenROAD (`openroad`), `sta`,
  `yosys`, `tclsh`. Designs range from ~200 to ~35,000 placed cells (DEF up to ~70 MB).

**Feature arrays may use only `3_place.def` (plus the platform files); label arrays use
`5_route.def`.** The grid (§2) is defined from `5_route.def` and is the same die.

## 2. Grid and array orientation

- GCell step `sx`, `sy` (DBU) = the `STEP` of `5_route.def`'s `GCELLGRID X` / `GCELLGRID Y`
  statements. Die = bounding box `(X0, Y0, X1, Y1)` of `DIEAREA` (DBU; µm = DBU / 2000).
- `nx = ceil((X1 − X0)/sx)`, `ny = ceil((Y1 − Y0)/sy)` (at least 1).
- GCell `(i, j)` is the rectangle `[X0 + i·sx, X0 + (i+1)·sx] × [Y0 + j·sy, Y0 + (j+1)·sy]`
  **clipped to the die** (the last row/column may be narrower). Its area `A(i, j)` is the
  clipped area in µm².
- Arrays are indexed **`[i][j]` = `[x][y]`**: shape `(nx, ny)`, first index along x, second
  along y, `[0][0]` at the die's lower-left corner.
- "The GCell of a point (x, y)": `i = floor((x − X0)/sx)`, `j = floor((y − Y0)/sy)`, each
  clamped to at most `nx−1` / `ny−1`; a point outside the die (strictly) has no GCell.
- "Splat a rectangle R with density v": for every GCell, add `v · area(R ∩ GCell)`
  (µm², R first clipped to the die).

## 3. Feature maps (from `3_place.def` only)

Components counted: every `COMPONENTS` entry with status `PLACED`, `FIXED` or `COVER` whose
master has a `SIZE` in the cell LEF. Footprint = placement point plus `SIZE (w, h)`, with w and
h swapped for orientations `E`, `W`, `FE`, `FW`.

| array | definition | unit |
|---|---|---|
| `cell_density` | Σ over counted components whose LEF `CLASS` is not `BLOCK` (standard cells, incl. tap cells) of their footprint area overlapping the GCell, ÷ `A(i, j)` | fraction |
| `macro_region` | same for components whose LEF `CLASS` is `BLOCK` (none in this corpus → all 0) | fraction |
| `rudy` | RUDY. For every net (below) with at least 2 connection points: bounding box `[xa, xb] × [ya, yb]` of its points; `w = max(xb − xa, sx/2000)`, `h = max(yb − ya, sy/2000)` (sides floored at one GCell step, µm); a box of size `w × h` **centred** on the bbox centre is splatted with density `d = (w + h)/(w·h)`; the accumulated value is finally ÷ `A(i, j)` | 1/µm |
| `pin_rudy` | for every such net and every one of its connection points, add the net's `d` to the GCell of that point | 1/µm |
| `pin_density` | for every such net, the number of its connection points that fall in the GCell | pins |

Nets: every `NETS` entry of `3_place.def` whose `+ USE` is not `POWER` or `GROUND` (clock and
reset nets included; `SPECIALNETS` are never used). Its connection points: one per entry of
its connection list —
- `( PIN p )`: the `PINS` placement point of port `p` (`PLACED`/`FIXED`/`COVER ( x y )`);
  skipped if the port has no placement;
- `( inst pin )`: the instance placement point plus the pin's in-cell position under the
  instance orientation, where the in-cell position is the mean of the centres of all `RECT`s
  listed for that pin in the cell LEF (all PORT layers); if the master or pin has no LEF
  geometry, the instance placement point itself; skipped if the instance is unplaced or
  unknown.

Orientation maps a cell-local point (px, py) of a cell of LEF size (w, h) to an offset from the
placement point: N (px, py); S (w−px, h−py); W (h−py, px); E (py, w−px); FN (w−px, py);
FS (px, h−py); FW (py, px); FE (h−py, w−px).

## 4. Label maps (from `5_route.def`)

Both on the grid of §2:
- Demand: every wire segment of every regular net (all net types, including clock nets),
  i.e. every pair of consecutive points inside one routing statement of the `NETS` section
  (`*` repeats the previous coordinate; `RECT` patches and vias contribute nothing). A
  horizontal segment adds its length, split at GCell boundaries, to the horizontal demand `Dh`
  of the GCells it passes in the row `floor(y/sy)`; a vertical segment likewise to `Dv` in the
  column `floor(x/sx)`. Lengths in µm. Demand falling outside the `nx × ny` grid is dropped.
- Capacity (identical for every GCell): `Ch = Σ over HORIZONTAL routing layers of
  (sx/2000)·(sy/2000)/pitch`, `Cv` likewise over VERTICAL layers (pitch from the tech LEF,
  µm; all routing layers metal1..metal10).

| array | definition |
|---|---|
| `congestion_raw` | `U = max(Dh/Ch, Dv/Cv)` per GCell (0 where there is no demand) |
| `congestion` | `U` smoothed by a 2-D Gaussian with σ = 1 GCell, radius 4, reflect boundary `(d c b a | a b c d | d c b a)` — exactly `scipy.ndimage.gaussian_filter(U, sigma=1.0)` with its defaults |

(No IR-drop or power channel is part of this contract.)

## 5. How the output is judged

A run **succeeds** on a design when `run.sh` exits 0 within 3,600 s and both archives load
and pass the public format check (`check_format_image.py`: exact array names, float32, 2-D,
common shape `(nx, ny)` of §2, finite, non-negative, `pin_density` integer-valued).
Correctness is then measured per array against independently computed ground truth,
element by element; an array passes on a design only if every GCell matches within
`|a−b| ≤ atol + rtol·|b|`:

| array | atol | rtol |
|---|---|---|
| `cell_density`, `macro_region` | 1e-5 | 1e-5 |
| `rudy`, `pin_rudy` | 1e-5 | 1e-5 |
| `pin_density` | 0 | 0 |
| `congestion`, `congestion_raw` | 1e-6 | 1e-5 |

You will not see the ground truth or the correctness scores during development; the format
checker is the only feedback the harness provides.
