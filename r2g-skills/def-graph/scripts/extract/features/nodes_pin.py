"""Per-pin node features -> nodes_pin.csv.

Refactored from feature_test_v2/py/nodes_pin.py: shared component/net parsers, liberty
from R2G_LIB_FILES, SPEF (I/O cap) optional. Pin x/y-std and caps are net-level stats
shared by all pins on a net (cell-origin approximation; see lib_db.get_pin_abs_pos_um).
"""
import csv
import os
import re

from case_paths import resolve_case_paths
from techlib.def_parse import parse_components, parse_nets, parse_units
from techlib.lef import cell_lef_paths, macro_pin_geometry
from techlib.liberty import (
    build_net_pin_stats,
    classify_pin_type,
    get_pin_abs_pos_um,
    get_pin_load_cap_fF,
    load_liberty_db,
)


def parse_iopins(def_path):
    pins = []
    in_pins = False
    cur = {}
    with open(def_path, "r") as f:
        for raw in f:
            line = raw.strip()
            if line.startswith("PINS"):
                in_pins = True
                continue
            if in_pins and line.startswith("END PINS"):
                if cur:
                    pins.append(cur)
                break
            if not in_pins:
                continue
            if line.startswith("-"):
                if cur:
                    pins.append(cur)
                parts = line.split()
                if len(parts) >= 2:
                    cur = {"name": parts[1], "net": "", "x": None, "y": None}
                else:
                    cur = {}
                m_net = re.search(r"\+ NET\s+(\S+)", line)
                if m_net and cur is not None:
                    cur["net"] = m_net.group(1)
                continue
            if "+ NET " in line:
                m_net = re.search(r"\+ NET\s+(\S+)", line)
                if m_net and cur is not None:
                    cur["net"] = m_net.group(1)
                continue
            if "+ PLACED " in line or "+ FIXED " in line:
                m_place = re.search(r"\(\s*(-?\d+)\s+(-?\d+)\s*\)", line)
                if m_place and cur is not None:
                    cur["x"] = int(m_place.group(1))
                    cur["y"] = int(m_place.group(2))
                continue
    return pins


def parse_spef_io_cap_by_net(spef_path, iopin_names):
    if not spef_path or not os.path.isfile(spef_path):
        return {}
    io_caps = {}
    name_map = {}
    in_name_map = False
    in_cap = False
    current_net = ""
    cap_scale_ff = 1.0
    with open(spef_path, "r") as f:
        for raw in f:
            s = raw.strip()
            if not s:
                continue
            if s.startswith("*NAME_MAP"):
                in_name_map = True
                continue
            if in_name_map:
                if s.startswith("*") and " " in s:
                    parts = s.split(None, 1)
                    if len(parts) == 2:
                        name_map[parts[0]] = parts[1].strip()
                    continue
                in_name_map = False
            if s.startswith("*C_UNIT"):
                m = re.match(r"^\*C_UNIT\s+([0-9eE+.-]+)\s+(\S+)\s*$", s)
                if m:
                    mag = float(m.group(1))
                    unit = m.group(2).upper()
                    if unit in ["FF", "FEMTOFARAD", "FEMTOFARADS"]:
                        cap_scale_ff = mag
                    elif unit in ["PF", "PICOFARAD", "PICOFARADS"]:
                        cap_scale_ff = mag * 1e3
                    elif unit in ["NF", "NANOFARAD", "NANOFARADS"]:
                        cap_scale_ff = mag * 1e6
                    elif unit in ["UF", "MICROFARAD", "MICROFARADS"]:
                        cap_scale_ff = mag * 1e9
                continue
            if s.startswith("*D_NET"):
                parts = s.split()
                current_net = parts[1] if len(parts) >= 2 else ""
                current_net = name_map.get(current_net, current_net)
                in_cap = False
                continue
            if s.startswith("*CAP"):
                in_cap = True
                continue
            if s.startswith("*RES") or s.startswith("*END"):
                in_cap = False
                continue
            if not in_cap or not current_net:
                continue
            parts = s.split()
            if len(parts) != 3:
                continue
            node = name_map.get(parts[1], parts[1])
            if node in iopin_names:
                try:
                    io_caps[current_net] = io_caps.get(current_net, 0.0) + float(parts[2]) * cap_scale_ff
                except Exception:
                    pass
    return io_caps


