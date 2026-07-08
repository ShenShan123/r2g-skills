#!/usr/bin/env python3
"""Inject an antenna model into the nangate45 LEFs so OpenROAD repair_antennas works.

Background (see references/signoff-fixing.md "nangate45 antenna repair"):
the stock nangate45 tech LEF ships **no** per-layer antenna ratio rules, so
OpenROAD `check_antennas` finds nothing and `repair_antennas` never fires — while
KLayout's FreePDK45.lydrc signoff deck flags antennas at 300:1.  Separately, the
only antenna diode `ANTENNA_X1` declares `ANTENNADIFFAREA 0.0`, which OpenROAD
rejects (RepairAntennas.cpp: a CORE_ANTENNACELL is only used `if diffArea > 0.0`),
so GRT-0246 "No diode with LEF class CORE ANTENNACELL found" is emitted.

This module performs two pure text transforms:

  * patch_tech_lef(text, ratio): add `ANTENNAMODEL OXIDE1` + `ANTENNAAREARATIO <ratio>`
    to every ROUTING layer block.  Default ratio = 300 — it MATCHES the signoff deck;
    it does NOT relax it.  (A tighter ratio makes OpenROAD over-repair so KLayout, whose
    PAR accounting differs slightly, still reads clean.)
  * patch_sc_lef(text, diff_area, cell): give the antenna diode a non-zero
    ANTENNADIFFAREA so repair_antennas accepts it.  The magnitude only tunes
    OpenROAD's diode-relief modelling; the KLayout signoff re-derives diode relief
    from the real GDS diffusion geometry, so it cannot produce a false pass.

Both transforms are idempotent (re-running is a no-op / rewrites the same value) and
do not touch any DRC/LVS *rule deck*.  This is a tech-model input to the router, not a
signoff rule.
"""
from __future__ import annotations

import argparse
import re
import sys

MARKER = "# r2g-antenna-model"
DEFAULT_RATIO = 300
DEFAULT_DIFF_AREA = 0.1
DIODE_CELL = "ANTENNA_X1"

# A LAYER *block* opener is `LAYER <name>` with no trailing `;`.  A `LAYER <name> ;`
# statement (inside MACRO PIN PORTs) ends with `;` and must NOT be treated as a block.
_LAYER_OPEN = re.compile(r"^(\s*)LAYER\s+(\S+)\s*$")
_TYPE_ROUTING = re.compile(r"^\s*TYPE\s+ROUTING\s*;")


def _split_keepends(text: str):
    """Return (lines_without_newline, had_trailing_newline)."""
    had_nl = text.endswith("\n")
    lines = text.split("\n")
    if had_nl:
        lines = lines[:-1]  # drop the empty element after the final newline
    return lines, had_nl


def _join(lines, had_nl: bool) -> str:
    return "\n".join(lines) + ("\n" if had_nl else "")


def patch_tech_lef(text: str, ratio: int = DEFAULT_RATIO) -> str:
    """Insert the antenna model before END of every ROUTING layer block (idempotent)."""
    lines, had_nl = _split_keepends(text)
    out: list[str] = []
    block: list[str] = []
    indent = "  "
    layer_name = None  # non-None while inside a LAYER block

    def flush():
        nonlocal block
        is_routing = any(_TYPE_ROUTING.match(l) for l in block)
        has_marker = any(MARKER in l for l in block)
        if is_routing and not has_marker:
            ins = [f"{indent}{MARKER}",
                   f"{indent}ANTENNAMODEL OXIDE1 ;",
                   f"{indent}ANTENNAAREARATIO {ratio} ;"]
            block = block[:-1] + ins + [block[-1]]  # before the trailing `END <name>`
        out.extend(block)
        block = []

    for l in lines:
        if layer_name is None:
            m = _LAYER_OPEN.match(l)
            if m:
                layer_name = m.group(2)
                block = [l]
            else:
                out.append(l)
        else:
            block.append(l)
            if re.match(rf"^\s*END\s+{re.escape(layer_name)}\b", l):
                flush()
                layer_name = None
    if block:  # unterminated block (malformed LEF) — emit untouched
        out.extend(block)
    return _join(out, had_nl)


