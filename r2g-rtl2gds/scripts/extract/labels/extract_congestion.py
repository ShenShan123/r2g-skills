#!/usr/bin/env python3
"""Per-cell routing-congestion label extractor.

Reads a routed DEF (6_final.def) plus a tech LEF, computes per-GCell routing
demand vs. capacity, smooths with a Gaussian neighborhood, and maps the result
to each placed instance. Output CSV: Design,Cell,cell_type,cell_congestion,label
where label = sqrt(cell_congestion).

Platform-agnostic: routing layers are detected by LEF `TYPE ROUTING` (works for
nangate metal*, sky130 met*/li1, asap7 M*); the nangate45 DEFAULT_LAYER_INFO is
only a logged last-resort fallback. See references/label-extraction.md.
"""
import csv
import math
import os
import sys

# sys.path bootstrap: make `import techlib.*` resolve when run via run_labels.sh
# (cwd is the project dir, not scripts/extract). Insert scripts/extract/ = the
# parent of this file's directory (labels/). Dup-guarded.
_EXTRACT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _EXTRACT_DIR not in sys.path:
    sys.path.insert(0, _EXTRACT_DIR)

from techlib.def_parse import (  # noqa: E402
    route_segments,
    parse_units,
    parse_design_name,
    parse_components,
)
from techlib.lef import routing_layer_info  # noqa: E402
from techlib import lef as _techlib_lef  # noqa: E402
from techlib.profile import get_profile  # noqa: E402


# DEFAULT_LAYER_INFO is the single nangate45 fallback table — now sourced from
# techlib (aliased from lef.DEFAULT_LAYER_INFO; same dict at import time). Retained as a
# module attribute because tests/test_techlib_lef.py and tests/test_techlib_profile.py
# assert equality against `extract_congestion.DEFAULT_LAYER_INFO`, and
# tests/test_extract_congestion.py references it directly.
DEFAULT_LAYER_INFO = _techlib_lef.DEFAULT_LAYER_INFO


def parse_tech_lef(tech_lef):
    """Parse routing-layer pitch/direction from a tech LEF.

    Thin compat wrapper over ``techlib.lef.routing_layer_info`` (which was ported
    verbatim from this function — Task 2 proved exact equality on all 6 platforms).
    Retained so tests/test_extract_congestion.py + tests/test_techlib_lef.py, which
    call ``extract_congestion.parse_tech_lef``, keep passing. The fallback table is
    the nangate45 ``DEFAULT_LAYER_INFO``.
    """
    return routing_layer_info(tech_lef, fallback=DEFAULT_LAYER_INFO)