def main():
    ctx = resolve_case_paths(__file__, "nodes_pin.csv")
    def_path = ctx["def_path"]
    graph_id = ctx["graph_id"]
    out_csv = ctx["out_csv"]

    dbu = parse_units(def_path)
    iopins = parse_iopins(def_path)
    comps = parse_components(def_path)
    nets = parse_nets(def_path)
    lib_db = load_liberty_db(ctx["lib_files"])
    # Per-cell LEF pin geometry (SC_LEF + macro LEFs) -> real intra-cell pin
    # positions for pin_x/y_std_um. Empty {} when no cell LEF is resolvable ->
    # get_pin_abs_pos_um falls back to the instance origin (documented approx).
    pin_geom = macro_pin_geometry(cell_lef_paths())
    spef_path = ctx["spef_path"] if os.path.isfile(ctx["spef_path"]) else ""
    iopin_names = {p.get("name", "") for p in iopins if p.get("name", "")}
    io_cap_by_net = parse_spef_io_cap_by_net(spef_path, iopin_names)

    iopin_by_name = {p.get("name", ""): p for p in iopins if p.get("name", "")}
    net_stats = {}
    for net_name, info in nets.items():
        pin_points = []
        pin_caps = []
        for inst, pin in info.get("conns", []):
            if inst == "PIN":
                p = iopin_by_name.get(pin)
                if not p:
                    continue
                x = (p.get("x") or 0) / dbu
                y = (p.get("y") or 0) / dbu
                pin_points.append((x, y))
                continue
            comp = comps.get(inst)
            if not comp:
                continue
            inst_x_um = (comp.get("x") or 0) / dbu
            inst_y_um = (comp.get("y") or 0) / dbu
            px, py = get_pin_abs_pos_um(inst_x_um, inst_y_um, comp.get("orient", "N"),
                                        comp.get("master", ""), pin, geom=pin_geom)
            pin_points.append((px, py))
            # Load caps only — an output pin's max_capacitance is a drive limit,
            # not a load, and used to dominate sum_pin_cap_fF (2026-07-05 fix).
            pin_caps.append(get_pin_load_cap_fF(comp.get("master", ""), pin, lib_db))
        stats = build_net_pin_stats(pin_points, pin_caps)
        stats["sum_cap_fF"] += io_cap_by_net.get(net_name, 0.0)
        net_stats[net_name] = stats

    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["graph_id", "inst_name", "pin_name", "pin_type_id", "sum_pin_cap_fF", "pin_x_std_um", "pin_y_std_um"])

        seen = set()

        for p in iopins:
            name = p.get("name", "")
            if not name:
                continue
            key = ("PIN", name)
            if key in seen:
                continue
            seen.add(key)
            stats = net_stats.get(p.get("net", ""), {"sum_cap_fF": 0.0, "x_std_um": 0.0, "y_std_um": 0.0})
            w.writerow(
                [
                    graph_id,
                    "PIN",
                    name,
                    14,
                    f"{stats['sum_cap_fF']:.6f}",
                    f"{stats['x_std_um']:.6f}",
                    f"{stats['y_std_um']:.6f}",
                ]
            )

        for net_name, info in nets.items():
            stats = net_stats.get(net_name, {"sum_cap_fF": 0.0, "x_std_um": 0.0, "y_std_um": 0.0})
            for inst, pin in info.get("conns", []):
                if inst == "PIN":
                    continue
                key = (inst, pin)
                if key in seen:
                    continue
                seen.add(key)
                comp = comps.get(inst, {})
                pin_type_id = classify_pin_type(comp.get("master", ""), pin, lib_db)
                w.writerow(
                    [
                        graph_id,
                        inst,
                        pin,
                        pin_type_id,
                        f"{stats['sum_cap_fF']:.6f}",
                        f"{stats['x_std_um']:.6f}",
                        f"{stats['y_std_um']:.6f}",
                    ]
                )


if __name__ == "__main__":
    main()
