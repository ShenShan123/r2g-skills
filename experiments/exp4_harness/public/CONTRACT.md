# Physical graph conversion contract

The task is final-layout B–F graph extraction only. Required files are five
HeteroData objects named b_graph.pt through f_graph.pt and five Data objects named
b_graph_homo.pt through f_graph_homo.pt, inside OUTPUT_DIR/dataset/.
The format, feature definitions, units, labels, filtering and graph reduction
rules are in SEMANTICS.md, graph-dataset.md, feature-extraction.md and
label-extraction.md. SEMANTICS.md is normative if the narrative documents differ.
Both forms must represent the same physical graph. Preserve node identities,
parallel edges, reverse edges, raw/log1p twins and documented NaN masks.

Only nonpower quantities are evaluated: connectivity and nonpower features,
wirelength, congestion, timing, ground capacitance, coupling capacitance and
resistance. Power features and IR drop values are excluded; preserve their
documented tensor slots. Do not create images, netlist graphs, or four-stage
graphs for this experiment.

Inputs are already complete: 6_final.def, 6_final.spef, 6_final.sdc, 6_final.v,
constraint.sdc, config.mk. Use final DEF and final SDC for geometry and timing;
the timing definition uses Liberty + final SDC without reading SPEF into STA.
SPEF supplies the RC quantities. Do not rerun synthesis or place-and-route.
Technology libraries are supplied separately and also installed with OpenROAD.

Numerical tolerances (absolute + relative * |truth|), in documented raw units:
wirelength 2e-4 + 1e-6; congestion 1e-6 + 1e-5; timing 1e-4 + 1e-6;
ground/coupling capacitance 1e-5 + 1e-5; resistance 1e-4 + 1e-5.
Full design acceptance also requires all graph features, topology and masks.
For label accuracy, missing finite reference values count as incorrect. The
six physical label families are averaged equally within each design; designs
are averaged equally. Copies across graph views/forms do not add weight.

The public checker checks representation, not physical correctness. It contains
no reference answers. Use it alongside your own physical checks on development
inputs. No hidden reference results will be supplied during development.
