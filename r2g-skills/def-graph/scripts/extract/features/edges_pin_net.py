"""Pin->net edges -> edges_pin_net.csv.

Refactored from feature_test_v2/py/edges_pin_net.py: shared net/component/SDC parsers,
liberty from R2G_LIB_FILES.
"""
import csv
import os

from case_paths import resolve_case_paths
from techlib.def_parse import parse_components_master, parse_nets, parse_sdc_clock_port_names
from techlib.liberty import classify_pin_type, infer_net_type_id, load_liberty_db


def main():
    ctx = resolve_case_paths(__file__, "edges_pin_net.csv")
    graph_id = ctx["graph_id"]
    out_csv = ctx["out_csv"]
    def_path = ctx["def_path"]

    nets = parse_nets(def_path)
    masters = parse_components_master(def_path)
    lib_db = load_liberty_db(ctx["lib_files"])
    clock_ports = parse_sdc_clock_port_names(ctx["sdc_path"]) if os.path.isfile(ctx["sdc_path"]) else set()
    clock_nets = set()
    if clock_ports:
        for net_name, info in nets.items():
            for inst, pin in info.get("conns", []):
                if inst == "PIN" and pin in clock_ports:
                    clock_nets.add(net_name)
                    break
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["graph_id", "inst_name", "pin_name", "pin_type_id", "net_name", "net_type_id"])
        for net_name, info in nets.items():
            net_type_id = infer_net_type_id(net_name, info.get("use", ""), net_name in clock_nets)
            for inst, pin in info.get("conns", []):
                if inst == "PIN":
                    continue
                pin_type_id = classify_pin_type(masters.get(inst, ""), pin, lib_db)
                w.writerow([graph_id, inst, pin, pin_type_id, net_name, net_type_id])


if __name__ == "__main__":
    main()
