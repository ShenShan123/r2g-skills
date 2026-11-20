# Normative B–F graph semantics

This appendix defines the evaluated quantities, including categorical encodings.
It contains no converter implementation or reference outputs. It takes precedence
over the accompanying narrative documents wherever they differ. The units,
equations, identity rules and encodings are the same for every experimental arm.

## Inputs and units

Use `6_final.def` for geometry, connectivity and route lengths, `6_final.sdc` for
STA, and `6_final.spef` for parasitics. The DEF's `UNITS DISTANCE MICRONS` value is
the number of database units per micrometre; do not assume a constant across
technologies. Only regular DEF `NETS` are used; `SPECIALNETS` are excluded.
Liberty capacitances are converted to fF using `capacitive_load_unit` and SPEF
capacitances/resistances using `*C_UNIT`/`*R_UNIT` (fF and ohms in the output).
Quoted Liberty attribute values and `.lib.gz` are valid input forms.

The supplied technology directory contains these matching files:

| Technology | Technology LEF | Cell LEF | Liberty |
| --- | --- | --- | --- |
| nangate45 | NangateOpenCellLibrary.tech.lef | NangateOpenCellLibrary.macro.mod.lef | NangateOpenCellLibrary_typical.lib |
| sky130hd | sky130_fd_sc_hd.tlef | sky130_fd_sc_hd_merged.lef | sky130_fd_sc_hd__tt_025C_1v80.lib |
| gf180 | gf180mcu_5LM_1TM_9K_9t_tech.lef | gf180mcu_5LM_1TM_9K_9t_sc.lef | gf180mcu_fd_sc_mcu9t5v0__ff_n40C_5v50.lib.gz |

## Identities, filtering and ordering

A gate is a DEF component, a net is a regular DEF net, an iopin is a DEF `PINS`
entry, and a pin is an instance-pin incidence `(instance, pin)` in a net's
connection list. A `(PIN port)` connection denotes an iopin. A pin's serialized
name is `instance/pin`; split at the last slash when recovering its identity.
Names may retain DEF backslash escapes or omit them; identity comparisons remove
every backslash. Other characters and hierarchy separators must be preserved.

Net type uses the first matching rule below. Let `s` be the lower-case net name
and let tokens be its nonempty pieces split on `[^a-z0-9]+`. Absent `+ USE` means
SIGNAL. This classification uses the DEF use/name rules, rather than adding SDC
clock-port classification.

| net_type_id | First matching rule |
| --- | --- |
| 1 | USE is POWER |
| 2 | USE is GROUND |
| 3 | USE is CLOCK, or a token contains `clk` or equals `clock` |
| 4 | `s` contains `reset`, starts with `rst`, or a token starts with `rst` or contains `reset` |
| 5 | `s` contains `scan`, or a token starts with `scan` or equals `se`, `si`, `so`, `test` or `testmode` |
| 0 | Otherwise |

An iopin uses the same classification applied to its attached net's **name**
(`+ NET` in PINS) and its own `+ USE` in PINS. Its own spelling is not substituted
for the net name.

- Keep instance pins on type-0 nets whose component is present and whose master
  contains neither FILL nor TAP, case insensitive.
- Keep gates that own at least one such pin.
- Keep type-0 nets with at least one kept pin or a kept iopin connection.
- Keep all type-0 iopins. An isolated kept iopin can exist even if its net is not
  kept, because PINS and NETS carry separate USE fields.

Nodes occur in the following type blocks. Within each block, sort by the
DEF-written names with ordinary lexicographic ordering, including any DEF
escape characters in the sort key. Pins sort by `(DEF-written instance, pin)`.
Identity matching then removes backslashes as described above.

| View | Type blocks, in order |
| --- | --- |
| b | gate, net, iopin, pin |
| c | gate, net, iopin |
| d | gate, iopin, pin |
| e | iopin, pin |
| f | gate, iopin |

## Node tensors and features

