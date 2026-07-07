#!/usr/bin/env python3
"""Per-cell routing-congestion label extractor.

Reads a routed DEF (``6_final.def``) plus a tech LEF and the standard-cell/macro
LEF(s), computes per-GCell routing demand vs. capacity, smooths the utilization
grid with a Gaussian filter, and maps the result to each placed instance over the
**whole GCell footprint of the cell's (orientation-aware) bounding box**. This is a
faithful port of ``RTL2Graph/label_test/py/Congestion_Parse.py``'s label method
(the 2026-07-06 update). Output CSV columns:

    Design, Cell, cell_type, cell_congestion, label, label_raw

where, averaged over every GCell the cell's bounding box overlaps,
    cell_congestion = mean(gaussian_util)         # smoothed utilization
    label           = mean(sqrt(gaussian_util))   # smoothed target  (== ref node_label[1])
    label_raw       = mean(sqrt(util))            # raw    target  (== ref node_label[0])

`label` (the smoothed sqrt) stays the canonical training target every consumer
reads (graph_lib gate y1, compute_label_stats, the RTL2Graph augmenters); the raw
`label_raw` is surfaced alongside so no information from the new 2-vector method is
lost.

Method notes vs. the old (origin-GCell) extractor:
  * Gaussian is scipy's ``gaussian_filter(util, sigma=1.0)`` — a separable 9-tap
    (radius=int(4*sigma+0.5)=4) reflect-boundary convolution — reproduced HERE in
    pure Python (``gaussian_filter_2d``) so the label stage keeps NO numpy/scipy
    runtime dep (bit-matched to scipy to <1e-12; see tests). The old manual 3x3
    (radius=1) kernel is retired.
  * Each cell is mapped over its bounding box (needs cell ``SIZE`` from the cell
    LEF, passed via ``SC_LEF``/``CELL_LEFS``/``ADDITIONAL_LEFS``), not just the
    origin GCell. With no cell LEF available every cell falls back to its origin
    GCell (logged) so the extractor still runs.

Platform-agnostic: routing layers are detected by LEF ``TYPE ROUTING`` (nangate
metal*, sky130 met*/li1, asap7 M*); the nangate45 fallback table only fires when
the tech LEF yields no routing layers. See references/label-extraction.md.
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
from techlib.lef import routing_layer_info, merge_macro_sizes  # noqa: E402
from techlib.profile import get_profile  # noqa: E402


CANONICAL_OUTPUT_NAME = "cell_congestion.csv"


def _force_canonical(requested_path):
    import os as _os
    if requested_path:
        out_dir = _os.path.dirname(_os.path.abspath(requested_path))
    else:
        out_dir = _os.path.dirname(_os.path.abspath(__file__))
    forced = _os.path.join(out_dir, CANONICAL_OUTPUT_NAME)
    if requested_path and _os.path.basename(requested_path) != CANONICAL_OUTPUT_NAME:
        print(f"Note: forcing canonical output name -> {forced} "
              f"(requested basename was '{_os.path.basename(requested_path)}')")
    return forced


# The routing-layer parse + nangate45 fallback table are single-sourced from
# ``techlib.lef`` (``routing_layer_info`` / ``lef.DEFAULT_LAYER_INFO``); main() calls
# routing_layer_info directly with the platform-aware profile fallback.


def parse_def_header_and_components(def_file):
    # db_units / design_name / components are single-sourced from techlib (proven
    # byte-equivalent: see tests/test_techlib_crossplatform.py). The GCELLGRID X/Y
    # STEP scan + DIEAREA stay LOCAL — congestion-specific, not part of def_parse.
    db_units = float(parse_units(def_file))
    design_name = parse_design_name(def_file)
    # parse_components -> {inst: {master,status,orient,x,y}} in DEF declaration order;
    # keep placed comps (x is not None). orient is needed for the bbox mapping.
    components = {
        inst: (c["x"], c["y"], c["master"], c.get("orient") or "N")
        for inst, c in parse_components(def_file).items()
        if c.get("x") is not None
    }

    grid_step_x = 4200
    grid_step_y = 4200
    die = None
    with open(def_file, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("DIEAREA"):
                # "DIEAREA ( x y ) ( x y ) [...] ;" — take the bounding rectangle of
                # every point (handles rectilinear die outlines, not just 2 points).
                toks = line.replace("(", " ").replace(")", " ").replace(";", " ").split()
                nums = []
                for t in toks[1:]:
                    try:
                        nums.append(int(t))
                    except ValueError:
                        pass
                if len(nums) >= 4:
                    xs = nums[0::2]
                    ys = nums[1::2]
                    die = (min(xs), min(ys), max(xs), max(ys))
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

    return db_units, grid_step_x, grid_step_y, die, components, design_name


# --------------------------------------------------------------------------- #
# Routed-wire demand grid (transpose-correct; keys are always (x_gcell,y_gcell))
# --------------------------------------------------------------------------- #
def add_split_segment(demand, fixed_coord, start, end, grid_step_main, grid_step_fixed, db_units,
                      vertical=False):
    """Accumulate one axis-parallel wire into the per-gcell demand grid.

    Every demand key MUST be (x_gcell, y_gcell) — the same convention
    build_grid_utilization and the cell mapper use. For a vertical wire the
    MAIN (walked) axis is y and the FIXED axis is x, so the key order flips
    (``vertical=True``). Keying vertical demand (main, fixed) = (y, x) was the
    2026-07-05 transposition bug: every cell's v_util was read from its
    diagonal-mirror gcell (~80% of aes_core congestion labels wrong; see
    failure-patterns.md "Dataset-Extraction Silent-Value Defects" #7)."""
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
            key = (fixed_grid, main_grid) if vertical else (main_grid, fixed_grid)
            demand[key] = demand.get(key, 0.0) + length_um
        cur = nxt


def add_route_segment(demand_h, demand_v, x1, y1, x2, y2, grid_step_x, grid_step_y, db_units):
    if x1 != x2:
        add_split_segment(demand_h, y1, x1, x2, grid_step_x, grid_step_y, db_units)
    if y1 != y2:
        add_split_segment(demand_v, x2, y1, y2, grid_step_y, grid_step_x, db_units,
                          vertical=True)


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

        # techlib.route_segments reproduces the point-regex + *-relative chain walk
        # (proven 0-mismatch on aes_core + cordic; tests/test_techlib_def_parse.py).
        # It is a strict superset of Congestion_Parse's len==2-only handling — on real
        # ORFS DEFs routing is emitted 2 points per line, so the two agree exactly.
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


# --------------------------------------------------------------------------- #
# Dense utilization grid + Gaussian smoothing                                  #
#                                                                              #
# gaussian_filter_2d reproduces scipy.ndimage.gaussian_filter(a, sigma=1.0)    #
# with its defaults (order=0, mode='reflect', cval=0.0, truncate=4.0), so the  #
# label stage needs NO numpy/scipy at runtime. Bit-matched to scipy to <1e-12  #
# (tests/test_extract_congestion.py::test_gaussian_matches_scipy).             #
# --------------------------------------------------------------------------- #
def densify_util(grid_util, gridxcnt, gridycnt):
    """Project the sparse (x_gcell,y_gcell)->util dict onto a dense gridxcnt x
    gridycnt list-of-lists (zeros where there is no routed demand). The dense
    array is what the Gaussian filter (which spreads into zero cells and reflects
    at the die edges) operates on."""
    dense = [[0.0] * gridycnt for _ in range(gridxcnt)]
    for (gx, gy), u in grid_util.items():
        if 0 <= gx < gridxcnt and 0 <= gy < gridycnt:
            dense[gx][gy] = u
    return dense


def _gaussian_weights(sigma=1.0, truncate=4.0):
    radius = int(truncate * sigma + 0.5)           # scipy: int(4.5) = 4 for sigma=1
    sigma2 = sigma * sigma
    phi = [math.exp(-0.5 / sigma2 * (x * x)) for x in range(-radius, radius + 1)]
    s = sum(phi)
    return [p / s for p in phi], radius


def _reflect_index(p, n):
    # scipy 'reflect' mode: (d c b a | a b c d | d c b a) — edge sample duplicated.
    if n == 1:
        return 0
    period = 2 * n
    r = p % period
    if r < 0:
        r += period
    if r >= n:
        r = period - 1 - r
    return r


def gaussian_filter_2d(grid, gridxcnt, gridycnt, sigma=1.0, truncate=4.0):
    """Pure-Python equivalent of scipy.ndimage.gaussian_filter(grid, sigma).

    Separable correlation: axis 0 (x) first, then axis 1 (y) — matching scipy's
    axis order — each a 1-D reflect-boundary correlation with the normalized
    Gaussian kernel. Input/return are dense gridxcnt x gridycnt lists of float."""
    w, radius = _gaussian_weights(sigma, truncate)

    # axis 0 (x): for each column y, correlate down the x index
    tmp = [[0.0] * gridycnt for _ in range(gridxcnt)]
    for y in range(gridycnt):
        for x in range(gridxcnt):
            acc = 0.0
            for k, wk in enumerate(w):
                xi = _reflect_index(x + (k - radius), gridxcnt)
                acc += wk * grid[xi][y]
            tmp[x][y] = acc

    # axis 1 (y): for each row x, correlate along the y index
    out = [[0.0] * gridycnt for _ in range(gridxcnt)]
    for x in range(gridxcnt):
        row = tmp[x]
        orow = out[x]
        for y in range(gridycnt):
            acc = 0.0
            for k, wk in enumerate(w):
                yi = _reflect_index(y + (k - radius), gridycnt)
                acc += wk * row[yi]
            orow[y] = acc

    return out


# --------------------------------------------------------------------------- #
# Cell -> bounding-box GCell mapping                                           #
# --------------------------------------------------------------------------- #
def cell_bbox_dbu(x, y, master, orient, sizes, db_units):
    """Orientation-aware placement bounding box in DBU, or None if the master's
    SIZE is unknown (caller then falls back to the single origin GCell).

    Mirrors Congestion_Parse.instance_direction_rect: N/S/FN/FS keep (w,h);
    E/W/FE/FW are rotated 90 deg so the footprint (w,h) swaps."""
    wh = sizes.get(master)
    if wh is None:
        return None
    w = wh[0] * db_units
    h = wh[1] * db_units
    o = orient or "N"
    if "N" in o or "S" in o:
        bw, bh = w, h
    elif "W" in o or "E" in o:
        bw, bh = h, w
    else:
        bw, bh = w, h
    return (x, y, x + bw, y + bh)


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def cell_congestion_over_bbox(bbox, ox, oy, util_dense, gauss_dense,
                              grid_step_x, grid_step_y, gridxcnt, gridycnt):
    """Average (gaussian_util, sqrt(gaussian_util), sqrt(util)) over every GCell
    the cell's bbox overlaps. Falls back to the single origin GCell (ox,oy) when
    bbox is None (unknown master size). Returns (cell_congestion, label, label_raw)."""
    if bbox is None:
        gx0 = gx1 = _clamp(ox // grid_step_x, 0, gridxcnt - 1)
        gy0 = gy1 = _clamp(oy // grid_step_y, 0, gridycnt - 1)
    else:
        bx0, by0, bx1, by1 = bbox
        gx0 = _clamp(int(bx0) // grid_step_x, 0, gridxcnt - 1)
        gx1 = _clamp(int(bx1) // grid_step_x, 0, gridxcnt - 1)
        gy0 = _clamp(int(by0) // grid_step_y, 0, gridycnt - 1)
        gy1 = _clamp(int(by1) // grid_step_y, 0, gridycnt - 1)

    sum_g = sum_sg = sum_su = 0.0
    count = 0
    for gx in range(gx0, gx1 + 1):
        col_u = util_dense[gx]
        col_g = gauss_dense[gx]
        for gy in range(gy0, gy1 + 1):
            u = col_u[gy]
            g = col_g[gy]
            sum_g += g
            sum_sg += math.sqrt(g) if g > 0 else 0.0
            sum_su += math.sqrt(u) if u > 0 else 0.0
            count += 1

    if count == 0:
        return 0.0, 0.0, 0.0
    return sum_g / count, sum_sg / count, sum_su / count


def _cell_lef_paths():
    """Cell/macro LEF paths (with per-MACRO SIZE) from the environment.

    run_labels.sh exports SC_LEF (standard-cell LEF) and ADDITIONAL_LEFS (macro
    LEFs) straight from resolve_platform_paths.sh; CELL_LEFS is an explicit
    override. Whitespace-separated; existence-checked by merge_macro_sizes."""
    paths = []
    for var in ("SC_LEF", "CELL_LEFS", "ADDITIONAL_LEFS"):
        for tok in os.environ.get(var, "").split():
            if tok and tok not in paths:
                paths.append(tok)
    return paths


def main():
    if len(sys.argv) < 2:
        def_file = os.path.join(os.path.dirname(__file__), "../6_final.def")
    else:
        def_file = sys.argv[1]

    output_csv = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), CANONICAL_OUTPUT_NAME)
    output_csv = _force_canonical(output_csv)
    design_name_override = sys.argv[3] if len(sys.argv) > 3 else None
    tech_lef = os.environ.get("TECH_LEF", os.path.join(os.path.dirname(__file__), "../NangateOpenCellLibrary.tech.lef"))

    if not os.path.exists(def_file):
        print(f"Error: DEF file not found at {def_file}")
        sys.exit(1)

    print(f"Processing {def_file}...")
    # Single-source the routing-layer parse + fallback through techlib. All platforms
    # share the same nangate45 fallback table, so the platform arg is non-critical,
    # but pass it to honor the single-source intent.
    layer_info = routing_layer_info(
        tech_lef,
        fallback=get_profile(os.environ.get("R2G_PLATFORM", "asap7")).fallback_routing_layers,
    )
    db_units, grid_step_x, grid_step_y, die, components, design_name = parse_def_header_and_components(def_file)
    if design_name_override:
        design_name = design_name_override

    # Cell footprints for the bbox mapping (graceful fallback when absent).
    cell_lefs = _cell_lef_paths()
    sizes = merge_macro_sizes(cell_lefs)
    if not sizes:
        print("WARNING: no cell SIZE data (SC_LEF/CELL_LEFS/ADDITIONAL_LEFS unset or "
              "unparseable); mapping every cell to its origin GCell only.")

    # Dense grid dimensions from the die outline (fallback: span of routed demand).
    if die is not None:
        die_left, die_bottom, die_right, die_top = die
        gridxcnt = max(1, math.ceil((die_right - die_left) / grid_step_x))
        gridycnt = max(1, math.ceil((die_top - die_bottom) / grid_step_y))
    else:
        gridxcnt = gridycnt = None  # set after demand extraction

    print(f"Design: {design_name}, DB Units: {db_units}, GCell: {grid_step_x} x {grid_step_y} DBU")
    print(f"Found {len(components)} components, {len(layer_info)} routing layers, "
          f"{len(sizes)} cell sizes.")

    cap_h, cap_v = calculate_grid_capacities(grid_step_x, grid_step_y, db_units, layer_info)
    print(f"Grid capacity H/V: {cap_h:.4f} / {cap_v:.4f} um")

    print("Extracting routed wire demand...")
    demand_h, demand_v = extract_grid_demand(def_file, db_units, grid_step_x, grid_step_y)
    grid_util = build_grid_utilization(demand_h, demand_v, cap_h, cap_v)

    if gridxcnt is None:
        # No DIEAREA: size the grid to cover all demand + all placements.
        max_gx = 0
        max_gy = 0
        for (gx, gy) in grid_util:
            max_gx = max(max_gx, gx)
            max_gy = max(max_gy, gy)
        for (x, y, _m, _o) in components.values():
            max_gx = max(max_gx, x // grid_step_x)
            max_gy = max(max_gy, y // grid_step_y)
        gridxcnt = max_gx + 1
        gridycnt = max_gy + 1

    print(f"Utilization grid: {gridxcnt} x {gridycnt} GCells "
          f"({len(grid_util)} with routed demand).")

    util_dense = densify_util(grid_util, gridxcnt, gridycnt)
    gauss_dense = gaussian_filter_2d(util_dense, gridxcnt, gridycnt, sigma=1.0)

    print("Mapping congestion to cells...")
    with open(output_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Design", "Cell", "cell_type", "cell_congestion", "label", "label_raw"])

        count = 0
        fallback = 0
        for cell_name, (x, y, cell_type, orient) in components.items():
            bbox = cell_bbox_dbu(x, y, cell_type, orient, sizes, db_units)
            if bbox is None:
                fallback += 1
            cong, label, label_raw = cell_congestion_over_bbox(
                bbox, x, y, util_dense, gauss_dense,
                grid_step_x, grid_step_y, gridxcnt, gridycnt)
            writer.writerow([design_name, cell_name, cell_type,
                             f"{cong:.9f}", f"{label:.9f}", f"{label_raw:.9f}"])
            count += 1

    if fallback:
        print(f"Note: {fallback}/{count} cells used origin-GCell fallback "
              f"(master SIZE not found in the supplied LEFs).")
    print(f"Successfully wrote {count} rows to {output_csv}")


if __name__ == "__main__":
    main()
