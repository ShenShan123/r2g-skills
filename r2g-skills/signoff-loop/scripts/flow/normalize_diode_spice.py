#!/usr/bin/env python3
"""Normalize antenna-diode primitives in a Magic-extracted SPICE netlist.

Magic emits the sky130 diode primitive as an X subcircuit instance with NO
.subckt definition (netgen invents a black box with pins "1 2"), while the PDK
cell library models the same primitive as a D device (pins anode/cathode,
properties area/pj). The class mismatch makes netgen flatten every diode-bearing
cell (sky130_fd_sc_hd__diode_2) and fail top-level pin matching on ALL
antenna-diode designs. Rewriting X->D (and perim= -> pj=, which the netgen setup
compares with 2% tolerance) presents the same device class on both sides.

Used by run_netgen_lvs.sh. See references/failure-patterns.md "sky130 LVS",
cause 5 (fixed 2026-06-11).

usage: normalize_diode_spice.py <extracted.spice>   (rewrites in place)
"""
import re
import sys

DIODE_X_RE = re.compile(
    r"^[Xx](\S*)\s+(\S+)\s+(\S+)\s+(sky130_fd_pr__diode_\S+)\s*(.*)$")


def join_continuations(lines):
    """Collapse SPICE '+' continuation lines so each logical line is whole."""
    logical = []
    for ln in lines:
        if ln.startswith("+") and logical:
            logical[-1] += " " + ln[1:].strip()
        else:
            logical.append(ln)
    return logical


def normalize_lines(lines):
    """Rewrite diode X-instances to D-devices. Returns (new_lines, count)."""
    out, count = [], 0
    for ln in join_continuations(lines):
        m = DIODE_X_RE.match(ln)
        if m:
            name, anode, cathode, model, props = m.groups()
            props = re.sub(r"\bperim=", "pj=", props)
            out.append(f"D{name or '0'} {anode} {cathode} {model} {props}".rstrip())
            count += 1
        else:
            out.append(ln)
    return out, count


def main():
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 1
    path = sys.argv[1]
    with open(path) as fh:
        lines = fh.read().splitlines()
    out, count = normalize_lines(lines)
    with open(path, "w") as fh:
        fh.write("\n".join(out) + "\n")
    print(f"normalized {count} antenna-diode instance(s) X->D in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
