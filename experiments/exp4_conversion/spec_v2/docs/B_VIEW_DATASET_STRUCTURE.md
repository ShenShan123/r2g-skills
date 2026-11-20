# R2G2.0 Four-Stage B-View Dataset Structure Specification

## 1. Scope and status

This document specifies the finalized **B view** produced by the five-script dataset
builder in this directory. A B-view sample is a PyTorch Geometric `HeteroData` object
with four canonical node types, logical incidence relations, optional physical
relations, and task-specific node/edge supervision.

The concrete reference used to verify every dimension and relation in this document is:

```text
/data1/zhouzw/R2G2.0_dataset_full_4stage/generated/bp_multi_top/v01/stages/
```

The four files are:

```text
floorplan/heterograph.pt
placement/heterograph.pt
cts/heterograph.pt
route/heterograph.pt
```

All four graphs share the same post-synthesis Canonical topology and the same
post-route labels. Their input features differ according to the causal cutoff of the
stage being predicted.

## 2. Quick dimension summary

### 2.1 Graph and node tensors

| Store | Meaning | Feature tensor | Feature dimensions | Label tensor | Label dimensions |
|---|---|---:|---:|---:|---:|
| graph | Design-level context | `global_features` | 19 | none | 0 |
| `gate` | Synthesized cell instance | `gate.x` | 30 | `gate.y` | 2 |
| `net` | Canonical synthesized Net | `net.x` | 28 | `net.y` | 2 |
| `io_pin` | Top-level design port | `io_pin.x` | 30 | `io_pin.y` | 2 |
| `pin` | Internal instance Pin | `pin.x` | 37 | `pin.y` | 2 |

Each node label tensor has a Boolean `y_valid_mask` with the same shape as `y`.
Feature dimensions remain fixed across stages; unavailable early-stage physical values
are stored as `NaN`, with the corresponding validity feature set to zero.

The last feature dimension of every node type is `graph_id`. It is currently the
constant numeric value `0` for a single stored graph and is reserved as a graph
membership placeholder for batching or downstream conversion.

### 2.2 Edge tensors

| Relation | Meaning | Input `edge_attr` dimensions | Supervision `edge_y` dimensions | Availability |
|---|---|---:|---:|---|
| `gate|has|pin` | Cell ownership of an internal Pin | 2 | 0 | all stages |
| `pin|connects_to|net` | Internal Pin-to-Net incidence | 2 | 0 | all stages |
| `io_pin|connects_to|net` | Top-level port-to-Net incidence | 2 | 0 | all stages |
| `gate|congestion_geom|gate` | Same-grid sparse physical neighborhood | 1 | 0 | CTS and Route only |
| `pin|timing_path|pin` | Directed OpenSTA path segment | 0 | 2 | all stages when present |
| `pin|timing_path|io_pin` | Directed path ending at a top-level port | 0 | 2 | all stages when present |
| `io_pin|timing_path|pin` | Directed path starting at a top-level port | 0 | 2 | all stages when present |
| `net|rc_coupling|net` | Extracted capacitive coupling pair | 0 | 1 | all stages when present |
| `pin|rc_resistance|pin` | Extracted directed effective-resistance pair | 0 | 1 | all stages when present |
| `pin|rc_resistance|io_pin` | Internal Pin to IO Pin resistance pair | 0 | 1 | all stages when present |
| `io_pin|rc_resistance|pin` | IO Pin to internal Pin resistance pair | 0 | 1 | all stages when present |

Timing and RC values are labels, not model input edge features. They are stored only in
`edge_y` with `edge_y_mask`; their `edge_attr` tensors have shape `[E, 0]`.

## 3. Four-stage causal contract

The stage name identifies the implementation stage to be predicted.

| Prediction graph | Input cutoff | Physical input | Standard-cell coordinates | HPWL/congestion inputs | Gate-Gate relation |
|---|---|---|---|---|---|
| `floorplan` | post-Yosys | no DEF | unavailable | unavailable | absent |
| `placement` | post-Floorplan | `2_floorplan.def` | untrusted; fixed macros and IO only | unavailable | absent |
| `cts` | post-Placement | `3_place.def` | trusted | available | present |
| `route` | post-CTS | `4_cts.def` | trusted | available | present |

`5_route.def`, final SPEF, PDNSim SP files, and post-route OpenSTA reports never enter
the feature extractor. They are used only to create the shared supervision attached to
all four prediction graphs.

### 3.1 Availability-state definitions

The following tables make a strict distinction between an entity/relation that does
not exist and a fixed-schema feature whose value is unavailable at a stage:

