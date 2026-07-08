"""Gate->pin edges -> edges_gate_pin.csv.

Refactored from feature_test_v2/py/edges_gate_pin.py: cell-type id via imported
cell_type_map (no exec()), shared component/net parsers, liberty from R2G_LIB_FILES.
"""
import csv

from case_paths import resolve_case_paths
from techlib.cell_types import cell_type_id, resolve_cell_type_map
from techlib.def_parse import parse_components_master, parse_nets
from techlib.liberty import classify_pin_type, load_liberty_db


def main():
    ctx = resolve_case_paths(__file__, "edges_gate_pin.csv")
    graph_id = ctx["graph_id"]
    out_csv = ctx["out_csv"]
    def_path = ctx["def_path"]

    masters = parse_components_master(def_path)
    lib_db = load_liberty_db(ctx["lib_files"])
    mp = resolve_cell_type_map(ctx["platform"], lib_db, ctx["sc_lib_files"])
    nets = parse_nets(def_path)
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["graph_id", "inst_name", "pin_name", "cell_type_id", "pin_type_id"])
        seen = set()
        for info in nets.values():
            for inst, pin in info.get("conns", []):
                if inst == "PIN":
                    continue
                key = (inst, pin)
                if key in seen:
                    continue
                seen.add(key)
                master = masters.get(inst, "")
                ct = cell_type_id(master, mp)
                pin_type_id = classify_pin_type(master, pin, lib_db)
                w.writerow([graph_id, inst, pin, ct, pin_type_id])


if __name__ == "__main__":
    main()
