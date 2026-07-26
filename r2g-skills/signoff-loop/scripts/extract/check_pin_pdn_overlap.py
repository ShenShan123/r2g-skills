#!/usr/bin/env python3
"""Diagnose a REAL pin-vs-PDN short behind a Netgen `top_pin_mismatch`
(failure-patterns.md #58, found live 2026-07-26 on sky130hs FFT ROM_16).

A `Top level cell failed pin matching` verdict is opaque: it covers antenna
diode representation, port feedthroughs, extraction artifacts — and GENUINE
layout shorts. ROM_16's case was the last kind: ORFS placed the met3 IO pins
`w_r[12]`/`w_r[20]` on coordinates crossed by met3 VSS PDN straps, so the
extracted netlist honestly merged the outputs into ground (net counts 201 vs
203) while the geometry-only KLayout deck (spacing rules, not connectivity)
stayed clean. Without this check the loop files a generic `top_pin_mismatch`
and burns fix sessions on the wrong strategies.

This tool re-derives the short GEOMETRICALLY from the DEF: every PINS-section
pin rectangle is tested for overlap against every SPECIALNETS stripe on the
same layer belonging to a different (power) net. Pure stdlib, independent of
Magic/Netgen — usable as evidence, not inference.

usage: check_pin_pdn_overlap.py <6_final.def> [--json]
stdout: one line per shorted pin (or JSON with --json).
exit: 0 = no overlap found; 4 = overlaps found; 1 = parse error.
"""
from __future__ import annotations

import json
import re
import sys


def parse_pins(text: str):
    """[(pin, net, layer, (x1,y1,x2,y2))] from the PINS section (PLACED pins
    with a LAYER rect; multi-PORT pins contribute each rect)."""
    m = re.search(r"\bPINS\b.*?END PINS", text, re.S)
    if not m:
        return []
    out = []
    for blk in re.finditer(r"- (\S+) \+ NET (\S+)(.*?);", m.group(0), re.S):
        pin, net, body = blk.group(1), blk.group(2), blk.group(3)
        if "+ USE SIGNAL" not in body and "+ USE " in body:
            continue                       # power/clock pins are not the subject
        layers = re.findall(
            r"LAYER (\S+) \( (-?\d+) (-?\d+) \) \( (-?\d+) (-?\d+) \)", body)
        placed = re.findall(r"(?:PLACED|FIXED) \( (-?\d+) (-?\d+) \)", body)
        for (lay, x1, y1, x2, y2), (px, py) in zip(layers, placed):
            x1, y1, x2, y2, px, py = map(int, (x1, y1, x2, y2, px, py))
            out.append((pin, net, lay,
                        (px + min(x1, x2), py + min(y1, y2),
                         px + max(x1, x2), py + max(y1, y2))))
    return out


def parse_specialnet_stripes(text: str):
    """[(net, layer, (x1,y1,x2,y2))] for every routed SPECIALNETS segment."""
    m = re.search(r"SPECIALNETS.*?END SPECIALNETS", text, re.S)
    if not m:
        return []
    out = []
    for blk in re.finditer(r"- (\S+)(.*?);", m.group(0), re.S):
        net, body = blk.group(1), blk.group(2)
        for seg in re.finditer(
                r"(\S+) (\d+) (?:\+ SHAPE \w+ )?\( (-?\d+) (-?\d+) \) "
                r"\( (\*|-?\d+) (\*|-?\d+) \)", body):
            lay, w = seg.group(1), int(seg.group(2))
            ax, ay = int(seg.group(3)), int(seg.group(4))
            bx = ax if seg.group(5) == "*" else int(seg.group(5))
            by = ay if seg.group(6) == "*" else int(seg.group(6))
            half = w // 2
            out.append((net, lay, (min(ax, bx) - half, min(ay, by) - half,
                                   max(ax, bx) + half, max(ay, by) + half)))
    return out


def _overlap(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def find_shorts(def_path: str):
    text = open(def_path, errors="replace").read()
    pins = parse_pins(text)
    stripes = parse_specialnet_stripes(text)
    shorts = []
    for pin, net, lay, box in pins:
        for snet, slay, sbox in stripes:
            if slay == lay and snet != net and _overlap(box, sbox):
                shorts.append({"pin": pin, "net": net, "layer": lay,
                               "pin_bbox": box, "power_net": snet,
                               "stripe_bbox": sbox})
    return shorts


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-3], file=sys.stderr)
        return 1
    as_json = "--json" in argv
    try:
        shorts = find_shorts(argv[1])
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps({"shorts": shorts}, indent=1))
    else:
        for s in shorts:
            print(f"PIN-PDN SHORT: pin {s['pin']} ({s['layer']}) overlaps "
                  f"{s['power_net']} stripe — pin bbox {s['pin_bbox']}, "
                  f"stripe bbox {s['stripe_bbox']}")
        if not shorts:
            print("no pin-vs-PDN overlap found")
    return 4 if shorts else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