| State | Meaning |
|---|---|
| **Full** | The tensor/field exists and is finite for every entity in the verified `bp_multi_top/v01` graph. |
| **Partial** | The tensor/field exists, but only aligned/eligible entities have finite values; other rows are `NaN`. |
| **NaN** | The field remains in the fixed feature schema, but every value is `NaN` because the prediction cutoff does not expose that information. |
| **Present** | The edge relation store exists and contains edges. |
| **Absent** | The edge relation store itself is not created. This is different from a present zero-dimensional `edge_attr`. |
| **Conditional** | The relation is created only when its source labels and both endpoint node types have at least one aligned row. |

Validity fields such as `placement_valid`, `pin_position_valid`, `hpwl_valid`, and
`congestion_feature_valid` are finite zero/one features in all stages. Their value, not
their own finiteness, states whether the corresponding physical values are usable.

### 3.2 Node-type existence by stage

Canonical node stores never change across the four B views.

| Node type | Floorplan | Placement | CTS | Route | Boundary rule |
|---|---|---|---|---|---|
| `gate` | Present | Present | Present | Present | Always the post-Yosys Canonical Gate set; backend-added cells do not become nodes. |
| `net` | Present | Present | Present | Present | Always the post-Yosys Canonical Net set, including IO-only Nets. |
| `io_pin` | Present | Present | Present | Present | Always the top-level post-Yosys port set. |
| `pin` | Present | Present | Present | Present | Always the Canonical `(inst_name, pin_name)` set. |

For `bp_multi_top/v01`, the counts are identical in every stage: 78,808 Gates, 96,588
Nets, 1,453 IO Pins, and 293,452 internal Pins.

### 3.3 Graph-level feature availability

| Feature group | Floorplan | Placement | CTS | Route |
|---|---|---|---|---|
| Logical/design/SDC fields: counts, fanout, placement target, utilization, ABC area, Liberty capacitance, voltage, frequency, and clock statistics | Full | Full | Full | Full |
| DEF die fields: `die_width_um`, `die_height_um`, `die_area_um2`, `dbu_per_um` | NaN | Full | Full | Full |

### 3.4 Gate feature availability

| Gate feature group | Floorplan | Placement | CTS | Route |
|---|---|---|---|---|
| Cell identity/function, Boolean cell classes, drive strength, area, leakage, clock domain, and logical timing levels | Full | Full | Full | Full |
| Placement origin/centre/normalized centre and orientation/status | NaN | Partial: fixed macros only, 26/78,808 | Partial: 78,806/78,808 | Partial: 78,806/78,808 |
| `placement_valid` | Full; all values 0 | Full; 26 values 1 | Full; 78,806 values 1 | Full; 78,806 values 1 |
| Five congestion input values | NaN | NaN | Partial: 78,806/78,808 | Partial: 78,806/78,808 |
| `congestion_feature_valid` | Full; all values 0 | Full; all values 0 | Full; 78,806 values 1 | Full; 78,806 values 1 |
| `graph_id` | Full | Full | Full | Full |

The two unavailable physical Gate rows in CTS/Route are the Canonical constant cells
`_140261_/LOGIC1_X1` and `_140262_/LOGIC0_X1`; the backend replaced them with many
physical constant cells without changing the B-view topology.

### 3.5 Net feature availability

| Net feature group | Floorplan | Placement | CTS | Route |
|---|---|---|---|---|
| Logical type, endpoint counts, fanout, driver/sink counts, macro/clock flags, domain, and sink capacitance | Full | Full | Full | Full |
| Stage-Net lineage counts, split/rename flags, anchor coverage, alignment/ambiguity/lineage flags | Full | Full | Full | Full |
| `net_bbox_width_um`, `net_bbox_height_um`, `hpwl_um` | NaN | NaN | Full: 96,588/96,588 | Full: 96,588/96,588 |
| Segment total/max/mean HPWL | NaN | NaN | Full: 96,588/96,588 | Full: 96,588/96,588 |
| `hpwl_valid`, `stage_segment_hpwl_valid` | Full; all values 0 | Full; all values 0 | Full; all values 1 | Full; all values 1 |
| `graph_id` | Full | Full | Full | Full |

Lineage descriptors are always finite because they also describe the explicit
post-Yosys identity mapping used before physical HPWL becomes available. Their values
are stage-specific even though their tensor positions exist in all four stages.

### 3.6 IO Pin feature availability

