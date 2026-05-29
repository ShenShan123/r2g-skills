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
import re
import sys


DEFAULT_LAYER_INFO = {
    "metal1": {"pitch": 0.14, "direction": "HORIZONTAL"},
    "metal2": {"pitch": 0.19, "direction": "VERTICAL"},
    "metal3": {"pitch": 0.14, "direction": "HORIZONTAL"},
    "metal4": {"pitch": 0.28, "direction": "VERTICAL"},
    "metal5": {"pitch": 0.28, "direction": "HORIZONTAL"},
    "metal6": {"pitch": 0.28, "direction": "VERTICAL"},
    "metal7": {"pitch": 0.8, "direction": "HORIZONTAL"},
    "metal8": {"pitch": 0.8, "direction": "VERTICAL"},
    "metal9": {"pitch": 1.6, "direction": "HORIZONTAL"},
    "metal10": {"pitch": 1.6, "direction": "VERTICAL"},
}


def parse_tech_lef(tech_lef):
    """Parse routing-layer pitch/direction from a tech LEF.

    Recognizes any layer declared TYPE ROUTING (platform-agnostic — nangate
    metal*, sky130 met*/li1, asap7 M*). Falls back to the nangate
    DEFAULT_LAYER_INFO (with a warning) when the LEF is absent or declares no
    routing layers.
    """
    if not tech_lef or not os.path.exists(tech_lef):
        print(f"WARNING: tech LEF not found ({tech_lef}); using nangate45 DEFAULT_LAYER_INFO")
        return DEFAULT_LAYER_INFO

    layers = {}
    current = None
    block = {}

    def _finalize():
        if block.get("type") == "ROUTING" and block.get("pitch_vals") and block.get("direction"):
            pv = block["pitch_vals"]
            direction = block["direction"]
            if len(pv) >= 2:
                pitch = pv[1] if direction == "HORIZONTAL" else pv[0]
            else:
                pitch = pv[0]
            if pitch > 0:
                layers[current] = {"pitch": pitch, "direction": direction}

    with open(tech_lef, "r") as f:
        for raw_line in f:
            parts = raw_line.replace(";", " ").split()
            if not parts:
                continue
            if parts[0] == "LAYER" and len(parts) >= 2:
                current = parts[1]
                block = {"pitch_vals": [], "direction": None, "type": None}
                continue
            if current is None:
                continue
            if parts[0] == "END":
                _finalize()
                current = None
                block = {}
                continue
            if parts[0] == "TYPE" and len(parts) >= 2:
                block["type"] = parts[1].upper()
            elif parts[0] == "PITCH":
                for tok in parts[1:]:
                    try:
                        block["pitch_vals"].append(float(tok))
                    except ValueError:
                        pass
            elif parts[0] == "DIRECTION" and len(parts) >= 2:
                block["direction"] = parts[1].upper()

    if not layers:
        print("WARNING: no TYPE ROUTING layers parsed; using nangate45 DEFAULT_LAYER_INFO")
        return DEFAULT_LAYER_INFO
    return layers


def parse_def_header_and_components(def_file):
    db_units = 2000.0
    grid_step_x = 4200
    grid_step_y = 4200
    components = {}
    design_name = "unknown"

    with open(def_file, "r") as f:
        lines = f.readlines()

    for line in lines:
        line = line.strip()
        if line.startswith("DESIGN"):
            parts = line.split()
            if len(parts) >= 2:
                design_name = parts[1]
        elif line.startswith("UNITS DISTANCE MICRONS"):
            parts = line.split()
            if len(parts) >= 4:
                try:
                    db_units = float(parts[3])
                except ValueError:
                    pass
        elif line.startswith("GCELLGRID X"):
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

    in_components = False
    for line in lines:
        line = line.strip()
        if line.startswith("COMPONENTS"):
            in_components = True
            continue
        if line.startswith("END COMPONENTS"):
            break
        if not in_components or not line.startswith("- "):
            continue

        parts = line.split()
        if len(parts) < 3:
            continue
        name = parts[1]
        cell_type = parts[2]
        try:
            open_paren_idx = parts.index("(")
            x = int(parts[open_paren_idx + 1])
            y = int(parts[open_paren_idx + 2])
        except (ValueError, IndexError):
            continue
        components[name] = (x, y, cell_type)

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

        points = re.findall(r"\(\s*([^\s\)]+)\s+([^\s\)]+)(?:\s+[^\)]*)?\s*\)", line)
        if len(points) < 2:
            continue

        curr_x = None
        curr_y = None
        for x_str, y_str in points:
            if curr_x is None or curr_y is None:
                if x_str == "*" or y_str == "*":
                    continue
                try:
                    curr_x = int(x_str)
                    curr_y = int(y_str)
                except ValueError:
                    curr_x = None
                    curr_y = None
                continue

            next_x = curr_x
            next_y = curr_y
            if x_str != "*":
                try:
                    next_x = int(x_str)
                except ValueError:
                    continue
            if y_str != "*":
                try:
                    next_y = int(y_str)
                except ValueError:
                    continue

            add_route_segment(demand_h, demand_v, curr_x, curr_y, next_x, next_y, grid_step_x, grid_step_y, db_units)
            curr_x = next_x
            curr_y = next_y

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
    layer_info = parse_tech_lef(tech_lef)
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
