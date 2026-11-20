# Image raster production: public numerical specification

This task converts a placed-and-routed design into five pre-route input maps
and two post-route congestion target maps. The arrays are numerical training
data; colour-map PNGs are optional previews and are not scored. All four
model × harness arms receive this same specification and the same raw inputs.

## Interface and files

```
bash <converter_dir>/run.sh <design_dir> <platform_dir> <out_dir>
```

The design directory contains `3_place.def` (placement, without regular-net
routing), `6_final.def` (routed layout), and `config.mk` identifying the design
and platform. Read the DEF `UNITS DISTANCE MICRONS` value; do not assume 2000.
The two DEFs describe the same physical die. Feature values use only the
placement DEF and technology files; the final DEF provides the shared grid
and congestion labels. Technology files are read-only:

| Platform | Technology LEF | Cell LEF |
|---|---|---|
| nangate45 | `NangateOpenCellLibrary.tech.lef` | `NangateOpenCellLibrary.macro.mod.lef` |
| sky130hd | `sky130_fd_sc_hd.tlef` | `sky130_fd_sc_hd_merged.lef` |
| gf180 | `gf180mcu_5LM_1TM_9K_9t_tech.lef` | `gf180mcu_5LM_1TM_9K_9t_sc.lef` |

Any supplied Liberty file is unnecessary for this task. Other platform files
must not be assumed. The output directory must contain:

- `features.npz`: exactly `cell_density`, `macro_region`, `rudy`, `pin_rudy`,
  `pin_density`.
- `labels.npz`: exactly `congestion`, `congestion_raw`.

Each array is non-negative, finite `float32`, with shape `(nx, ny)`, loadable
using `numpy.load(..., allow_pickle=False)`. `pin_density` is integer-valued.
Additional standalone files such as metadata or previews are optional. Exit
0 on success. The per-design production deadline is 1800 seconds. The campaign
supplies the same tools and resource budget to all arms.

## Grid and coordinate convention

Let `u` be the final DEF DBU-per-micron value, `(X0,Y0,X1,Y1)` the bounding box
of `DIEAREA`, and `sx,sy` the `STEP` values of `GCELLGRID X/Y` (all in DBU).
Set `nx=ceil((X1-X0)/sx)` and `ny=ceil((Y1-Y0)/sy)`.
Arrays have **x first, y second**, with `[0,0]` at the die's lower left.
GCell `(i,j)` starts at `(X0+i*sx,Y0+j*sy)`. Its area `A(i,j)` in square
microns is its rectangle clipped to the die; the last row or column can be
narrower. The grid origin is the die origin, including non-zero origins.

Point binning uses `floor((x-X0)/sx), floor((y-Y0)/sy)` in DBU. An internal
boundary belongs to the bin above/right. Points exactly on the die's upper
boundary are clamped into the last bin; points strictly outside have no bin.
When pin geometry was computed in microns, convert back to DBU and round to
`1e-6 DBU` before binning to remove floating-point round-trip noise. For example,
`28500 DBU / 5700 DBU` belongs to bin 5; flooring `14.25 // 2.85` in binary
floating point can incorrectly yield bin 4.

“Splat rectangle R with density d” means add
`d * area(R intersect GCell)` to each GCell, with areas in square microns.
Rectangles are clipped to the physical die for feature splatting.

## Five placement feature maps

Count every `COMPONENTS` entry with status `PLACED`, `FIXED` or `COVER` and a
known LEF `SIZE(w,h)`. Its footprint is the placement origin plus `(w,h)`,
swapping width and height for orientations `W,E,FW,FE`.

| Channel | Value per GCell | Units |
|---|---|---|
| `cell_density` | summed overlap area of components whose LEF CLASS is not BLOCK, divided by A | fraction |
| `macro_region` | summed overlap area of components whose LEF CLASS is BLOCK, divided by A | fraction |
| `rudy` | net bounding-box density splatted as below, divided by A | 1/micron |
| `pin_rudy` | sum of the corresponding net densities at each connection point's GCell | 1/micron |
| `pin_density` | number of such connection points in the GCell | pins |

All three net-based channels use regular `NETS` entries whose `+ USE` is
neither POWER nor GROUND. Clock and reset nets are included. `SPECIALNETS` are
excluded. Count one point for each connection-list entry, including repeated
entries if present:

- `(PIN p)`: port p's DEF placement origin; omit unplaced or unknown ports.
- `(instance pin)`: instance placement origin plus its oriented cell-local
  pin position; omit unplaced or unknown instances. If the master or pin
  geometry is absent, use the instance placement origin.

