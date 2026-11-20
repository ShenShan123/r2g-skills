# Graph-conversion contract (public spec, version 1.0, frozen 2026-11-07)

You are asked to write a converter that turns one routed physical design into two
PyTorch-Geometric graphs, **view b** and **view c**, exactly as specified below. This
document says WHAT every tensor holds. How you compute it is up to you.

## 1. Interface

```
bash <converter_dir>/run.sh <design_dir> <platform_dir> <out_dir>
```

- `<design_dir>` (read-only) contains exactly three files of one design:
  - `5_route.def` — the final routed layout (DEF 5.8, `UNITS DISTANCE MICRONS 2000`), including
    filler and tap cells, all signal and clock nets with their routing.
  - `6_final.spef` — parasitics extracted from that layout (OpenRCX; `*C_UNIT 1 PF`, `*R_UNIT 1 OHM`).
  - `constraint.sdc` — the timing constraints the design was implemented with (Tcl SDC).
- `<platform_dir>` (read-only) contains the Nangate45 platform files:
  - `NangateOpenCellLibrary.tech.lef` — technology LEF (routing layers, pitches).
  - `NangateOpenCellLibrary.macro.mod.lef` — standard-cell LEF (cell sizes, pin shapes).
  - `NangateOpenCellLibrary_typical.lib` — the standard-cell Liberty library (the only library).
- `<out_dir>` (writable, exists, empty) must receive `b_graph.pt` and `c_graph.pt`, each a
  `torch_geometric.data.Data` saved with `torch.save(data, path)` and loadable with
  `torch.load(path, weights_only=False)`.
- Exit code 0 on success. The run has no network, at most 3,600 s wall time per design, and
  the tools in the environment: Python 3.13 with numpy, pandas, torch (CPU) and
  torch_geometric; OpenROAD (`openroad`, which embeds OpenSTA), `sta`, `yosys`, `tclsh`.
  Use any of them or none. Designs range from ~200 to ~35,000 placed standard cells
  (DEF files up to ~70 MB).

## 2. Names

All names are used **exactly as written in the DEF**, including DEF escape backslashes
(e.g. instance `result\[31\]$_SDFF_PP0_`). The scorer compares names after deleting every
backslash, so either form is accepted, but names must otherwise be identical to the DEF.

- gate = a DEF `COMPONENTS` instance name;
- net = a DEF `NETS` net name (regular nets only; `SPECIALNETS` are never used anywhere);
- iopin = a DEF `PINS` pin name (a top-level port);
- pin = an instance pin, written `"<inst>/<pin>"` (e.g. `"_123_/A1"`), one per `( inst pin )`
  connection that appears in a DEF `NETS` connection list. `( PIN <name> )` connections are
  I/O pins, not pins.

## 3. Which entities are kept

### 3.1 Net type (`net_type_id`)

Each DEF net gets a type, decided by the first rule that matches:

| id | rule |
|---|---|
| 1 | the net's `+ USE` is `POWER` |
| 2 | the net's `+ USE` is `GROUND` |
| 3 | `+ USE CLOCK`; **or** (name rule) the lower-cased net name, split into tokens on every non-alphanumeric character (`[^a-z0-9]+`), has a token that contains `clk` or equals `clock` |
| 4 | the lower-cased name contains `reset` or starts with `rst`, or a token starts with `rst` or contains `reset` |
| 5 | the lower-cased name contains `scan`, or a token starts with `scan` or equals one of `se si so test testmode` |
| 0 | otherwise — a **signal net** |

(A net whose `+ USE` is absent counts as `SIGNAL`. The SDC is not used to classify nets: its
clock is created through a Tcl expression, not a literal `[get_ports <name>]`.)

An I/O pin is classified with the same rules, applied to **the name of the net the pin is
attached to** (`+ NET` in `PINS`) together with **the pin's own `+ USE`** from `PINS`
(absent = `SIGNAL`).

### 3.2 Kept sets

- **pins**: every instance-pin connection `(inst, pin)` of a signal net (type 0).
- **gates**: every component whose master name does **not** contain `FILL` or `TAP`
  (case-insensitive) and that owns at least one kept pin.
- **nets**: every signal net that has at least one kept pin or at least one kept I/O pin
  connection.
- **iopins**: every DEF `PINS` entry whose own classification (3.1) is 0. Its net does **not**
  have to be kept: e.g. a port `clock0` (no `clk` token) whose net carries `+ USE CLOCK` in
  `NETS` is a kept iopin with no edge.

Consequences: power/ground nets, the clock tree (clock nets, clock buffers whose pins are all
on clock nets), reset/scan-named nets, filler and tap cells are not in the graph.

## 4. Node order and node names

