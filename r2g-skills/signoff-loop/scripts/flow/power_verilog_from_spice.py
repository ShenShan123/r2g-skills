#!/usr/bin/env python3
"""Make a Verilog schematic explicit and closed over Sky130 power nets.

The deterministic fallback for OpenROAD ``write_verilog -include_pwr_gnd``
uses the exact SPICE cell signatures.  Logical ORFS netlists can also retain
small RTL-generated child modules.  Power pins added inside those children
must be propagated through their module ports; otherwise each child gets a
private implicit ``VDD``/``VSS`` net and Netgen reports a topology mismatch.
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
MODULE_RE = re.compile(
    r"(?ms)^(?P<prefix>\s*module\s+(?P<name>[^\s(]+)\s*\()"
    r"(?P<header>.*?)(?P<close>\)\s*;)"
    r"(?P<body>.*?^\s*endmodule\b)"
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
    if transformed and not re.search(r"(?m)^\s*(?:wire|inout)\s+VDD\s*;", output):
        module_end = re.search(r"(?ms)^\s*module\s+.*?\)\s*;", output)
        if not module_end:
            raise ValueError("top module declaration not found")
        # The logical ORFS top usually has no external power pins.  Keep the
        # derived supplies as internal nets so LVS compares the same top-level
        # interface as the extracted layout.  Hierarchical child modules are
        # upgraded to explicit inout ports below.
        output = output[:module_end.end()] + "\n wire VDD;\n wire VSS;" + output[module_end.end():]
    return output, {
        "instances_powered": transformed,
        "unknown_cell_types": sorted(unknown_cells),
        "known_cell_types": len(pin_map),
    }


def _module_has_power_cells(body: str, known_cells: set[str]) -> bool:
    if known_cells:
        cell_pattern = "|".join(re.escape(cell) for cell in sorted(known_cells))
        if re.search(rf"(?m)^\s*(?:{cell_pattern})\s+", body):
            return True
    # The standalone normalizer has no SPICE map.  This conservative pattern
    # covers Sky130/efab standard-cell instances without guessing arbitrary
    # user modules.
    return bool(re.search(r"(?m)^\s*(?:sky130_|ef_)[^\s(]+\s+[^\s(]+\s*\(", body))


def propagate_hierarchical_power_ports(
    verilog: str, known_cells: set[str] | None = None
) -> tuple[str, dict]:
    """Connect explicit VDD/VSS through every powered child module.

    ORFS may emit non-flattened implementation modules.  A power pin added to
    a cell in such a child is otherwise an undeclared implicit local net.  This
    pass adds non-ANSI ``inout`` ports to powered modules and named
    ``.VDD(VDD), .VSS(VSS)`` connections at each powered-child instance.  It
    reports positional child instances instead of silently rewriting them.
    """
    known_cells = set(known_cells or ())
    modules = list(MODULE_RE.finditer(verilog))
    names = {m.group("name") for m in modules}
    if not modules:
        return verilog, {
            "modules_seen": 0,
            "modules_powered": [],
            "child_instances_connected": 0,
            "positional_child_instances_skipped": 0,
            "changed": False,
            "reason": "no_modules",
        }

    # A module that instantiates a powered module also needs the supply pair.
    powered = {
        m.group("name")
        for m in modules
        if _module_has_power_cells(m.group("body"), known_cells)
    }
    changed_closure = True
    while changed_closure:
        changed_closure = False
        for m in modules:
            if m.group("name") in powered:
                continue
            body = m.group("body")
            if any(re.search(rf"(?m)^\s*{re.escape(child)}\s+[^\s(]+\s*\(", body)
                   for child in powered if child in names):
                powered.add(m.group("name"))
                changed_closure = True

    # The top-level module is not a child of another module.  Its VDD/VSS are
    # internal supply nets unless the source already declared them as actual
    # interface ports; adding synthetic top ports makes Netgen report a pin
    # mismatch when the layout has no corresponding labels.
    instantiated = set()
    for m in modules:
        body = m.group("body")
        for child in names:
            if child == m.group("name"):
                continue
            if re.search(rf"(?m)^\s*{re.escape(child)}\s+[^\s(]+\s*\(", body):
                instantiated.add(child)
    top_names = names - instantiated or {modules[0].group("name")}

    edits: list[tuple[int, int, str]] = []
    module_header_edits: dict[str, tuple[int, int, str]] = {}
    declarations_added: list[str] = []
    for m in modules:
        name = m.group("name")
        if name not in powered:
            continue
        header = m.group("header")
        tokens = re.findall(r"(?<![\w$])(VDD|VSS)(?![\w$])", header)
        new_header = header
        missing = [supply for supply in ("VDD", "VSS") if supply not in tokens]
        if missing and name not in top_names:
            trimmed = header.rstrip()
            suffix = header[len(trimmed):]
            if not trimmed.endswith(","):
                trimmed += ","
            new_header = trimmed + "\n    VDD,\n    VSS" + suffix
        if new_header != header:
            module_header_edits[name] = (m.start("header"), m.end("header"), new_header)
        body = m.group("body")
        decl_missing = [
            supply for supply in ("VDD", "VSS")
            if not re.search(rf"(?m)^\s*(?:wire|input|output|inout)\s+{supply}\s*;", body)
        ]
        if decl_missing:
            declarations_added.append(name)
            insert_at = m.start("close") + len(m.group("close"))
            declaration_kind = "wire" if name in top_names else "inout"
            declaration = "".join(
                f"\n {declaration_kind} {supply};" for supply in decl_missing)
            edits.append((insert_at, insert_at, declaration))

    # Apply edits in reverse source order so original offsets remain valid.
    edits.extend(module_header_edits.values())
    for start, end, replacement in sorted(edits, reverse=True):
        verilog = verilog[:start] + replacement + verilog[end:]

    connected = 0
    skipped = 0
    child_types = sorted(powered)
    if child_types:
        child_pattern = "|".join(re.escape(name) for name in child_types)
        instance_re = re.compile(
            rf"(?ms)^(?P<indent>\s*)(?P<type>{child_pattern})\s+"
            r"(?P<instance>[^\s(]+)\s*\((?P<body>.*?)\);"
        )

        def connect(match: re.Match[str]) -> str:
            nonlocal connected, skipped
            body = match.group("body")
            present = set(re.findall(r"\.(\w+)\s*\(", body))
            if {"VDD", "VSS"}.issubset(present):
                return match.group(0)
            if not present:
                skipped += 1
                return match.group(0)
            additions = "".join(
                f",\n{match.group('indent')}    .{supply}({supply})"
                for supply in ("VDD", "VSS") if supply not in present
            )
            connected += 1
            return (f"{match.group('indent')}{match.group('type')} "
                    f"{match.group('instance')} ({body}{additions});")

        verilog = instance_re.sub(connect, verilog)

    changed = bool(module_header_edits or edits or connected)
    return verilog, {
        "modules_seen": len(modules),
        "modules_powered": sorted(powered),
        "modules_declarations_added": declarations_added,
        "child_instances_connected": connected,
        "positional_child_instances_skipped": skipped,
        "changed": changed,
        "reason": "propagated_power_ports" if changed else "already_closed",
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
    pin_map = cell_power_pins(spice)
    output, stats = add_power_pins(source, pin_map)
    output, hierarchy = propagate_hierarchical_power_ports(output, set(pin_map))
    if stats["instances_powered"] == 0 or ".VPWR(" not in output:
        raise SystemExit("no standard-cell power pins were added")
    _write(args.output, output)
    receipt = {
        "schema": "r2g.powered_verilog_from_spice.v2",
        "source": str(args.verilog.resolve()),
        "source_sha256": _sha(source),
        "spice": str(args.spice.resolve()),
        "spice_sha256": _sha(spice),
        "output": str(args.output.resolve()),
        "output_sha256": _sha(output),
        **stats,
        "hierarchical_power": hierarchy,
    }
    if args.receipt:
        _write(args.receipt, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