| IO Pin feature group | Floorplan | Placement | CTS | Route |
|---|---|---|---|---|
| Direction/role, clock/startpoint/endpoint flags, SDC clock and IO constraints, logical timing levels, and connected Net type | Full | Full | Full | Full |
| Position, normalized position, die-boundary distances, layer ID, and nearest-TAP distance | NaN | Full: 1,453/1,453 | Full: 1,453/1,453 | Full: 1,453/1,453 |
| `pin_position_valid` | Full; all values 0 | Full; all values 1 | Full; all values 1 | Full; all values 1 |
| `graph_id` | Full | Full | Full | Full |

### 3.7 Internal Pin feature availability

| Internal Pin feature group | Floorplan | Placement | CTS | Route |
|---|---|---|---|---|
| Pin type/role/direction, Liberty electrical values, owner-cell identity/function, functional flags, SDC clock context, and logical timing levels | Full | Full | Full | Full |
| Position, normalized position, and die-boundary distances | NaN | Partial: fixed-macro Pins, 5,439/293,452 | Partial: 293,450/293,452 | Partial: 293,450/293,452 |
| `pin_position_valid` | Full; all values 0 | Full; 5,439 values 1 | Full; 293,450 values 1 | Full; 293,450 values 1 |
| `graph_id` | Full | Full | Full | Full |

### 3.8 Node-label availability

All node-label tensors and masks are copied unchanged to every stage. “Partial” below
means partial EDA supervision coverage, not stage-dependent feature availability.

| Node label | Floorplan | Placement | CTS | Route | Verified coverage |
|---|---|---|---|---|---:|
| Gate `cell_congestion` | Partial | Partial | Partial | Partial | 78,806/78,808 |
| Gate `ir_drop_mV` | Partial | Partial | Partial | Partial | 78,806/78,808 |
| Net `routed_wirelength_um` | Full | Full | Full | Full | 96,588/96,588 |
| Net `ground_cap_pF` | Partial | Partial | Partial | Partial | 83,510/96,588 |
| Pin `setup_slack_ns`, `hold_slack_ns` | Partial | Partial | Partial | Partial | 18,073/293,452 each |
| IO Pin `setup_slack_ns`, `hold_slack_ns` | Partial | Partial | Partial | Partial | 608/1,453 each |

These labels are attached for supervision but are never classified as stage input
features.

### 3.9 Edge-relation and edge-payload availability

The table uses PyG triplet notation (`source|relation|destination`). In metadata schema
keys, the three core logical relations are abbreviated as `gate→pin`, `pin→net`, and
`io_pin→net`; these denote the `has` or `connects_to` triplets shown below.

| Relation/payload | Floorplan | Placement | CTS | Route | Input/label boundary |
|---|---|---|---|---|---|
| `gate|has|pin`, 2-D `edge_attr` | Present | Present | Present | Present | Logical input relation. |
| `pin|connects_to|net`, 2-D `edge_attr` | Present | Present | Present | Present | Logical input relation. |
| `io_pin|connects_to|net`, 2-D `edge_attr` | Present | Present | Present | Present | Logical input relation. |
| `gate|congestion_geom|gate`, 1-D distance `edge_attr` | Absent | Absent | Present | Present | Optional congestion-model input; requires trusted standard-cell positions. |
| Timing-path relations, 0-D `edge_attr`, 2-D `edge_y` | Present for this sample | Present for this sample | Present for this sample | Present for this sample | Shared post-route supervision; generically Conditional on aligned report paths. |
| `net|rc_coupling|net`, 0-D `edge_attr`, 1-D `edge_y` | Present | Present | Present | Present | Shared post-route supervision; generically Conditional on aligned SPEF Cc pairs. |
| RC-resistance relations, 0-D `edge_attr`, 1-D `edge_y` | Present for this sample | Present for this sample | Present for this sample | Present for this sample | Shared post-route supervision; each endpoint-type combination is generically Conditional. |

Timing and RC relation indices are themselves post-route supervision structure. The
default input encoder excludes them; they are enabled only by the corresponding task
view. If a required endpoint node type or positive label row does not exist, the
relation is **Absent**, not represented by artificial mapping/query edges.

## 4. Canonical B-view topology

The Canonical topology is built only from the flattened post-Yosys Verilog netlist.
Backend-added cells and physical Net names do not create new B-view nodes.

```text
gate ──has──> pin ──connects_to──> net
io_pin ───────────────────────────> net

gate <──congestion_geom──> gate      optional physical input relation
pin/io_pin ──timing_path──> pin/io_pin   label-only relation
net <──rc_coupling──> net                label-only relation
pin/io_pin ──rc_resistance──> pin/io_pin label-only relation
```

Canonical entity keys are:

