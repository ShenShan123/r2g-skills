"""Persist the per-GCell grids the congestion label already computes.

`extract_congestion` reads the DEF's GCELLGRID, accumulates routed-wire demand
per GCell, divides by capacity, smooths with a radius-4 Gaussian -- and then
averages the result over each cell's bbox and throws the grid away. That
discarded array IS the image representation: emitting it costs one more file,
not a second extraction, and it keeps a single source of truth for the demand
arithmetic (a worker-local re-parse is how congestion and wirelength once
drifted apart).

Index convention, stated once and carried in the sidecar: arrays are [x][y] with
shape (num_gcells_x, num_gcells_y). That is CircuitNet's own convention --
`np.zeros(gcell_size)` with `gcell_size = [x_count, y_count]` and
`density[j, k] += 1` where j indexes x (feature_extraction/src/read.py) -- so a
grid emitted here loads into that ecosystem without a transpose. Matching it was
not free of risk: keying this grid (y, x) is precisely the 2026-07-05 defect that
made ~79.7% of congestion labels wrong on every platform while every shape and
summary statistic still looked plausible.

The file is TRIPLES, not a dense matrix, for that reason. A dense dump is
byte-identical under transposition and only the reader's assumption decides
which it was; a row that names its own x and y cannot be misread. Only cells
with a non-zero value are written -- the Gaussian leaves most of a sparse die at
exactly zero -- and the sidecar carries the dimensions so a consumer allocates
zeros and fills.
"""
import csv
import json
import os

GRID_CSV_NAME = "congestion_grid.csv"
GRID_JSON_NAME = "congestion_grid.json"
COLUMNS = ["x_gcell", "y_gcell", "util", "gaussian_util"]


def write_grid_csv(path, util_dense, gauss_dense, gridxcnt, gridycnt):
    """Write non-zero GCells as (x, y, util, gaussian_util) rows. Returns the count."""
    tmp = path + ".tmp"
    n = 0
    with open(tmp, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for x in range(gridxcnt):
            col_u = util_dense[x]
            col_g = gauss_dense[x]
            for y in range(gridycnt):
                u, g = col_u[y], col_g[y]
                if u == 0.0 and g == 0.0:
                    continue
                w.writerow([x, y, f"{u:.9f}", f"{g:.9f}"])
                n += 1
    # atomic: a half-written grid read as complete is the irdrop incident's shape
    os.replace(tmp, path)
    return n


def read_grid_csv(path, gridxcnt, gridycnt):
    """Rebuild dense [x][y] arrays. Raises ValueError when a coordinate does not
    fit the declared dimensions -- which is what a transposed read looks like."""
    util = [[0.0] * gridycnt for _ in range(gridxcnt)]
    gauss = [[0.0] * gridycnt for _ in range(gridxcnt)]
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            x, y = int(row["x_gcell"]), int(row["y_gcell"])
            if not (0 <= x < gridxcnt and 0 <= y < gridycnt):
                raise ValueError(
                    f"gcell ({x},{y}) out of range for grid "
                    f"{gridxcnt}x{gridycnt} — axes swapped?")
            util[x][y] = float(row["util"])
            gauss[x][y] = float(row["gaussian_util"])
    return {"util": util, "gaussian_util": gauss}


def write_grid_sidecar(path, *, design, gridxcnt, gridycnt, grid_step_x,
                       grid_step_y, db_units, die, cap_h, cap_v, nonzero):
    """Dimensions + the index convention travel WITH the data, so a consumer
    never has to infer the axis order."""
    tmp = path + ".tmp"
    meta = {
        "design": design,
        "gcell_size": [gridxcnt, gridycnt],
        "index_order": "xy",
        "convention": ("arrays are [x][y] with shape (num_gcells_x, "
                       "num_gcells_y), matching CircuitNet's "
                       "feature_extraction/src/read.py"),
        "grid_step_dbu": [grid_step_x, grid_step_y],
        "db_units": db_units,
        "die_dbu": list(die) if die else None,
        "capacity_um": {"h": cap_h, "v": cap_v},
        "nonzero_gcells": nonzero,
        "channels": {
            "util": "max(h_demand/cap_h, v_demand/cap_v) per GCell, unsmoothed",
            "gaussian_util": "util after the radius-4 sigma=1.0 Gaussian",
        },
    }
    with open(tmp, "w") as f:
        json.dump(meta, f, indent=1)
    os.replace(tmp, path)
    return meta