def parse_def_header_and_components(def_file):
    # db_units / design_name / components are single-sourced from techlib (proven
    # byte-equivalent: see tests/test_techlib_crossplatform.py). The GCELLGRID X/Y
    # STEP scan stays LOCAL — it is congestion-specific and not part of def_parse.
    db_units = float(parse_units(def_file))
    design_name = parse_design_name(def_file)
    # parse_components -> {inst: {master,status,orient,x,y}} in DEF declaration order;
    # keep only placed comps (x is not None), matching congestion's old "needs ( x y )".
    components = {
        inst: (c["x"], c["y"], c["master"])
        for inst, c in parse_components(def_file).items()
        if c.get("x") is not None
    }

    grid_step_x = 4200
    grid_step_y = 4200
    with open(def_file, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("GCELLGRID X"):
                parts = line.split()
                try:
                    grid_step_x = int(parts[parts.index("STEP") + 1])
                except (ValueError, IndexError):
                    pass
            elif line.startswith("GCELLGRID Y"):
                parts = line.split()
                try:
                    grid_step_y = int(parts[parts.index("STEP") + 1])
                except (ValueError, IndexError):
                    pass
            elif line.startswith("COMPONENTS"):
                break

    return db_units, grid_step_x, grid_step_y, components, design_name


def add_split_segment(demand, fixed_coord, start, end, grid_step_main, grid_step_fixed, db_units):
    if start == end:
        return

    lo = min(start, end)
    hi = max(start, end)
    fixed_grid = fixed_coord // grid_step_fixed
    cur = lo

    while cur < hi:
        main_grid = cur // grid_step_main
        next_boundary = (main_grid + 1) * grid_step_main
        nxt = min(hi, next_boundary)
        length_um = (nxt - cur) / db_units
        if length_um > 0:
            demand[(main_grid, fixed_grid)] = demand.get((main_grid, fixed_grid), 0.0) + length_um
        cur = nxt


def add_route_segment(demand_h, demand_v, x1, y1, x2, y2, grid_step_x, grid_step_y, db_units):
    if x1 != x2:
        add_split_segment(demand_h, y1, x1, x2, grid_step_x, grid_step_y, db_units)
    if y1 != y2:
        add_split_segment(demand_v, x2, y1, y2, grid_step_y, grid_step_x, db_units)


def extract_grid_demand(def_file, db_units, grid_step_x, grid_step_y):
    demand_h = {}
    demand_v = {}

    with open(def_file, "r") as f:
        lines = f.readlines()

    in_nets = False
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("NETS") and not line.startswith("END NETS") and not line.startswith("SPECIALNETS"):
            in_nets = True
            continue
        if line.startswith("END NETS"):
            in_nets = False
            continue
        if not in_nets:
            continue
        if "ROUTED" not in line and "NEW" not in line and not line.startswith("+"):
            continue

        # techlib.route_segments reproduces the prior inline point-regex + *-relative
        # chain walk that fed add_route_segment (proven 0-mismatch on aes_core + cordic;
        # see tests/test_techlib_def_parse.py).
        for x1, y1, x2, y2 in route_segments(line):
            add_route_segment(demand_h, demand_v, x1, y1, x2, y2, grid_step_x, grid_step_y, db_units)

    return demand_h, demand_v


def calculate_grid_capacities(grid_step_x, grid_step_y, db_units, layer_info):
    grid_w_um = grid_step_x / db_units
    grid_h_um = grid_step_y / db_units
    cap_h = 0.0
    cap_v = 0.0

    for info in layer_info.values():
        pitch = info["pitch"]
        direction = info["direction"]
        if pitch <= 0:
            continue
        if direction == "HORIZONTAL":
            cap_h += grid_w_um * (grid_h_um / pitch)
        elif direction == "VERTICAL":
            cap_v += grid_h_um * (grid_w_um / pitch)

    return cap_h, cap_v


def build_grid_utilization(demand_h, demand_v, cap_h, cap_v):
    grid_util = {}
    all_grids = set(demand_h) | set(demand_v)
    for grid in all_grids:
        h_util = demand_h.get(grid, 0.0) / cap_h if cap_h > 0 else 0.0
        v_util = demand_v.get(grid, 0.0) / cap_v if cap_v > 0 else 0.0
        grid_util[grid] = max(h_util, v_util)
    return grid_util


def gaussian_cell_congestion(grid_util, grid_x, grid_y, radius=1, sigma=1.0):
    weighted_sum = 0.0
    weight_sum = 0.0
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            weight = math.exp(-((dx * dx + dy * dy) / (2.0 * sigma * sigma)))
            weighted_sum += weight * grid_util.get((grid_x + dx, grid_y + dy), 0.0)
            weight_sum += weight
    return weighted_sum / weight_sum if weight_sum > 0 else 0.0


def main():
    if len(sys.argv) < 2:
        def_file = os.path.join(os.path.dirname(__file__), "../6_final.def")
    else:
        def_file = sys.argv[1]

    output_csv = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), "cell_congestion.csv")
    design_name_override = sys.argv[3] if len(sys.argv) > 3 else None
    tech_lef = os.environ.get("TECH_LEF", os.path.join(os.path.dirname(__file__), "../NangateOpenCellLibrary.tech.lef"))

    if not os.path.exists(def_file):
        print(f"Error: DEF file not found at {def_file}")
        sys.exit(1)

    print(f"Processing {def_file}...")
    # Single-source the routing-layer parse + fallback through techlib. All platforms
    # share the same nangate45 fallback table (Task 5), so the platform arg is
    # non-critical here, but pass it to honor the single-source intent.
    layer_info = routing_layer_info(
        tech_lef,
        fallback=get_profile(os.environ.get("R2G_PLATFORM", "nangate45")).fallback_routing_layers,
    )
    db_units, grid_step_x, grid_step_y, components, design_name = parse_def_header_and_components(def_file)
    if design_name_override:
        design_name = design_name_override

    print(f"Design: {design_name}, DB Units: {db_units}, GCell: {grid_step_x} x {grid_step_y} DBU")
    print(f"Found {len(components)} components and {len(layer_info)} routing layers.")

    cap_h, cap_v = calculate_grid_capacities(grid_step_x, grid_step_y, db_units, layer_info)
    print(f"Grid capacity H/V: {cap_h:.4f} / {cap_v:.4f} um")

    print("Extracting routed wire demand...")
    demand_h, demand_v = extract_grid_demand(def_file, db_units, grid_step_x, grid_step_y)
    grid_util = build_grid_utilization(demand_h, demand_v, cap_h, cap_v)
    print(f"Processed utilization for {len(grid_util)} grids.")

    print("Mapping congestion to cells...")
    with open(output_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Design", "Cell", "cell_type", "cell_congestion", "label"])

        count = 0
        for cell_name, (x, y, cell_type) in components.items():
            grid_x = x // grid_step_x
            grid_y = y // grid_step_y
            cell_congestion = gaussian_cell_congestion(grid_util, grid_x, grid_y)
            label = math.sqrt(cell_congestion)
            writer.writerow([design_name, cell_name, cell_type, f"{cell_congestion:.9f}", f"{label:.9f}"])
            count += 1

    print(f"Successfully wrote {count} rows to {output_csv}")


if __name__ == "__main__":
    main()
