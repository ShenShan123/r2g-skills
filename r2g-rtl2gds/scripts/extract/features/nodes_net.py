"""Per-net node features -> nodes_net.csv.

Refactored from feature_test_v2/py/nodes_net.py: shared component/net/SDC parsers,
liberty from R2G_LIB_FILES, and a tech-LEF-derived routing-layer matcher (replacing the
hardcoded ``metal\\d+`` regex) so ``num_layer`` is correct on every platform. On nangate45
the derived matcher reduces to the original metal-layer count.
"""
import csv
import os

from case_paths import resolve_case_paths
from techlib.def_parse import (
    parse_components,
    parse_nets,
    parse_sdc_clock_port_names,
    parse_units,
)
from techlib.lef import routing_layer_regex
from techlib.liberty import (
    get_pin_direction,
    infer_net_type_id,
    load_liberty_db,
    macro_cell_keys,
    norm_cell_key,
)


def parse_iopins(def_path):
    pins = {}
    in_pins = False
    cur = None
    import re
    with open(def_path, "r") as f:
        for raw in f:
            s = raw.strip()
            if s.startswith("PINS"):
                in_pins = True
                continue
            if in_pins and s.startswith("END PINS"):
                break
            if not in_pins:
                continue
            if s.startswith("-"):
                parts = s.split()
                if len(parts) >= 2:
                    cur = parts[1]
                    pins[cur] = {"x": 0, "y": 0}
                else:
                    cur = None
                m_place = re.search(r"\+\s*(PLACED|FIXED)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)", s)
                if cur and m_place:
                    pins[cur]["x"] = int(m_place.group(2))
                    pins[cur]["y"] = int(m_place.group(3))
                continue
            m_place = re.search(r"\+\s*(PLACED|FIXED)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)", s)
            if cur and m_place and cur in pins:
                pins[cur]["x"] = int(m_place.group(2))
                pins[cur]["y"] = int(m_place.group(3))
    return pins


def parse_pin_dirs(def_path):
    pins = {}
    in_pins = False
    cur_name = ""
    import re
    with open(def_path, "r") as f:
        for raw in f:
            s = raw.strip()
            if s.startswith("PINS"):
                in_pins = True
                continue
            if in_pins and s.startswith("END PINS"):
                break
            if not in_pins:
                continue
            if s.startswith("-"):
                parts = s.split()
                cur_name = parts[1] if len(parts) >= 2 else ""
                m_dir = re.search(r"\+\s*DIRECTION\s+(\S+)", s)
                if cur_name and m_dir:
                    pins[cur_name] = m_dir.group(1).upper().rstrip(";")
                continue
            m_dir = re.search(r"\+\s*DIRECTION\s+(\S+)", s)
            if cur_name and m_dir:
                pins[cur_name] = m_dir.group(1).upper().rstrip(";")
    return pins


def hpwl_from_points(pts):
    if not pts:
        return 0.0
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (max(xs) - min(xs)) + (max(ys) - min(ys))


def main():
    ctx = resolve_case_paths(__file__, "nodes_net.csv")
    def_path = ctx["def_path"]
    graph_id = ctx["graph_id"]
    out_csv = ctx["out_csv"]

    dbu = parse_units(def_path)
    clock_ports = parse_sdc_clock_port_names(ctx["sdc_path"]) if os.path.isfile(ctx["sdc_path"]) else set()
    lib_db = load_liberty_db(ctx["lib_files"])
    # Masters that only exist in the per-design macro libs (lib_files minus
    # sc_lib_files), e.g. fakeram45_* — used for connects_macro_flag. Empty for
    # pure std-cell designs, where 0 is the correct flag.
    macro_keys = macro_cell_keys(ctx["lib_files"], ctx["sc_lib_files"])
    layer_re, _from_lef = routing_layer_regex(ctx["tech_lef"])
    comp_info = parse_components(def_path)
    iopin_pos = parse_iopins(def_path)
    pin_dirs = parse_pin_dirs(def_path)
    nets = parse_nets(def_path)
    clock_nets = set()
    if clock_ports:
        for net_name, info in nets.items():
            for inst, pin in info.get("conns", []):
                if inst == "PIN" and pin in clock_ports:
                    clock_nets.add(net_name)
                    break
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "graph_id",
                "net_name",
                "net_type_id",
                "fanout",
                "pin_count",
                "num_drivers",
                "num_sinks",
                "connects_macro_flag",
                "num_layer",
                "hpwl_um",
            ]
        )
        for name, info in nets.items():
            pin_count = len(info.get("conns", []))
            fanout = max(0, pin_count - 1)
            num_drivers = 0
            num_sinks = 0
            connects_macro_flag = 0
            for inst, pin in info.get("conns", []):
                if inst == "PIN":
                    # DEF PIN DIRECTION is the port's direction from the CHIP's
                    # perspective: an INPUT port drives the net internally, an
                    # OUTPUT port sinks it (2026-07-05 fix — the unswapped
                    # mapping counted every output-port net as 2-driver/0-sink).
                    direction = pin_dirs.get(pin, "")
                    if direction == "INPUT":
                        num_drivers += 1
                    elif direction == "OUTPUT":
                        num_sinks += 1
                    elif direction in {"INOUT", "FEEDTHRU"}:
                        num_drivers += 1
                        num_sinks += 1
                    continue
                master = comp_info.get(inst, {}).get("master", "")
                if macro_keys and norm_cell_key(master) in macro_keys:
                    connects_macro_flag = 1
                direction = get_pin_direction(master, pin, lib_db)
                if direction == "OUTPUT":
                    num_drivers += 1
                elif direction == "INPUT":
                    num_sinks += 1
                elif direction in {"INOUT", "FEEDTHRU"}:
                    num_drivers += 1
                    num_sinks += 1
            if num_drivers == 0 and pin_count > 0:
                num_drivers = 1
                num_sinks = max(0, pin_count - 1)
            layers = set()
            hpwl_points = []
            for inst, pin in info.get("conns", []):
                if inst == "PIN":
                    p = iopin_pos.get(pin)
                    if p:
                        hpwl_points.append((p.get("x", 0) / dbu, p.get("y", 0) / dbu))
                else:
                    c = comp_info.get(inst)
                    if c:
                        hpwl_points.append(((c.get("x") or 0) / dbu, (c.get("y") or 0) / dbu))
            for r in info.get("routes", []):
                m = layer_re.search(r)
                if m:
                    layers.add(m.group(1).lower())
            hp = hpwl_from_points(hpwl_points)
            net_type_id = infer_net_type_id(name, info.get("use", ""), name in clock_nets)
            w.writerow([graph_id, name, net_type_id, fanout, pin_count, num_drivers, num_sinks, connects_macro_flag, len(layers), f"{hp:.2f}"])


if __name__ == "__main__":
    main()
