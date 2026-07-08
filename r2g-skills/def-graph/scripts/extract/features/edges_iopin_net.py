"""I/O-pin->net edges -> edges_iopin_net.csv.

Refactored from feature_test_v2/py/edges_iopin_net.py: shared SDC parser; pin direction
+ net-type ids from lib_db.
"""
import csv
import os
import re

from case_paths import resolve_case_paths
from techlib.def_parse import parse_sdc_clock_port_names
from techlib.liberty import direction_id, infer_net_type_id


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
                    cur = {"name": parts[1], "net": "", "dir": "", "use": ""}
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
                continue
            if "+ NET " in line:
                m = re.search(r"\+ NET\s+(\S+)", line)
                if m and cur is not None:
                    cur["net"] = m.group(1)
                continue
            if "+ DIRECTION " in line:
                m = re.search(r"\+ DIRECTION\s+(\S+)", line)
                if m and cur is not None:
                    # rstrip(';') for parity with the dash-line branch above: a
                    # continuation `+ DIRECTION OUTPUT;` (no space before ';')
                    # would otherwise store 'OUTPUT;' and mis-map pin_direction_id.
                    cur["dir"] = m.group(1).upper().rstrip(";")
                continue
            if "+ USE " in line:
                m = re.search(r"\+ USE\s+(\S+)", line)
                if m and cur is not None:
                    cur["use"] = m.group(1).upper().rstrip(";")
                continue
    return pins


def main():
    ctx = resolve_case_paths(__file__, "edges_iopin_net.csv")
    graph_id = ctx["graph_id"]
    out_csv = ctx["out_csv"]
    def_path = ctx["def_path"]

    pins = parse_pins(def_path)
    clock_ports = parse_sdc_clock_port_names(ctx["sdc_path"]) if os.path.isfile(ctx["sdc_path"]) else set()
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["graph_id", "iopin_name", "net_name", "net_type_id", "pin_direction_id"])
        for p in pins:
            net_name = p.get("net", "")
            iopin_name = p.get("name", "")
            net_use = p.get("use", "") or "SIGNAL"
            net_type_id = infer_net_type_id(net_name, net_use, iopin_name in clock_ports)
            w.writerow([graph_id, iopin_name, net_name, net_type_id, direction_id(p.get("dir", ""))])


if __name__ == "__main__":
    main()
