"""Per-instance node features -> nodes_gate.csv.

Refactored from feature_test_v2/py/nodes_gate.py: cell-type id via the imported
cell_type_map (no more exec() of net_to_pt.py), components via the shared parser,
liberty from R2G_LIB_FILES. One row per placed component, in DEF declaration order.
"""
import csv

from case_paths import resolve_case_paths
from techlib.cell_types import cell_type_id, resolve_cell_type_map
from techlib.def_parse import parse_components, parse_units
from techlib.liberty import get_cell_area, get_cell_power, load_liberty_db


def orient_id(s):
    t = s.upper()
    lst = ["N", "S", "E", "W", "FN", "FS", "FE", "FW"]
    try:
        return lst.index(t)
    except Exception:
        return -1


def status_id(s):
    t = s.upper()
    if t == "PLACED":
        return 0
    if t == "FIXED":
        return 1
    if t == "UNPLACED":
        return 2
    return -1


def main():
    ctx = resolve_case_paths(__file__, "nodes_gate.csv")
    def_path = ctx["def_path"]
    graph_id = ctx["graph_id"]
    out_csv = ctx["out_csv"]

    dbu = parse_units(def_path)
    lib_db = load_liberty_db(ctx["lib_files"])
    mp = resolve_cell_type_map(ctx["platform"], lib_db, ctx["sc_lib_files"])
    comps = parse_components(def_path)
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "graph_id",
                "inst_name",
                "master",
                "cell_type_id",
                "cell_area",
                "cell_power",
                "x_um",
                "y_um",
                "orientation",
                "orientation_id",
                "placement_status",
                "placement_status_id",
            ]
        )
        for inst, c in comps.items():
            x = (c.get("x") or 0) / dbu
            y = (c.get("y") or 0) / dbu
            master = c.get("master", "")
            ct = cell_type_id(master, mp)
            w.writerow(
                [
                    graph_id,
                    inst,
                    master,
                    ct,
                    f"{get_cell_area(master, lib_db):.6f}",
                    f"{get_cell_power(master, lib_db):.6f}",
                    f"{x:.6f}",
                    f"{y:.6f}",
                    c.get("orient", ""),
                    orient_id(c.get("orient", "")),
                    c.get("status", ""),
                    status_id(c.get("status", "")),
                ]
            )


if __name__ == "__main__":
    main()