| Entity | Stable key |
|---|---|
| Gate | `inst_name` |
| Net | `net_name` |
| IO Pin | `iopin_name` |
| internal Pin | `(inst_name, pin_name)` |
| Gate-Pin incidence | `(inst_name, pin_name)` |
| Pin-Net incidence | `(inst_name, pin_name, net_name)` |
| IO Pin-Net incidence | `(iopin_name, net_name)` |

An IO-only Net remains a Net node even when it has no internal Gate-Pin incidence.

## 5. Graph-level features: 19 dimensions

| Index | Feature | Meaning |
|---:|---|---|
| 0 | `num_logical_cells` | Number of synthesized Canonical Gate nodes. |
| 1 | `num_logical_nets` | Number of Canonical Net nodes, including IO-only Nets. |
| 2 | `num_ios` | Number of top-level IO Pin nodes. |
| 3 | `avg_fanout` | Average logical Net fanout. |
| 4 | `die_width_um` | Current input snapshot die width in micrometres. |
| 5 | `die_height_um` | Current input snapshot die height in micrometres. |
| 6 | `die_area_um2` | Current input snapshot die area in square micrometres. |
| 7 | `dbu_per_um` | DEF database units per micrometre. |
| 8 | `place_density` | Configured placement density. |
| 9 | `place_density_is_default` | One when the configured density is a default rather than an explicit sample value. |
| 10 | `core_utilization` | Configured target core utilization. |
| 11 | `abc_area` | Synthesis/ABC area statistic from the design configuration. |
| 12 | `total_lib_pin_cap_fF` | Sum of Liberty Pin capacitances over Canonical internal Pins. |
| 13 | `v_nom` | Nominal supply voltage. |
| 14 | `freq_hz` | Design clock frequency in hertz. |
| 15 | `num_clocks` | Number of parsed SDC clocks. |
| 16 | `min_clock_period_ns` | Minimum parsed clock period. |
| 17 | `max_clock_period_ns` | Maximum parsed clock period. |
| 18 | `avg_clock_period_ns` | Average parsed clock period. |

Physical global fields are `NaN` when the current causal cutoff does not expose a DEF.

## 6. Gate nodes

### 6.1 Meaning

A `gate` node is a synthesized logical cell instance from `1_1_yosys.v`. A Gate remains
in all four B views even when the backend removes, replaces, or renames its physical
implementation.

### 6.2 Gate input features: 30 dimensions

| Feature | Meaning |
|---|---|
| `x_um`, `y_um` | DEF placement origin of the cell, in micrometres. |
| `center_x_um`, `center_y_um` | Cell centre after applying oriented LEF width/height. |
| `center_x_normalized`, `center_y_normalized` | Cell centre normalized to the die extent. |
| `cell_type_id` | Technology-specific master-cell ID from `configs/encode_map.csv`. |
| `cell_function_id` | Functional class ID: sequential, buffer, inverter, clock cell, constant, physical-only, and related classes. |
| `is_sequential_cell` | One for a flip-flop or latch. |
| `is_buffer_cell` | One for a buffer cell. |
| `is_inverter_cell` | One for an inverter cell. |
| `is_clock_buffer_cell` | One for a clock-buffer cell. |
| `is_clock_gate_cell` | One for a clock-gating cell. |
| `drive_strength` | Drive-strength value parsed from the master suffix when available. |
| `cell_area_um2` | LEF cell area in square micrometres. |
| `cell_leakage_power` | Raw Liberty cell leakage-power value; no dataset normalization is applied. |
| `clock_domain_id` | Encoded clock domain inferred from SDC and logical timing propagation. |
| `timing_forward_level` | Longest logical level from a timing startpoint to the Gate's Pins. |
| `timing_reverse_level` | Longest reverse logical level toward a timing endpoint. |
| `timing_level_valid` | One when a logical timing level is available. |
| `orientation_id` | Encoded DEF orientation. |
| `placement_status_id` | Encoded DEF placement status. |
| `placement_valid` | One when the stage permits this Gate coordinate and the coordinate exists. |
| `congestion_pin_density` | Mean Pin count over the 2.1 µm GCells overlapped by this Gate. |
| `congestion_cell_density` | Mean cell count over the Gate's overlapped GCells. |
| `congestion_net_density` | Mean number of physical stage Nets whose bounding boxes overlap those GCells. |
| `congestion_rudy` | Mean RUDY value using `(1/width + 1/height) × normalized overlap` per physical stage Net. |
| `congestion_rudy_pin` | Pin-weighted RUDY contribution over the Gate's GCells. |
| `congestion_feature_valid` | One when trusted standard-cell coordinates make the five congestion inputs available. |
| `graph_id` | Constant graph-membership placeholder, currently zero. |

The congestion grid is fixed by technology information known before routing:

```text
15 FastRoute tracks × 0.14 µm Metal3 pitch = 2.1 µm = 4200 DBU
```

### 6.3 Gate labels: 2 dimensions

| Label | Unit | Validity field | Meaning |
|---|---|---|---|
| `cell_congestion` | dimensionless | `congestion_valid` | Maximum horizontal/vertical routed demand-to-capacity ratio of the Gate's fixed 2.1 µm Route grid cell. |
| `ir_drop_mV` | mV | `irdrop_valid` | `1000 × (VDD source voltage − solved Gate VDD-node voltage)`. |

The corresponding mask is `gate.y_valid_mask = [congestion_valid, irdrop_valid]`.

## 7. Net nodes

### 7.1 Meaning

A `net` node is a Canonical synthesized Net. Placement, CTS, and routing may split one
Canonical Net into many physical stage Nets. Stable internal Pin keys and IO Pin names
align those stage Nets back to the original node; transparent backend BUF/INV chains are
included only when they have a unique Canonical owner.

### 7.2 Net input features: 28 dimensions

| Feature | Meaning |
|---|---|
| `net_type_id` | Encoded SIGNAL/CLOCK/RESET/SCAN/POWER/GROUND-like Net class. |
| `pin_count` | Number of Canonical internal and IO endpoints. |
| `fanout` | Number of logical sink endpoints. |
| `num_drivers` | Number of driver endpoints inferred from Liberty/IO direction. |
| `num_sinks` | Number of sink endpoints inferred from Liberty/IO direction. |
| `connects_macro_flag` | One when the Net connects to a large LEF macro or RAM-like master. |
| `is_clock_net` | One when the Net belongs to the inferred clock network. |
| `clock_domain_id` | Encoded unique clock domain, or `UNKNOWN` when ambiguous/unavailable. |
| `total_sink_cap_fF` | Sum of Liberty capacitance over sink Pins. |
| `net_bbox_width_um` | Width of the union of all aligned physical small-Net bounding boxes. |
| `net_bbox_height_um` | Height of the union of all aligned physical small-Net bounding boxes. |
| `hpwl_um` | Sum of the separately computed HPWL values of all aligned physical small Nets. It is not the HPWL of one large Canonical bounding box. |
| `stage_net_count` | Total number of physical stage Nets aligned to this Canonical Net. |
| `stage_direct_net_count` | Number of physical Nets found directly through stable Canonical endpoints. |
| `stage_inferred_backend_net_count` | Additional physical Nets reached through uniquely owned backend BUF/INV components. |
| `stage_net_split_flag` | One when the Canonical Net maps to more than one physical stage Net. |
| `stage_net_renamed_flag` | One when a one-to-one aligned stage Net has a different name. |
| `stage_net_anchor_count` | Number of Canonical endpoints found in the physical snapshot. |
| `stage_net_anchor_coverage` | Found Canonical endpoints divided by total Canonical endpoints. |
| `stage_net_alignment_valid` | One when all expected Canonical endpoints are aligned. |
| `stage_lineage_ambiguous_flag` | One when a backend-connected component is anchored to multiple Canonical Nets. |
| `stage_lineage_valid` | One when at least one stage Net is aligned with no ambiguous component. |
| `stage_segment_total_hpwl_um` | Sum of all aligned physical small-Net HPWL values; equal to `hpwl_um` when valid. |
| `stage_segment_max_hpwl_um` | Maximum HPWL among aligned physical small Nets. |
| `stage_segment_mean_hpwl_um` | Mean HPWL among aligned physical small Nets. |
| `hpwl_valid` | One when all aligned small-Net geometries and lineage are valid. |
| `stage_segment_hpwl_valid` | Explicit validity of the small-Net HPWL aggregation. |
| `graph_id` | Constant graph-membership placeholder, currently zero. |

HPWL is available only in CTS and Route prediction graphs because their inputs are
post-Placement and post-CTS respectively. Missing or ambiguous small-Net geometry yields
`NaN`; the builder never falls back to a large bounding box over only the base endpoints.

### 7.3 Net labels: 2 dimensions

| Label | Unit | Validity field | Meaning |
|---|---|---|---|
| `routed_wirelength_um` | µm | `wirelength_valid` | Sum of Manhattan routed segment lengths over every Route DEF physical Net aligned to the Canonical Net. |
| `ground_cap_pF` | pF | `ground_cap_valid` | Sum of SPEF ground capacitance over all aligned physical Nets. |

The corresponding mask is `net.y_valid_mask = [wirelength_valid,
ground_cap_valid]`.

## 8. IO Pin nodes

### 8.1 Meaning

