# RTL2Graph_v3 reference alignment (def-graph) — 2026-07-14

**Trigger.** A fresh `RTL2Graph_v3/` reference drop ("all scripts updated after debugging"). Task: compare
to the `def-graph` skill, align ours to the reference's genuine improvements, and report any bugs found there.

**Method.** Subsystem-by-subsystem comparison (base_garph netlist graph, feature_test_v4 nodes/edges +
metadata/lib_db, label_test, last_graph b–f augment, odb2def) with a hard rule: distinguish a *debugged bug
fix* from our *intentional multi-platform superset*.

## Finding: the reference is BEHIND ours on correctness

`def-graph` forked from an earlier RTL2Graph and then fixed a long series of silent-value defects (2026-07).
`RTL2Graph_v3`'s debugging did **not** absorb those, so blindly "aligning ours to the reference" would
regress us. Confirmed reference bugs (reported to the user; NOT ported — see failure-patterns.md #47):
congestion vertical-demand transpose, wirelength/congestion RECT-patch not stripped, timing STA↔ODB name not
de-escaped, and c/d/e/f `build_directed_edges` edge-attr misalignment (our "bug #5"). `odb2def` is identical;
`netlist_graph` is a superset (ours adds a sized-constant strip). Node order/sort/filtering, timing→pin,
y-slot order, metadata/global_feat[12], sum_pin_cap all equivalent or intentional superset.

## Adopted (user-approved): three changes

Decisions taken via AskUserQuestion:
- **Label convention** → keep BOTH raw and transformed as parallel columns/tensors (neither dropped).
- **Improvements** → adopt num_drivers force-fill removal AND LEF pin-center geometry.
- **Reference** → report bugs only; do NOT edit `RTL2Graph_v3`.

1. **Raw-label twins.** `data.y_raw` / `edge_y_raw` / `rc_edge_y_raw` mirror the normalized tensors slot-for-
   slot with the raw physical value (EDA-Schema/CircuitNet: demand/cap ratio, mV, ns slack [signed], um, fF,
   Ohm). Sourced from label-CSV raw columns that already existed; `graph_lib.LABEL_SPECS` gained a
   `raw_column`, the builders a `value_col_key`, `build_directed_edges`/`build_parasitic_edges`/
   `attach_rc_labels` a raw pass. `build_graphs` sets the twins + a `y_raw_schema`.
2. **`num_drivers` no-fill.** `nodes_net.py` dropped `num_drivers==0 → 1` (which also overwrote `num_sinks`).
   Matches the reference ("不再强制补值") and the verifier's own no-fill recompute. Verifier `>= 1 on ALL nets`
   assert relaxed to `>= 1 on SOME net`.
3. **LEF pin geometry.** `techlib.lef.macro_pin_geometry` + `apply_orient` + `pin_abs_pos_um`; wired through
   `liberty.get_pin_abs_pos_um(…, geom=)` into `nodes_pin`/`nodes_net`. `run_features.sh` now exports
   `SC_LEF`/`ADDITIONAL_LEFS`. Empty ⇒ instance-origin fallback.

## Verification

- Regenerated `cordic` (sky130hs) end-to-end: labels 7/7, all 5 views + netlist_graph, `y_nan_frac ==
  y_raw_nan_frac` on every slot, RC populated (35k edges). Pin geometry activated (390 sky130hs macros parsed).
- `tools/verify_graph_dataset.py design_cases/cordic` → **204/204** including the new raw-tensor checks (shape,
  NaN-parity, `log1p` identities, independent SPEF-oracle raw ground/coupling) and pin-center HPWL.
- def-graph pytest: **395 passed, 14 skipped** (added `test_lef_pin_geometry.py`, raw-twin + no-fill tests).

## Post-review fixes (/code-review xhigh, 23 agents)

The review caught a **serious inherited bug** plus 8 coverage/robustness gaps:

- **`apply_orient` FN/FS swap** — the port carried the RTL2Graph original's transposed FN/FS (FN returned MX,
  FS returned MY). FS is the alternating-row flip (~half of all std cells; cordic 2488/5105), so
  `hpwl_um`/`pin_x/y_std_um` were wrong for every net touching a flipped cell — and the verifier's
  `_v_apply_orient` + the unit test replicated the identical swap, so the build verified green anyway. Fixed
  (swap FN↔FS), then **validated against OpenDB placed pin locations** (cordic FS=MX 2488/2488). Firewall
  lesson recorded in failure-patterns #47.
- **Timing raw INF** — raw twin read `Cell_Slack_ns` (`"INF"` off-path → `+inf`); switched to `Path_Delay_ns`
  (finite, clean `y[:,3]==log1p(y_raw[:,3])` identity). `Cell_Slack_ns` stays a CSV-only column.
- Verifier hardening: guard the raw SPEF block (no crash on pre-raw-twin corpora); timing/wirelength edge
  `log1p` identities; `edge_y_raw`/`rc_edge_y_raw` added to the interleave oracle; `SC_LEF` whitespace-split +
  `CELL_LEFS`; a `num_drivers==0` no-fill honesty check (targets 0-driver nets past the 200-net cap); POLYGON
  MASK off-by-one.

Re-validated: cordic force-regen → verifier **212/212**, pytest **395 passed / 14 skipped**, OpenDB
orientation oracle **5105/5105**.

## Superseded invariants

- CLAUDE.md def-graph ⭐ "Tensor schema": now every label tensor has a RAW twin (`y_raw`/`edge_y_raw`/
  `rc_edge_y_raw`) with NaN-parity + clean-transform identities.
- `hpwl_um` / `pin_x/y_std_um` are no longer cell-origin approximations when a cell LEF resolves.
- `num_drivers` may legitimately be 0 (was: force-filled to ≥1).