All numeric features and labels use float32. Homogeneous `x[N,10]` starts with
`node_type` (gate=0, net=1, iopin=2, pin=3), then `graph_id`, then eight per-type
features below. `graph_id` is any constant design identifier and is not a
physical-accuracy target. Pad unused feature columns with zero.

| Type | Eight features, in column order |
| --- | --- |
| gate | cell_type_id, cell_area, cell_power, x_um, y_um, orientation_id, placement_status_id, 0 |
| net | net_type_id, fanout, pin_count, num_drivers, num_sinks, connects_macro_flag, num_layer, hpwl_um |
| iopin | pin_x_um, pin_y_um, nearest_tap_distance_um, pin_direction_id, 0, 0, 0, 0 |
| pin | pin_type_id, sum_pin_cap_fF, 0, 0, 0, 0, 0, 0 |

`cell_power` and IR-drop values are excluded from physical accuracy. Retain the
slots. Other feature quantities are defined as follows:

- `cell_type_id`: zero-based index of the component's upper-case master name in
  the sorted upper-case cell names of the supplied standard-cell Liberty; an
  unknown master has ID N, where N is that vocabulary size. `cell_area` is the
  master's Liberty area, zero when unknown. This corpus uses standard-cell
  layouts; `connects_macro_flag` is zero.
- Gate `x_um,y_um` are the DEF PLACED/FIXED placement point divided by DBU/um.
  An unplaced entity has NaN coordinates. Placement IDs are PLACED=0, FIXED=1,
  UNPLACED=2; orientation IDs are N=0, S=1, E=2, W=3, FN=4, FS=5, FE=6, FW=7.
- `pin_count` counts **all** original instance-pin and top-port incidences of the
  net, including incidences later excluded from graph nodes. `fanout` is
  `max(0,pin_count-1)`.
- `num_drivers` counts instance pins with Liberty direction output/inout and top
  ports with DEF direction INPUT/INOUT/FEEDTHRU. `num_sinks` counts instance pins
  with direction input/inout and top ports with OUTPUT/INOUT/FEEDTHRU. Missing
  directions contribute zero. A top-level INPUT drives the chip-internal net.
- `num_layer` is the number of distinct tech-LEF TYPE ROUTING layers named by the
  net's routing statements (case insensitive). Via-only and patch statements can
  establish layer presence, although their geometric length is zero.
- `hpwl_um` is the connection-point bounding box width plus height. A top-port
  connection point is its DEF placement point. An instance pin's point is its
  orientation-transformed cell-LEF pin position added to the placement point.
  With no usable LEF pin geometry, use the instance placement point. Ignore
  unplaced connection points; no points means HPWL zero.
- A cell-LEF pin position is the mean of its shape-box centres across all PORT
  shapes/layers. A RECT is one box. For a rectilinear POLYGON, use its maximal
  horizontal-slab box decomposition, merging vertically adjacent slabs with the
  same x span, as in OpenDB getAvgXY. For cell SIZE `(w,h)`, local `(px,py)` maps
  to these offsets from the DEF placement point:

| Orientation | Offset |
| --- | --- |
| N | (px,py) |
| S | (w-px,h-py) |
| W | (h-py,px) |
| E | (py,w-px) |
| FN | (w-px,py) |
| FS | (px,h-py) |
| FW | (py,px) |
| FE | (h-py,w-px) |

- Iopin coordinates use its DEF placement point. Its direction IDs are INPUT=0,
  OUTPUT=1, INOUT=2, FEEDTHRU=3, unknown=-1.
- `nearest_tap_distance_um` is Euclidean distance from the iopin placement point
  to the nearest placed component whose upper-case master contains TAP; gf180
  also treats FILLTIE and ENDCAP as tap patterns. Zero if no such component exists.
- `sum_pin_cap_fF` is shared by every pin on a net: sum the present Liberty
  **capacitance** attributes of its instance pins, plus SPEF grounded capacitance
  located on its top-port nodes. Missing capacitance contributes zero. Never use
  `max_capacitance`, which is an output-drive limit, or total wire ground cap in
  place of port-node ground cap. Input pins normally supply the Liberty load;
  the normative rule is the present capacitance attributes, not a fabricated
  output-drive limit.