def parse_pin_antenna(ref_text: str) -> dict:
    """Reference LEF → {(macro, pin): [stripped ANTENNA* property lines]}.

    Antenna properties live at pin scope (between DIRECTION and PORT); they never
    appear inside a PORT block, so collecting every `ANTENNA…` line seen while inside
    a PIN is sufficient.
    """
    data: dict = {}
    macro = pin = None
    for l in ref_text.split("\n"):
        s = l.strip()
        mM = re.match(r"^MACRO\s+(\S+)", s)
        if mM:
            macro, pin = mM.group(1), None
            continue
        if macro and re.match(rf"^END\s+{re.escape(macro)}\b", s):
            macro = pin = None
            continue
        mP = re.match(r"^PIN\s+(\S+)", s)
        if mP:
            pin = mP.group(1)
            continue
        if pin and re.match(rf"^END\s+{re.escape(pin)}\b", s):
            pin = None
            continue
        if macro and pin and s.startswith("ANTENNA"):
            data.setdefault((macro, pin), []).append(s)
    return data


def merge_pin_antenna(mod_text: str, ref_text: str) -> str:
    """Inject per-pin ANTENNA* properties from a reference LEF into the SC LEF.

    Idempotent: a pin that already carries any ANTENNA property is left untouched.
    Properties are inserted just before the pin's PORT (or its END, for portless
    pins), matching the reference ordering.
    """
    data = parse_pin_antenna(ref_text)
    lines, had_nl = _split_keepends(mod_text)
    out: list[str] = []
    macro = pin = None
    pin_indent = ""
    have_ant = injected = False
    for l in lines:
        s = l.strip()
        mM = re.match(r"^MACRO\s+(\S+)", s)
        if mM:
            macro, pin = mM.group(1), None
            out.append(l)
            continue
        if macro and re.match(rf"^END\s+{re.escape(macro)}\b", s):
            macro = pin = None
            out.append(l)
            continue
        mP = re.match(r"^PIN\s+(\S+)", s)
        if mP:
            pin = mP.group(1)
            pin_indent = re.match(r"^(\s*)", l).group(1) + "  "
            have_ant = injected = False
            out.append(l)
            continue
        if pin is not None:
            if s.startswith("ANTENNA"):
                have_ant = True
            if not injected and (s.startswith("PORT") or re.match(rf"^END\s+{re.escape(pin)}\b", s)):
                key = (macro, pin)
                if key in data and not have_ant:
                    out.extend(f"{pin_indent}{al}" for al in data[key])
                injected = True
            if re.match(rf"^END\s+{re.escape(pin)}\b", s):
                pin = None
        out.append(l)
    return _join(out, had_nl)


def _fix_diode_pinA(pin_lines: list, diff_area: float, indent: str) -> list:
    """Transform a diode cell's input-pin block: drop ANTENNAGATEAREA, ensure exactly
    one positive ANTENNADIFFAREA (rewrite existing, else insert after DIRECTION)."""
    has_diff = any("ANTENNADIFFAREA" in pl for pl in pin_lines)
    res: list = []
    for pl in pin_lines:
        if "ANTENNAGATEAREA" in pl:          # a diode node is diffusion, not a gate
            continue
        if "ANTENNADIFFAREA" in pl:
            res.append(re.sub(r"(ANTENNADIFFAREA\s+)[0-9.eE+-]+", rf"\g<1>{diff_area}", pl))
            continue
        res.append(pl)
        if not has_diff and re.match(r"^\s*DIRECTION\b", pl):
            res.append(f"{indent}ANTENNADIFFAREA {diff_area} ;")
    return res