Nodes are laid out in type blocks, in this block order, and each block is sorted by name
with plain lexicographic string ordering of the DEF names (Python `sorted`):

- view **b**: gates (by inst name), nets (by net name), iopins (by pin name), pins (by the
  tuple `(inst, pin)`);
- view **c**: gates, nets, iopins (same three blocks as b, no pin block).

`data.node_name` is a Python `list[str]` of length N in node order: gate → inst name, net →
net name, iopin → port name, pin → `"inst/pin"`.

## 5. Node features `x` (float32, shape `[N, 10]`)

`x[:,0]` = node type: 0 gate, 1 net, 2 iopin, 3 pin. `x[:,1]` = a graph identifier, any one
constant value for the whole design (not scored). `x[:,2:10]` = up to 8 per-type features,
zero-padded on the right:

**gate** (x2..x8, x9 = 0)

| slot | name | definition |
|---|---|---|
| x2 | cell_type_id | the master's index in the sorted (upper-case, Python `sorted`) list of all cell names in the Liberty file, 0-based; a master not in the Liberty → N (= number of Liberty cells) |
| x3 | cell_area | the Liberty `area` of the master (0 if unknown) |
| x4 | cell_power | the Liberty `cell_leakage_power` of the master, in the library's unit (0 if unknown) |
| x5 | x_um | DEF placement x of the instance (the `PLACED`/`FIXED` point, i.e. the oriented cell's lower-left corner) ÷ 2000 |
| x6 | y_um | same for y |
| x7 | orientation_id | N 0, S 1, E 2, W 3, FN 4, FS 5, FE 6, FW 7 |
| x8 | placement_status_id | PLACED 0, FIXED 1, UNPLACED 2 |

**net** (x2..x9)

| slot | name | definition |
|---|---|---|
| x2 | net_type_id | 3.1 (always 0 for kept nets) |
| x3 | fanout | `max(0, pin_count − 1)` |
| x4 | pin_count | number of connections in the net's DEF connection list, instance pins **and** `( PIN … )` entries, kept or not |
| x5 | num_drivers | over the same connections: an instance pin whose Liberty direction is `output` counts 1, `inout` counts 1; a `( PIN p )` whose `PINS` `+ DIRECTION` is `INPUT` counts 1 (a chip input drives the net), `INOUT`/`FEEDTHRU` counts 1; anything else 0 |
| x6 | num_sinks | likewise: instance pin direction `input` or `inout` → 1; PIN direction `OUTPUT`, `INOUT` or `FEEDTHRU` → 1 |
| x7 | connects_macro_flag | 1 if the net touches an instance of a hard macro (no macros exist in this corpus → always 0) |
| x8 | num_layer | number of distinct routing layers (tech-LEF `TYPE ROUTING` layer names, case-insensitive) that the net's routing statements are on: the layer named right after `ROUTED`, `FIXED`, `COVER` or `NEW` in the net's routing, counting via-only and patch statements too; 0 if unrouted |
| x9 | hpwl_um | half-perimeter of the bounding box of the net's connection points, in µm: an I/O pin's point is its `PINS` placement point; an instance pin's point is the instance placement point plus the pin's in-cell position under the instance orientation, where the in-cell position is the mean of the centres of all `RECT`s listed for that pin in the cell LEF (all PORT layers). 0 for a net with no connection points |

Orientation maps a cell-local point (px, py) of a cell of LEF size (w, h) to an offset from
the placement point: N (px, py); S (w−px, h−py); W (h−py, px); E (py, w−px); FN (w−px, py);
FS (px, h−py); FW (py, px); FE (h−py, w−px).

**iopin** (x2..x5, rest 0)

| slot | name | definition |
|---|---|---|
| x2 | pin_x_um | `PINS` placement x ÷ 2000 |
| x3 | pin_y_um | `PINS` placement y ÷ 2000 |
| x4 | nearest_tap_distance_um | Euclidean distance (µm) from (pin_x, pin_y) to the nearest placement point of a component whose master contains `TAP`; 0 if there is none |
| x5 | pin_direction_id | `PINS` `+ DIRECTION`: INPUT 0, OUTPUT 1, INOUT 2, FEEDTHRU 3, other/absent −1 |

**pin** (x2..x3, rest 0)

| slot | name | definition |
|---|---|---|
| x2 | pin_type_id | category of the pin, see table below |
| x3 | sum_pin_cap_fF | a **net-level** value shared by every pin of the same net: Σ over the instance pins in the net's connection list of that pin's Liberty `capacitance` (pins without a `capacitance` attribute contribute 0; never `max_capacitance`), **plus** the SPEF grounded capacitance (2-node `*CAP` entries) located on the net's top-level port nodes (SPEF node name = the port name). In fF |

`pin_type_id` — first matching rule, on the upper-cased pin name P and the Liberty pin
direction D of the instance's master:

| id | rule |
|---|---|
| 12 | P ∈ {VDD, VPWR, VCC, VCCA, VCCD} |
| 13 | P ∈ {VSS, VGND, GND, VSSA, VSSD} |
| 11 | D is inout (or feedthru) |
| 5 | the Liberty pin has `clock : true`, or P contains `CLK`, or P ∈ {CK, CP, CLOCK} |
| 6 | P starts with `RST`, or contains `RESET`, or P ∈ {RN, RESETN, RSTN} |
| 7 | P starts with `SET`, or P ∈ {SN, SETN} |
| 8 | P ∈ {E, EN, OE, TE, GATE} |
| 9 | P starts with `SCAN`, or P ∈ {SE, SI, SO} |
| 10 | P ∈ {S, S0, S1, SEL, SELECT} and D is not output |
| 0 / 1 / 2 / 3 | D is input and P starts with A / B / C / anything else |
| 4 | D is output |
| 14 | anything else (unknown direction) |

## 6. Labels `y` and their raw twins `y_raw` (float32, shape `[N, 6]`)

`y[:,0] = y_raw[:,0] = x[:,0]` (node type). Slots 1–5 hold labels; **NaN wherever a label does
not apply to the node type** (or is undefined for that node, as stated). `y` holds the
normalized training target, `y_raw` the raw physical value, slot for slot, with identical NaN
placement.

| slot | node type | `y_raw` (raw) | `y` (normalized) |
|---|---|---|---|
| 1 congestion | gate | mean of G over the cell's GCells (see 6.1) | mean of √G over the same GCells |
| 2 IR drop | gate | **out of scope**: any value or NaN on gate rows (not scored); must be NaN on other node types | same |
| 3 timing | pin (view b) | path delay (ns) of the pin's **owning instance** (6.2) | `log(1 + path_delay)` |
| 4 wirelength | net | routed length (µm) of the net (6.3) | `log1p(length)` |
| 5 ground cap | net | SPEF ground capacitance of the net (fF) (6.4); NaN if the net has no `*D_NET` in the SPEF | `log1p(cap)` |

In view c there are no pin nodes, so slot 3 is NaN on every node (it moves to `edge_y`, §7).

### 6.1 Congestion

- GCell grid: step `sx`, `sy` (DBU) = the `STEP` of the DEF's `GCELLGRID X` / `GCELLGRID Y`
  statements; the grid has `nx = ceil(die_width / sx)` × `ny = ceil(die_height / sy)` cells
  (die from `DIEAREA`, which starts at (0,0) here). A coordinate `c` (DBU) lies in GCell index
  `floor(c / step)`.
- Demand: every wire segment of every regular net (all net types, including clock nets),
  i.e. every pair of consecutive points inside one routing statement of the DEF `NETS`
  section (`*` repeats the previous coordinate; `RECT` patches and vias contribute nothing).
  A horizontal segment adds its length, split at GCell boundaries, to the horizontal demand
  `Dh` of the GCells it passes through in the row of its y; a vertical segment likewise to the
  vertical demand `Dv` in the column of its x. Lengths in µm. Demand falling outside the grid
  is dropped.
- Capacity (the same for every GCell): `Ch = Σ over HORIZONTAL routing layers of
  (sx/2000)·(sy/2000)/pitch`, `Cv` likewise over VERTICAL layers (pitch from the tech LEF, µm;
  all routing layers metal1..metal10).
- Utilization `U = max(Dh/Ch, Dv/Cv)` per GCell (0 where there is no demand), on the dense
  `nx × ny` grid.
- `G` = `U` smoothed by a 2-D Gaussian filter with σ = 1 GCell, kernel radius 4, and
  reflect boundary handling `(d c b a | a b c d | d c b a)` — exactly
  `scipy.ndimage.gaussian_filter(U, sigma=1.0)` with its defaults.
- Per gate: the cell's bounding box is its placement point plus its LEF `SIZE` (w, h),
  with w and h swapped for orientations E, W, FE, FW. It covers GCell columns
  `floor(x_lo/sx) … floor(x_hi/sx)` and rows `floor(y_lo/sy) … floor(y_hi/sy)` (inclusive;
  indices clamped into the grid). The raw label is the mean of `G` over those GCells; the
  normalized label is the mean of `√G` over them.

### 6.2 Timing (path delay)

Static timing analysis with OpenSTA (as embedded in OpenROAD) on the routed netlist:
the Liberty file above, the DEF connectivity, and `constraint.sdc` read whole as given.
**No parasitics** (no SPEF, no wire-load), and no constraint added or changed (in particular
clocks stay ideal unless the SDC says otherwise).

- For every pin of every instance take its setup (max) slack. Ignore pins whose slack is
  unconstrained (|slack| ≥ 1e29).
- An instance's worst slack `s` = the minimum over its pins. `T` = the smallest clock period
  among the SDC's clocks.
- `path_delay = max(0, T − s)` ns; the normalized label is `log(1 + path_delay)`.
- An instance with no constrained pin (off every timed path) has path_delay **0** and label
  **0** (not NaN).
- Every kept pin carries its owning instance's values.

### 6.3 Wirelength

Sum over the net's routing statements in `NETS` of `|x2−x1| + |y2−y1|` over consecutive
points (same segment rules as 6.1: `*` repeats a coordinate, `RECT` patches and vias add
nothing), ÷ 2000 → µm. A net with no routing has length 0.

### 6.4 Ground capacitance

For the net's `*D_NET` in the SPEF: the sum of all grounded `*CAP` entries (lines with one
node and a value; lines with two nodes are coupling capacitors and are excluded), times the
`*C_UNIT`, in fF. SPEF names go through `*NAME_MAP`; SPEF and DEF may escape characters
differently (SPEF names here are unescaped, DEF escapes `[`/`]` inside instance names), so
match names after removing backslashes.