`pin_type_id` uses the first matching rule below. P is the upper-case instance
pin name, D its Liberty direction. Liberty `clock : true` accepts quoted values.

| pin_type_id | First matching rule |
| --- | --- |
| 12 | P is VDD, VPWR, VCC, VCCA or VCCD |
| 13 | P is VSS, VGND, GND, VSSA or VSSD |
| 11 | D is inout or feedthru |
| 5 | Liberty clock=true, P contains CLK, or P is CK, CP or CLOCK |
| 6 | P starts with RST, contains RESET, or is RN, RESETN or RSTN |
| 7 | P starts with SET, or is SN or SETN |
| 8 | P is E, EN, OE, TE or GATE |
| 9 | P starts with SCAN, or is SE, SI or SO |
| 10 | P is S, S0, S1, SEL or SELECT and D is not output |
| 0 / 1 / 2 / 3 | D is input and P starts with A / B / C / any other prefix |
| 4 | D is output |
| 14 | Otherwise |

## Node labels

Homogeneous `y` and `y_raw` are float32 `[N,6]`, beginning with node type; the
remaining five slots are congestion, IR drop, timing, wirelength, ground cap.
Except for excluded gate power values, off-type slots are NaN. Undefined labels
are also NaN, with the same mask in both twins.

| Slot | Applied node type | Raw value | Normalized value |
| --- | --- | --- | --- |
| congestion | gate | mean G over the gate's GCells | mean sqrt(G) over the same GCells |
| IR drop | gate only | Excluded | Excluded |
| timing | pin | Owning instance's path delay, ns | log1p(raw) |
| wirelength | net | Net route length, um | log1p(raw) |
| ground cap | net in b/c; pin in d/e | Net's SPEF ground cap, fF, broadcast to its kept pins in d/e | log1p(raw) |

No ground cap survives in f. Wirelength/timing/congestion whose entity has been
folded instead appear on the appropriate folded-edge family below.

**Wirelength:** sum Manhattan lengths of consecutive route points in the net's
regular DEF routing statements, divided by DBU/um. A `*` coordinate repeats its
previous value. Vias and RECT patches add no length. An unrouted net has zero
length. Count ordinary signal and non-signal nets when forming congestion demand,
but only kept signal nets when assigning graph wirelength labels.

**Congestion:** let `(x0,y0,x1,y1)` be DIEAREA bounds. For DEF GCELLGRID steps
`sx,sy`, the grid has `ceil((x1-x0)/sx)` by `ceil((y1-y0)/sy)` bins. Bin a DBU
coordinate with `floor((x-x0)/sx)` / `floor((y-y0)/sy)`. Split each horizontal or
vertical regular-net route segment at bin boundaries and add its overlapping
length in um to the corresponding horizontal/vertical demand grid `Dh,Dv`.
Discard out-of-grid demand. For cell width `gw=sx/DBU` and height `gh=sy/DBU`,
capacity is `Ch=sum(gw*gh/pitch)` over all horizontal routing layers, and similarly
`Cv` over vertical layers. If LEF PITCH has two values, use its y pitch for a
horizontal layer and x pitch for a vertical layer. Utilization is
`U=max(Dh/Ch,Dv/Cv)`; unused bins are zero. G is a radius-4, sigma-1 separable
Gaussian smoothing of U with reflect boundary handling (the boundary sample is
repeated, as in scipy.ndimage.gaussian_filter defaults). A gate covers bins from
the floors of its bbox lower and upper coordinates **inclusive**, clamped to the
grid. Use orientation-aware LEF SIZE for the bbox (swap w/h for E,W,FE,FW).