A cell-local pin position is the unweighted mean of all of its LEF RECT
centres across every PORT and layer. Include rectilinear POLYGON shapes by
decomposing them into maximal horizontal slab rectangles: split at vertex
y-coordinates, pair x-crossings within each band, and merge vertically
adjacent rectangles with identical x-intervals. Average the resulting
rectangle centres together with explicit RECT centres. Ignore obstruction
geometry and MASK identifiers when reading coordinates. This definition also
applies to the polygon pins present in GF180.

For local `(px,py)` in a cell of size `(w,h)`, use these oriented offsets:

| Orientation | Offset |
|---|---|
| N | `(px,py)` |
| S | `(w-px,h-py)` |
| W | `(h-py,px)` |
| E | `(py,w-px)` |
| FN | `(w-px,py)` |
| FS | `(px,h-py)` |
| FW | `(py,px)` |
| FE | `(h-py,w-px)` |

Only nets with at least two available points contribute to any of `rudy`,
`pin_rudy`, or `pin_density`. Let the points' bounding box be
`[xa,xb] x [ya,yb]` in microns. Set `w=max(xb-xa,sx/u)`,
`h=max(yb-ya,sy/u)`, and `d=(w+h)/(w*h)`. Centre this expanded w-by-h
rectangle on the original bounding-box centre and splat it with density d
for `rudy`. Add d to `pin_rudy` and 1 to `pin_density` for each of the net's
points that falls in the die. Do not count single-point nets.

## Two routed label maps

Parse the routing statements of **all regular nets**, including clock nets.
Each consecutive pair of route points contributes its Manhattan segment
length. `*` repeats that coordinate from the preceding point. `NEW` starts
a new path: do not connect it to the preceding path. Via names and RECT
patches add no length. `SPECIALNETS` do not contribute.

For a horizontal segment at y, the row is `floor((y-Y0)/sy)`; split its x
length at GCell boundaries and add the length in microns to horizontal demand
`Dh`. For a vertical segment use column `floor((x-X0)/sx)` and accumulate
`Dv`. Drop portions outside the `nx*ny` grid. This demand grid covers the full
step-sized outer bins; a segment whose fixed-coordinate bin is outside the
grid contributes nothing, including a segment exactly at the outer grid edge.

Use every technology LEF layer of `TYPE ROUTING` with a positive pitch.
For `PITCH px py`, the relevant track spacing is py for HORIZONTAL layers,
px for VERTICAL layers; for a single PITCH value use that value. Capacity is
constant across bins, including partial edge bins:

```
Ch = sum((sx/u)*(sy/u)/pitch for each HORIZONTAL routing layer)
Cv = sum((sx/u)*(sy/u)/pitch for each VERTICAL routing layer)
U  = maximum(Dh/Ch, Dv/Cv)
```

`congestion_raw=U`. `congestion` applies the separable Gaussian kernel
`K[k]=exp(-k*k/2)/sum(exp(-j*j/2), j=-4..4)`, for k=-4..4, along both axes.
This has sigma 1 and radius 4, with half-sample symmetric reflection
at the boundary (`d c b a | a b c d | d c b a`), equivalent to
`scipy.ndimage.gaussian_filter(U, sigma=1.0)` using defaults. Compute in
adequate intermediate precision and store the final arrays as float32.
IR drop and power channels are outside this experiment's scope.

## Acceptance and accuracy

The public checker verifies format and grid shape; it provides no numerical
reference values. The hidden evaluator independently parses raw DEF/LEF and
computes each array without importing R2G conversion code. It accepts a pixel
when `abs(claim-reference) <= atol + rtol*abs(reference)`:

| Channel | atol | rtol |
|---|---:|---:|
| cell_density, macro_region, rudy, pin_rudy | 1e-5 | 1e-5 |
| pin_density | 0 | 0 |
| congestion, congestion_raw | 1e-6 | 1e-5 |

A design is **fully correct** only if production succeeds, format is correct,
and every pixel of all seven arrays passes. Missing or malformed arrays have
zero accuracy against their full reference-sized denominator; failed production
receives zero accuracy. Pixel accuracy is first computed per channel, then
averaged over the **six independent channels**: the five features plus raw
congestion, excluding channels whose reference is entirely zero from this
average; such channels still must match every pixel for full correctness.
Smoothed congestion is still required for full correctness, but
is not counted a second time in the primary accuracy metric. The campaign
averages design accuracies equally, so a larger die cannot dominate the score.

All arms use the same 18 development and 60 held-out designs, covering the
three technologies and three design sizes. Development has only raw inputs,
this specification and the format checker. Converters are frozen before
testing; numerical reference values and held-out test feedback are unavailable
during development. The three repeats are independent development sessions.