def fix_diode(text: str, diff_area: float = DEFAULT_DIFF_AREA, cell: str = DIODE_CELL) -> str:
    """Make the antenna diode usable by repair_antennas (idempotent).

    OpenROAD only accepts a CORE_ANTENNACELL as a diode when its pin declares
    ANTENNADIFFAREA > 0 (RepairAntennas.cpp).  The stock cell has ANTENNADIFFAREA 0.0
    (and, after a pin-merge from .macro.lef, an ANTENNAGATEAREA that mislabels the
    diffusion node as a gate).  We rewrite the first signal pin of the diode cell so it
    carries exactly one positive ANTENNADIFFAREA and no ANTENNAGATEAREA.
    """
    lines, had_nl = _split_keepends(text)
    out: list = []
    i, n = 0, len(lines)
    in_cell = False
    while i < n:
        l = lines[i]
        if re.match(rf"^\s*MACRO\s+{re.escape(cell)}\b", l):
            in_cell = True
        elif in_cell and re.match(rf"^\s*END\s+{re.escape(cell)}\b", l):
            in_cell = False
        if in_cell:
            mP = re.match(r"^(\s*)PIN\s+(A)\b", l)  # the diode's signal pin
            if mP:
                indent = mP.group(1) + "  "
                pin_lines = [l]
                i += 1
                while i < n and not re.match(r"^\s*END\s+A\b", lines[i]):
                    pin_lines.append(lines[i]); i += 1
                if i < n:
                    pin_lines.append(lines[i])  # END A
                out.extend(_fix_diode_pinA(pin_lines, diff_area, indent))
                i += 1
                continue
        out.append(l)
        i += 1
    return _join(out, had_nl)


def patch_sc_lef(text: str, diff_area: float = DEFAULT_DIFF_AREA, cell: str = DIODE_CELL,
                 ref_text: str | None = None) -> str:
    """Full SC-LEF patch: merge pin antenna model (if ref given) then fix the diode."""
    if ref_text is not None:
        text = merge_pin_antenna(text, ref_text)
    return fix_diode(text, diff_area=diff_area, cell=cell)


def sc_gate_area_count(text: str) -> int:
    return len(re.findall(r"^\s*ANTENNAGATEAREA\s", text, re.M))


def tech_model_layers(text: str) -> int:
    """Count routing layers that carry the injected ANTENNAAREARATIO (for verification)."""
    return len(re.findall(r"^\s*ANTENNAAREARATIO\s", text, re.M))


def diode_diff_area(text: str, cell: str = DIODE_CELL):
    """Return the ANTENNADIFFAREA value declared inside the diode cell, or None."""
    in_cell = False
    for l in text.split("\n"):
        if re.match(rf"^\s*MACRO\s+{re.escape(cell)}\b", l):
            in_cell = True
        elif in_cell and re.match(rf"^\s*END\s+{re.escape(cell)}\b", l):
            return None
        elif in_cell:
            m = re.search(r"ANTENNADIFFAREA\s+([0-9.eE+-]+)", l)
            if m:
                return float(m.group(1))
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Patch nangate45 LEFs with an antenna model.")
    ap.add_argument("mode", choices=["tech", "sc"])
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", dest="out", required=True)
    ap.add_argument("--ratio", type=int, default=DEFAULT_RATIO)
    ap.add_argument("--diff-area", type=float, default=DEFAULT_DIFF_AREA)
    ap.add_argument("--cell", default=DIODE_CELL)
    ap.add_argument("--ref", help="reference LEF (.macro.lef) to merge per-pin antenna model from")
    args = ap.parse_args(argv)

    text = open(args.inp, encoding="utf-8").read()
    if args.mode == "tech":
        new = patch_tech_lef(text, ratio=args.ratio)
        n = tech_model_layers(new)
        if n == 0:
            print("ERROR: no routing layers received an antenna ratio", file=sys.stderr)
            return 1
        open(args.out, "w", encoding="utf-8").write(new)
        print(f"tech LEF: {n} routing layers now carry ANTENNAAREARATIO {args.ratio}")
    else:
        ref_text = open(args.ref, encoding="utf-8").read() if args.ref else None
        new = patch_sc_lef(text, diff_area=args.diff_area, cell=args.cell, ref_text=ref_text)
        v = diode_diff_area(new, cell=args.cell)
        if not v or v <= 0:
            print(f"ERROR: {args.cell} ANTENNADIFFAREA still not positive (={v})", file=sys.stderr)
            return 1
        ga = sc_gate_area_count(new)
        if ref_text is not None and ga == 0:
            print("ERROR: pin-antenna merge produced no ANTENNAGATEAREA entries", file=sys.stderr)
            return 1
        open(args.out, "w", encoding="utf-8").write(new)
        print(f"SC LEF: {args.cell} ANTENNADIFFAREA = {v}; std-cell ANTENNAGATEAREA pins = {ga}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