**Timing:** run OpenSTA on supplied Liberty, LEFs and final DEF connectivity,
reading the whole final SDC unchanged. Do not read SPEF or add clock/IO constraints.
For each instance, take the minimum setup/max slack among its pins, ignoring
unconstrained `abs(slack)>=1e29` or nonfinite slacks. Let T be the smallest period
among all SDC clocks. Its delay is `max(0,T-worst_slack)` ns, assigned to every kept
pin of that instance. An instance with no constrained pin has zero delay, rather
than NaN. Clocks are ideal unless the supplied SDC specifies otherwise.

**Ground cap:** sum only grounded entries in the net's SPEF CAP block, converting
to fF. A CAP entry with two nodes is coupling and must not enter ground cap. A net
absent from SPEF has NaN ground cap; an existing block with no ground entries has
zero. Resolve NAME_MAP and remove escape backslashes for joins.

## Topology, folded rows and both graph forms

Every undirected physical/folded edge is represented twice. Homogeneous
`edge_index` is int64 `[2,E]` in interleaved forward/reverse pairs. Edge order
between different undirected edges is immaterial. Keep parallel edges: the edge
identity includes its folded entity, rather than just its two surviving endpoints.
Remove duplicate endpoint appearances **within** one folded entity before forming
its clique, and do not create self-loops.

| View | Homogeneous family ID | Relation | Undirected physical edges |
| --- | --- | --- | --- |
| b | 0 / no edge_type required | connects | Gate–pin, pin–net and iopin–net incidences |
| c | 0 | pin | One gate–net edge for each kept pin |
| c | 1 | iopin_connection | Kept iopin–net incidences |
| d | 0 | gate_pin | Kept gate–pin incidences |
| d | 1 | net | For each kept net, clique over its kept pins and iopins |
| e | 0 | gate | For each kept gate, clique over its kept pins |
| e | 1 | net | Same net cliques as d |
| f | 0 | net | For each kept net, clique over its kept gates and iopins |

Homogeneous c/d/e/f require int64 `edge_type[E]`, float32 `edge_attr[E,8]`,
`edge_y[E,6]` and `edge_y_raw[E,6]`. Label twins start with the edge family ID;
their remaining slots follow the node label order above. Each forward/reverse
pair carries the same semantic row. Folded entities supply these rows:

| Family | edge_attr[8] | Finite edge labels, and raw twins |
| --- | --- | --- |
| pin | pin_type_id, sum_pin_cap_fF, six zeros | Owning gate's timing only |
| iopin_connection / gate_pin | Eight zeros | None; all five label slots NaN |
| net | Net's eight node features | Wirelength only |
| gate | Gate's seven node features, then zero | Congestion only; power value excluded |

Ground capacitance is never a folded-edge label. The other off-family label
slots are NaN. Parallel rows sharing endpoints are evaluated as a multiset of
**complete semantic rows**, maintaining feature/label attachment; arbitrary edge
serialization order cannot itself cause a mismatch.

Heterogeneous `{view}_graph.pt` uses HeteroData, `graph_kind='hetero'`, and one
store per documented node type. Each type has `node_name[N_type]`, float32
`x[N_type,9]` and `y,y_raw[N_type,5]`: remove the redundant leading type column
from its homogeneous block and preserve every other nonpower value and order.
The homogeneous file is `{view}_graph_homo.pt` and is a Data object with a global
`node_name[N]` list. Extra metadata is allowed. A schema attribute, when present,
must describe the fixed column order above; its absence does not change the order.

Hetero physical edges use `(source_type,relation,destination_type)` stores with
int64 `edge_index[2,E_relation]` in type-local indices. c/d/e/f carry the
corresponding `edge_attr[E_relation,8]`, `edge_y` and `edge_y_raw[E_relation,5]`
slices, dropping only homogeneous label column zero. Both directions must exist,
with identical semantic rows; per-store edge order is immaterial. Empty relations
need not be serialized.

## RC edge labels

RC uses an edge set separate from physical/folded topology. Ground cap remains
on nodes. Coupling and resistance attach as follows, with both directed copies:

| View | Coupling | Resistance |
| --- | --- | --- |
| b | Kept net–net | Kept pin/iopin–pin/iopin |
| c | Kept net–net | None |
| d/e | Kept driver pin/iopin–driver pin/iopin | Kept pin/iopin–pin/iopin |
| f | None | None |

For a coupling net, its driver is the first instance connection with SPEF
direction O; otherwise the first top-port connection with direction I; otherwise
its first SPEF connection. d/e drop a coupling whose resolved driver is not a
kept node. Drop self-edges after attachment. Resistance uses only pin/port pairs
in the same connected resistor component whose endpoints survive in the view.

**Coupling cap:** resolve each endpoint token to its own net, independently of the
CAP block containing it. Count each physical four-field CAP entry once by its
unordered raw node-token pair, including mirror entries in the partner block.
Sum values per unordered cross-net pair, in fF. Same-net coupling is not a
cross-net target.

**Resistance:** include every pair of SPEF CONN pins/ports connected through the
net's RES graph. On a tree, resistance is the sum along the unique path. For a
graph with cycles or parallel resistors, the target is the actual Kirchhoff
effective resistance, rather than a traversal spanning-tree approximation.
Equivalently, inject one ampere at one endpoint and withdraw it at the other;
their voltage difference is the target in ohms. Disconnected endpoints have no
resistance edge. This rule supersedes the approximation mentioned in the older
narrative label document.

Every homogeneous graph requires present-but-possibly-empty `rc_edge_index[2,R]`
and `rc_edge_type[R]` int64, and float32 `rc_edge_y,rc_edge_y_raw[R,3]`. RC type
0 is coupling and type 1 is resistance. Rows are respectively
`[0,log1p(cap_fF),NaN]` and `[1,NaN,log1p(res_ohm)]`; raw twins carry the raw value
in the same slot. Interleave forward/reverse edges and repeat their rows. F has
empty RC tensors. Hetero relations are `rc_coupling` or `rc_resistance`, with
type-local indices and two-column `rc_edge_y,rc_edge_y_raw` after dropping the
homogeneous type column. Empty hetero RC relations may be absent.

## Physical tolerances and primary accuracy

Every numerical check uses `abs(claim-truth) <= atol + rtol*abs(truth)`.
Categorical/integer values, identities, masks, graph families, topology and
serialization dimensions are exact. Physical NaNs must occur where specified;
they do not earn primary label-accuracy credit.

| Quantity | atol | rtol |
| --- | --- | --- |
| Coordinates/distances, um | 1e-4 | 1e-6 |
| HPWL, um | 6e-3 | 1e-6 |
| Liberty area and pin capacitance | 1e-6 | 1e-5 |
| Congestion raw/normalized | 1e-6 | 1e-5 |
| Timing raw ns / normalized | 1e-4 | 1e-6 |
| Wirelength raw um | 2e-4 | 1e-6 |
| Wirelength normalized | 1e-6 | 1e-6 |
| Ground/coupling capacitance raw fF | 1e-5 | 1e-5 |
| Resistance raw ohms | 1e-4 | 1e-5 |
| Ground-cap normalized | 1e-5 | 1e-5 |
| Coupling/resistance normalized | 1e-6 | 1e-5 |

Full acceptance requires all ten graphs and every applicable nonpower check.
Primary label accuracy uses **B hetero raw labels** as the canonical copy: gate
congestion, pin timing, net wirelength, net ground capacitance, and its coupling
and resistance edge labels. Each finite truth key is counted once, with one
undirected copy for RC. Missing keys/outputs count as incorrect; duplicate
submitted identities cannot supply best-of-many credit. Compute the rate for
each of the six families with finite truth entries, average applicable family
rates within a design, and average designs equally. A family with no finite
truth entry is inapplicable, rather than an automatic correct value. Other
views, graph forms, raw/normalized twins, padding and off-type NaNs do not add
weight to this accuracy metric, but remain subject to full acceptance.