## 7. Edges

All edge tensors are `int64` `edge_index[2, E]`; every undirected edge appears as two directed
columns, forward then reverse, adjacent: `[fwd0, rev0, fwd1, rev1, …]`.

### View b

Undirected edges, generated in this order and with each directed (src, dst) pair emitted only
once:
1. gate–pin: for every kept pin (sorted by `(inst, pin)`), gate `inst` ↔ pin `inst/pin`;
2. pin–net: for every kept pin, sorted by `(inst, pin, net)`, pin ↔ its net;
3. iopin–net: for every kept iopin whose net is a kept net, sorted by `(iopin, net)`,
   iopin ↔ its net.

View b has no `edge_attr`/`edge_y`.

### View c

Pins are folded into gate–net edges:
1. one undirected gate–net edge **per kept pin**, (gate = the pin's instance, net = the pin's
   net), in `(inst, pin, net)` order — a gate with two pins on the same net gets two parallel
   edges; then
2. one undirected iopin–net edge per kept iopin whose net is kept, in `(iopin, net)` order.

Per directed column (both directions of an edge carry identical rows):

| tensor | shape / dtype | pin-derived edge | iopin edge |
|---|---|---|---|
| `edge_type` | `[E]` int64 | 0 | 1 |
| `edge_attr` | `[E, 8]` float32 | `[pin_type_id, sum_pin_cap_fF, 0, 0, 0, 0, 0, 0]` of that pin (§5) | all 0 |
| `edge_y` | `[E, 6]` float32 | `[0, NaN, NaN, timing label of the pin's instance, NaN, NaN]` | `[1, NaN, NaN, NaN, NaN, NaN]` |
| `edge_y_raw` | `[E, 6]` float32 | same with the raw path delay (ns) in column 3 | same as `edge_y` |

