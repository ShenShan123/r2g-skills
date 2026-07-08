"""Per-I/O-pin node features -> nodes_iopin.csv.

Refactored from feature_test_v2/py/nodes_iopin.py: shared design-name/SDC/component
parsers; tap positions come from the shared component parser filtered by is_tap_master.
"""
import csv
import os
import re

from case_paths import resolve_case_paths
from techlib.def_parse import parse_components, parse_design_name, parse_sdc_clock_port_names, parse_units
from techlib.liberty import direction_id, infer_net_type_id, is_tap_master


def parse_pins(def_path):
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
                    cur = {"name": parts[1], "net": "", "dir": "", "use": "", "x": None, "y": None, "layer": ""}
                else:
                    cur = {}
                m = re.search(r"\+ NET\s+(\S+)", line)
                if m and cur is not None:
                    cur["net"] = m.group(1)
                m = re.search(r"\+ DIRECTION\s+(\S+)", line)
                if m and cur is not None:
                    cur["dir"] = m.group(1).upper().rstrip(";")
                m = re.search(r"\+ USE\s+(\S+)", line)
                if m and cur is not None:
                    cur["use"] = m.group(1).upper().rstrip(";")
                m = re.search(r"\+ LAYER\s+(\S+)", line)
                if m and cur is not None:
                    cur["layer"] = m.group(1)
                continue
            if "+ NET " in line:
                m = re.search(r"\+ NET\s+(\S+)", line)
                if m and cur is not None:
                    cur["net"] = m.group(1)
                continue
            if "+ DIRECTION " in line:
                m = re.search(r"\+ DIRECTION\s+(\S+)", line)
                if m and cur is not None:
                    cur["dir"] = m.group(1).upper()
                continue
            if "+ USE " in line:
                m = re.search(r"\+ USE\s+(\S+)", line)
                if m and cur is not None:
                    cur["use"] = m.group(1).upper().rstrip(";")
                continue
            if "+ LAYER " in line:
                m = re.search(r"\+ LAYER\s+(\S+)", line)
                if m and cur is not None:
                    cur["layer"] = m.group(1)
                continue
            if "+ PLACED " in line or "+ FIXED " in line:
                nums = re.findall(r"(-?\d+)", line)
                if len(nums) >= 2 and cur is not None:
                    cur["x"] = int(nums[0])
                    cur["y"] = int(nums[1])
                continue
    return pins


def tap_positions_um(def_path, dbu):
    comps = parse_components(def_path)
    taps = []
    for c in comps.values():
        if is_tap_master(c.get("master", "")) and c.get("x") is not None:
            taps.append(((c.get("x") or 0) / dbu, (c.get("y") or 0) / dbu))
    return taps


def main():
    ctx = resolve_case_paths(__file__, "nodes_iopin.csv")
    def_path = ctx["def_path"]
    graph_id = ctx["graph_id"]
    out_csv = ctx["out_csv"]

    dbu = parse_units(def_path)
    design_name = parse_design_name(def_path)
    pins = parse_pins(def_path)
    clock_ports = parse_sdc_clock_port_names(ctx["sdc_path"]) if os.path.isfile(ctx["sdc_path"]) else set()
    tap_positions = tap_positions_um(def_path, dbu)
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "graph_id",
                "iopin_name",
                "net_name",
                "pin_x_um",
                "pin_y_um",
                "pin_owner_master",
                "pin_name",
                "pin_layer_hint",
                "nearest_tap_distance_um",
                "pin_direction",
                "pin_direction_id",
                "net_use",
                "net_type_id",
            ]
        )
        for p in pins:
            x = (p.get("x") or 0) / dbu
            y = (p.get("y") or 0) / dbu
            net_name = p.get("net", "")
            iopin_name = p.get("name", "")
            nearest_tap = 0.0
            if tap_positions:
                nearest_tap = min((((x - tx) ** 2 + (y - ty) ** 2) ** 0.5) for tx, ty in tap_positions)
            net_use = p.get("use", "") or "SIGNAL"
            w.writerow(
                [
                    graph_id,
                    iopin_name,
                    net_name,
                    f"{x:.6f}",
                    f"{y:.6f}",
                    design_name,
                    iopin_name,
                    p.get("layer", ""),
                    f"{nearest_tap:.6f}",
                    p.get("dir", ""),
                    direction_id(p.get("dir", "")),
                    net_use,
                    infer_net_type_id(net_name, net_use, iopin_name in clock_ports),
                ]
            )


if __name__ == "__main__":
    main()
