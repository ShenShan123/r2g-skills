#!/usr/bin/env python3
"""Add explicit standard-cell power pins to a named-pin Verilog netlist.

This is the deterministic fallback for OpenROAD ``write_verilog
-include_pwr_gnd`` failures.  Power-pin signatures come from the exact SPICE
library used by Netgen, so the transform never guesses pins a cell does not
have.  Functional connections and the top-level port list are unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


POWER_NET = {"VGND": "VSS", "VNB": "VSS", "VPB": "VDD", "VPWR": "VDD"}
SUBCKT_RE = re.compile(r"^\.subckt\s+(\S+)\s+(.*)$", re.IGNORECASE | re.MULTILINE)
INSTANCE_RE = re.compile(
    r"(?ms)^(\s*)(sky130_(?:fd|ef)_sc_[^\s]+)\s+([^\s(]+)\s*\((.*?)\);"
)


def cell_power_pins(spice: str) -> dict[str, tuple[str, ...]]:
    result = {}
    for match in SUBCKT_RE.finditer(spice):
        pins = tuple(pin for pin in match.group(2).split() if pin in POWER_NET)
        if pins:
            result[match.group(1)] = pins
    return result


def add_power_pins(verilog: str, pin_map: dict[str, tuple[str, ...]]) -> tuple[str, dict]:
    transformed = 0
    unknown_cells: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        nonlocal transformed
        indent, cell, instance, body = match.groups()
        pins = pin_map.get(cell)
        if not pins:
            unknown_cells.add(cell)
            return match.group(0)
        present = set(re.findall(r"\.(\w+)\s*\(", body))
        missing = [pin for pin in pins if pin not in present]
        if not missing:
            return match.group(0)
        additions = "".join(
            f",\n{indent}    .{pin}({POWER_NET[pin]})" for pin in missing
        )
        transformed += 1
        return f"{indent}{cell} {instance} ({body}{additions});"

    output = INSTANCE_RE.sub(replace, verilog)
    if transformed and not re.search(r"(?m)^\s*wire\s+VDD\s*;", output):
        module_end = re.search(r"(?ms)^\s*module\s+.*?\);", output)
        if not module_end:
            raise ValueError("top module declaration not found")
        output = output[:module_end.end()] + "\n wire VDD;\n wire VSS;" + output[module_end.end():]
    return output, {
        "instances_powered": transformed,
        "unknown_cell_types": sorted(unknown_cells),
        "known_cell_types": len(pin_map),
    }


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("verilog", type=Path)
    ap.add_argument("spice", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--receipt", type=Path)
    args = ap.parse_args()
    source = args.verilog.read_text()
    spice = args.spice.read_text()
    output, stats = add_power_pins(source, cell_power_pins(spice))
    if stats["instances_powered"] == 0 or ".VPWR(" not in output:
        raise SystemExit("no standard-cell power pins were added")
    _write(args.output, output)
    receipt = {
        "schema": "r2g.powered_verilog_from_spice.v1",
        "source": str(args.verilog.resolve()),
        "source_sha256": _sha(source),
        "spice": str(args.spice.resolve()),
        "spice_sha256": _sha(spice),
        "output": str(args.output.resolve()),
        "output_sha256": _sha(output),
        **stats,
    }
    if args.receipt:
        _write(args.receipt, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