## 8. Required fields

| view | required attributes on the `Data` object |
|---|---|
| b | `x`, `y`, `y_raw`, `edge_index`, `node_name` |
| c | `x`, `y`, `y_raw`, `edge_index`, `edge_attr`, `edge_type`, `edge_y`, `edge_y_raw`, `node_name` |

Other attributes are allowed and ignored. Out of scope (not required, not scored): IR drop,
coupling capacitance, equivalent resistance, graph-level features, views d/e/f.

## 9. How the output is judged

A converter run **succeeds** on a design when `run.sh` exits 0 within 3,600 s and both
files load and pass the public format check (`check_format.py`: shapes, dtypes, required
fields, block layout, NaN placement, edge layout). Correctness is then measured against
independently computed ground truth, by name, per check group: node identity (gates, nets,
I/O pins, pins), node order, edges of b and c, the feature groups of §5, the c edge features of
§7, and each label of §6. A group passes on a design only if every entity in it matches.
Tolerances (pass iff `|a−b| ≤ atol + rtol·|b|`; integers and categories must be exact):

| quantity | atol | rtol |
|---|---|---|
| coordinates, distances (µm) | 1e-4 | 1e-6 |
| hpwl_um | 6e-3 | 1e-6 |
| cell_area, cell_power, sum_pin_cap_fF | 1e-6 | 1e-5 |
| congestion (raw and normalized) | 1e-6 | 1e-5 |
| timing path delay (ns) and its label | 1e-4 | 1e-6 |
| wirelength µm | 2e-4 | 1e-6 |
| wirelength label | 1e-6 | 1e-6 |
| ground cap fF and its label | 1e-5 | 1e-5 |

You will not see the ground truth or the correctness scores during development; the format
checker is the only feedback the harness provides.