An `io_pin` node represents a top-level Verilog port. Its stable key is the port name,
and it remains present even for an IO-only Net with no internal cell incidence.

### 8.2 IO Pin input features: 30 dimensions

| Feature | Meaning |
|---|---|
| `pin_x_um`, `pin_y_um` | Top-level DEF Pin location. |
| `pin_x_normalized`, `pin_y_normalized` | Pin location normalized to the die extent. |
| `distance_to_die_left_um`, `distance_to_die_right_um` | Horizontal distance to each die boundary. |
| `distance_to_die_bottom_um`, `distance_to_die_top_um` | Vertical distance to each die boundary. |
| `pin_position_valid` | One when the stage permits and provides a physical IO position. |
| `pin_direction_id` | Encoded INPUT/OUTPUT/INOUT direction. |
| `pin_role_id` | Encoded primary-input, primary-output, primary-inout, or clock-port role. |
| `is_clock_port` | One when an SDC clock is created on this port. |
| `is_driver_pin`, `is_sink_pin` | Logical driver/sink role after applying top-level IO direction semantics. |
| `is_timing_startpoint`, `is_timing_endpoint` | Logical timing endpoint classification. |
| `clock_domain_id` | Encoded associated clock domain. |
| `clock_period_ns` | Associated clock period. |
| `clock_uncertainty_ns` | Associated SDC clock uncertainty. |
| `clock_constraint_valid` | One when a clock constraint is available. |
| `input_delay_ns`, `output_delay_ns` | SDC IO-delay values. |
| `io_constraint_valid` | One when an input or output delay constraint is available. |
| `pin_layer_id` | Encoded physical Pin layer. |
| `timing_forward_level`, `timing_reverse_level` | Logical forward/reverse timing levels. |
| `timing_level_valid` | One when the timing levels are defined. |
| `net_type_id` | Encoded type of the connected Canonical Net. |
| `nearest_tap_distance_um` | Euclidean distance from the IO Pin to the nearest placed cell whose master name contains `TAP`. |
| `graph_id` | Constant graph-membership placeholder, currently zero. |

### 8.3 IO Pin labels: 2 dimensions

| Label | Unit | Validity field | Meaning |
|---|---|---|---|
| `setup_slack_ns` | ns | `setup_valid` | Post-route OpenSTA setup slack aligned to the IO port. |
| `hold_slack_ns` | ns | `hold_valid` | Post-route OpenSTA hold slack aligned to the IO port. |

The corresponding mask is `io_pin.y_valid_mask = [setup_valid, hold_valid]`.

## 9. Internal Pin nodes

### 9.1 Meaning

A `pin` node represents one named Pin owned by one Canonical Gate. Its identity is the
pair `(inst_name, pin_name)`, never `pin_name` alone.

### 9.2 Pin input features: 37 dimensions

| Feature | Meaning |
|---|---|
| `pin_type_id` | Encoded fine-grained Pin type such as data, clock, reset, select, output, power, or ground. |
| `pin_role_id` | Encoded modeling role such as DATA, Q, CLOCK, RESET, combinational input/output, or INOUT. |
| `pin_direction_id` | Encoded Liberty direction. |
| `pin_cap_fF` | Liberty input Pin capacitance. |
| `pin_max_transition_ns` | Liberty maximum transition constraint. |
| `pin_max_capacitance_fF` | Liberty maximum capacitance constraint. |
| `cell_type_id` | Encoded master type of the owning Gate. |
| `cell_function_id` | Encoded function class of the owning Gate. |
| `owner_drive_strength` | Parsed drive strength of the owning Gate. |
| `is_clock_pin`, `is_data_pin` | Clock/data functional flags. |
| `is_reset_pin`, `is_set_pin`, `is_enable_pin` | Control-Pin functional flags. |
| `is_sequential_pin`, `is_combinational_pin` | Owner/function classification flags. |
| `is_driver_pin`, `is_sink_pin` | Direction-derived logical role. |
| `is_timing_startpoint`, `is_timing_endpoint` | Logical timing endpoint classification. |
| `clock_domain_id` | Encoded associated clock domain. |
| `clock_period_ns`, `clock_uncertainty_ns` | Associated SDC clock values. |
| `clock_constraint_valid` | One when a clock constraint is available. |
| `timing_forward_level`, `timing_reverse_level` | Logical timing levels. |
| `timing_level_valid` | One when those levels are defined. |
| `pin_x_um`, `pin_y_um` | Physical Pin location computed from DEF cell placement plus oriented LEF Pin offset. |
| `pin_x_normalized`, `pin_y_normalized` | Pin location normalized to the die. |
| `distance_to_die_left_um`, `distance_to_die_right_um` | Horizontal distance to die boundaries. |
| `distance_to_die_bottom_um`, `distance_to_die_top_um` | Vertical distance to die boundaries. |
| `pin_position_valid` | One when the stage permits and provides the owner coordinate and LEF Pin geometry. |
| `graph_id` | Constant graph-membership placeholder, currently zero. |

### 9.3 Internal Pin labels: 2 dimensions

| Label | Unit | Validity field | Meaning |
|---|---|---|---|
| `setup_slack_ns` | ns | `setup_valid` | Post-route OpenSTA setup slack aligned by `(inst_name, pin_name)`. |
| `hold_slack_ns` | ns | `hold_valid` | Post-route OpenSTA hold slack aligned by `(inst_name, pin_name)`. |

The corresponding mask is `pin.y_valid_mask = [setup_valid, hold_valid]`.

## 10. Edge relations, features, and labels

### 10.1 `gate|has|pin`

- Meaning: directed ownership edge from a Canonical Gate to each internal Pin it owns.
- Cardinality: exactly one edge per internal Pin.
- Input features, 2 dimensions:
  - `cell_type_id`: encoded master type of the source Gate.
  - `pin_type_id`: encoded type of the target Pin.
- Labels: none.

### 10.2 `pin|connects_to|net`

- Meaning: directed incidence from an internal Pin to its Canonical Net.
- Cardinality: exactly one edge per internal Pin.
- Input features, 2 dimensions:
  - `pin_type_id`: encoded type of the source Pin.
  - `net_type_id`: encoded type of the target Net.
- Labels: none.

### 10.3 `io_pin|connects_to|net`

- Meaning: directed incidence from a top-level IO Pin to its Canonical Net.
- Cardinality: exactly one edge per connected IO Pin.
- Input features, 2 dimensions:
  - `pin_direction_id`: encoded top-level port direction.
  - `net_type_id`: encoded type of the target Net.
- Labels: none.

### 10.4 `gate|congestion_geom|gate`

- Meaning: optional same-grid physical neighborhood used by congestion models.
- Availability: only CTS and Route prediction graphs.
- Construction:
  - assign Gate placement origins to fixed 2.1 µm grid cells;
  - rank same-cell candidate pairs by Gate-centre Euclidean distance;
  - accept deterministic nearest pairs while both endpoint degrees remain below five;
  - represent every undirected pair as two symmetric PyG directed entries.
- Input features, 1 dimension:
  - `euclidean_distance_um`: centre-to-centre distance in micrometres.
- Labels: none.

Because grid membership uses the placement origin while distance uses the centre, the
distance can exceed 2.1 µm for cells with different widths. This relation is stored as
an independent edge type and is not merged into the logical incidence `edge_attr`.

### 10.5 Timing-path relations

Relations are created only for endpoint-type combinations that have actual aligned
OpenSTA path samples:

```text
pin|timing_path|pin
pin|timing_path|io_pin
io_pin|timing_path|pin
```

- Meaning: directed source-to-destination order from the OpenSTA path report.
- Input features: zero dimensions (`edge_attr.shape == [E, 0]`).
- Labels, 2 dimensions:
  - `setup_delay_ns`: setup/max-path delay sample.
  - `hold_delay_ns`: hold/min-path delay sample.
- Mask: `edge_y_mask` stores independent setup/hold validity.
- Missing node types or empty endpoint combinations do not create a relation store.

### 10.6 `net|rc_coupling|net`

- Meaning: positive extracted coupling-capacitance pair between two Canonical Nets.
- Physical semantics: undirected; stored as two symmetric directed entries.
- Input features: zero dimensions.
- Label, 1 dimension:
  - `coupling_cap_pF`: SPEF coupling capacitance in pF.
- Mask: `edge_y_mask[:, 0]`.
- Only observed positive pairs are stored; absent pairs are not negative examples.

### 10.7 RC-resistance relations

Possible endpoint combinations are:

```text
pin|rc_resistance|pin
pin|rc_resistance|io_pin
io_pin|rc_resistance|pin
```

- Meaning: directed effective resistance from the extracted driver endpoint to a sink
  endpoint on an aligned Canonical Net.
- Input features: zero dimensions.
- Label, 1 dimension:
  - `effective_resistance_ohm`: SPEF-derived effective path resistance in ohms.
- Mask: `edge_y_mask[:, 0]`.
- Missing endpoint types or empty combinations are omitted rather than supplemented by
  mapping/query files.

## 11. `bp_multi_top/v01` verified tensor sizes

### 11.1 Node counts

All four stages contain:

| Node type | Count |
|---|---:|
| Gate | 78,808 |
| Net | 96,588 |
| IO Pin | 1,453 |
| internal Pin | 293,452 |

### 11.2 Edge counts by stage

| Relation | Floorplan | Placement | CTS | Route |
|---|---:|---:|---:|---:|
| `gate|has|pin` | 293,452 | 293,452 | 293,452 | 293,452 |
| `pin|connects_to|net` | 293,452 | 293,452 | 293,452 | 293,452 |
| `io_pin|connects_to|net` | 1,453 | 1,453 | 1,453 | 1,453 |
| `pin|timing_path|pin` | 80,770 | 80,770 | 80,770 | 80,770 |
| `pin|timing_path|io_pin` | 608 | 608 | 608 | 608 |
| `io_pin|timing_path|pin` | 1,253 | 1,253 | 1,253 | 1,253 |
| `net|rc_coupling|net` | 1,214,344 | 1,214,344 | 1,214,344 | 1,214,344 |
| `pin|rc_resistance|pin` | 177,975 | 177,975 | 177,975 | 177,975 |
| `pin|rc_resistance|io_pin` | 139 | 139 | 139 | 139 |
| `io_pin|rc_resistance|pin` | 1 | 1 | 1 | 1 |
| `gate|congestion_geom|gate` | absent | absent | 69,896 | 69,730 |

The Gate-Gate counts are directed storage counts, corresponding to 34,948 CTS and
34,865 Route undirected pairs.

### 11.3 Valid label coverage

The same label values and masks are attached to all four stages.

| Store/label | Valid | Total |
|---|---:|---:|
| Gate congestion | 78,806 | 78,808 |
| Gate IR drop | 78,806 | 78,808 |
| Net routed wirelength | 96,588 | 96,588 |
| Net ground capacitance | 83,510 | 96,588 |
| internal Pin setup slack | 18,073 | 293,452 |
| internal Pin hold slack | 18,073 | 293,452 |
| IO Pin setup slack | 608 | 1,453 |
| IO Pin hold slack | 608 | 1,453 |
| Pin-Pin timing setup delay | 70,629 | 80,770 |
| Pin-Pin timing hold delay | 18,768 | 80,770 |
| Pin-IO timing setup/hold delay | 608 / 608 | 608 |
| IO-Pin timing setup/hold delay | 1,157 / 1,205 | 1,253 |
| Net-Net coupling capacitance | 1,214,344 | 1,214,344 |
| Pin-Pin effective resistance | 177,975 | 177,975 |
| Pin-IO effective resistance | 139 | 139 |
| IO-Pin effective resistance | 1 | 1 |

## 12. Missing-value and leakage rules

- Features unavailable at a prediction cutoff are `NaN`; they are not imputed with
  zero or filled from a later DEF.
- Feature validity is carried by explicit feature columns such as `placement_valid`,
  `pin_position_valid`, `hpwl_valid`, and `congestion_feature_valid`.
- Labels retain raw physical values with no normalization, clipping, filtering, or
  train/test selection.
- `y_valid_mask` and `edge_y_mask` describe source/alignment validity only.
- Route/post-route labels are never valid model inputs merely because they are attached
  to the graph object.
- The default message-passing relations are only the three logical incidence types.
- `gate|congestion_geom|gate` is an optional congestion-model input relation.
- Timing and RC relations are task-specific supervision relations and must be excluded
  from the input encoder unless a deliberate non-leaking training protocol says
  otherwise.

## 13. Loading and introspection

```python
import torch

graph = torch.load(
    "generated/bp_multi_top/v01/stages/cts/heterograph.pt",
    map_location="cpu",
    weights_only=False,
)

print(graph.node_types)
print(graph.edge_types)

print(graph["gate"].x.shape)
print(graph["gate"].x_schema)
print(graph["gate"].y.shape)
print(graph["gate"].y_schema)
print(graph["gate"].y_valid_mask.shape)

edge_type = ("gate", "congestion_geom", "gate")
print(graph[edge_type].edge_index.shape)
print(graph[edge_type].edge_attr.shape)
print(graph[edge_type].edge_schema)
```

Machine-readable schema and alignment sidecars are stored beside each graph:

```text
heterograph.metadata.json
heterograph.alignment.json
```

The full verified statistics are stored in:

```text
generated/bp_multi_top/v01/statistics/four_stage_data_statistics.json
generated/bp_multi_top/v01/statistics/four_stage_data_statistics.csv
generated/bp_multi_top/v01/statistics/four_stage_data_statistics.md
```

The current four-stage contract check and full tensor-statistics check both report
`PASS`.
