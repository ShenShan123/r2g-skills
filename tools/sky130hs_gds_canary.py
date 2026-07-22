#!/usr/bin/env python3
"""Geometry canary for the sky130hs KLayout lefdef import (RMD-P0-04).

Why (failure-patterns.md #33, three-platform pilot 2026-07-22): an unpatched
sky130hs.lyt carries LEGACY lefdef reader option names, so KLayout's DEF→GDS
merge SILENTLY drops every DEF-derived shape — routing wires, vias, pin rects,
special (power) routing. Magic then extracts a portless top subckt and every
Netgen LVS verdict is invalid. Tool presence alone cannot see this: the pilot's
ENV gate passed while all four sky130hs fixtures produced unusable GDS.

This canary proves the import path END TO END instead of trusting the option
names: it writes a tiny synthetic DEF containing exactly the geometry classes
the defect loses — a signal pin with port geometry, a routed signal net, a via,
and special (power) routing — imports it through the PLATFORM'S OWN .lyt
reader options with KLayout in batch mode, and verifies each class landed on
its CANONICAL sky130A GDS layer/datatype number (met1 68/20, met2 69/20,
met2.pin 69/16, met4 71/20, via 68/44). The failure mode is NOT "no shapes":
the legacy options still emit geometry, but on meaningless legacy numbers
(met1 -> 1/0, pin -> 3/2, …) that Magic's sky130A tech cannot map — which is
exactly how the electrical top-level geometry is lost. Counting shapes by name
would pass both ways; only the number check separates them.

Usage:
  python3 tools/sky130hs_gds_canary.py [--platform sky130hs] [--flow-dir DIR]
                                       [--lyt PATH]   # override the .lyt probed

Exit codes: 0 = geometry survives (canary PASS); 2 = one or more DEF geometry
classes dropped (legacy .lyt — run tools/patch_sky130hs_lyt.py, regenerate GDS);
3 = environment missing (no klayout / no .lyt) — cannot verify, treat as NOT
ready when gating a strict campaign.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

# Minimal tech LEF: gives the DEF reader layer definitions + default routing
# widths + a cut geometry for the via. Only what the canary DEF references.
CANARY_LEF = """\
VERSION 5.8 ;
BUSBITCHARS "[]" ;
DIVIDERCHAR "/" ;
UNITS
  DATABASE MICRONS 1000 ;
END UNITS
LAYER met1
  TYPE ROUTING ;
  DIRECTION HORIZONTAL ;
  WIDTH 0.14 ;
  PITCH 0.34 ;
END met1
LAYER via
  TYPE CUT ;
END via
LAYER met2
  TYPE ROUTING ;
  DIRECTION VERTICAL ;
  WIDTH 0.14 ;
  PITCH 0.46 ;
END met2
LAYER met4
  TYPE ROUTING ;
  DIRECTION VERTICAL ;
  WIDTH 0.30 ;
  PITCH 0.92 ;
END met4
VIA via_canary DEFAULT
  LAYER met1 ;
    RECT -0.1 -0.1 0.1 0.1 ;
  LAYER via ;
    RECT -0.075 -0.075 0.075 0.075 ;
  LAYER met2 ;
    RECT -0.1 -0.1 0.1 0.1 ;
END via_canary
END LIBRARY
"""

# The DEF carries one of each geometry class the legacy .lyt drops:
# a PIN with LAYER port geometry, a routed NET (met1 + via + met2), and a
# SPECIALNET power stripe with explicit width.
CANARY_DEF = """\
VERSION 5.8 ;
DIVIDERCHAR "/" ;
BUSBITCHARS "[]" ;
DESIGN canary ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 10000 10000 ) ;
PINS 1 ;
- clk + NET clk + DIRECTION INPUT + USE SIGNAL
  + LAYER met2 ( -140 -500 ) ( 140 500 )
  + PLACED ( 5000 500 ) N ;
END PINS
SPECIALNETS 1 ;
- VPWR + USE POWER
  + ROUTED met4 600 ( 500 2000 ) ( 9500 2000 ) ;
END SPECIALNETS
NETS 1 ;
- clk ( PIN clk )
  + ROUTED met1 ( 5000 5000 ) ( 8000 5000 )
    NEW met2 ( 5000 1000 ) ( 5000 5000 )
    NEW met2 ( 5000 5000 ) via_canary ;
END NETS
END DESIGN
"""

# Runs INSIDE klayout -b. Reads the .lyt's lefdef reader options verbatim
# (the exact options ORFS's def2stream merge uses), adds only the canary LEF
# as input, imports the DEF, and reports polygon counts per geometry class.
CANARY_PYA = """\
import json
import pya

lyt_path = globals().get("lyt_path")
lef_path = globals().get("lef_path")
def_path = globals().get("def_path")
out_path = globals().get("out_path")

tech = pya.Technology()
tech.load(lyt_path)
opts = tech.load_layout_options
cfg = opts.lefdef_config
cfg.lef_files = [lef_path]
cfg.read_lef_with_def = False
opts.lefdef_config = cfg

layout = pya.Layout()
layout.read(def_path, opts)

top = layout.top_cell()
counts = {}
for li in layout.layer_indexes():
    info = layout.get_info(li)
    n = 0
    it = top.begin_shapes_rec(li)
    while not it.at_end():
        if it.shape().is_box() or it.shape().is_polygon() or it.shape().is_path():
            n += 1
        it.next()
    if n:
        counts[str(info)] = n

with open(out_path, "w") as f:
    json.dump(counts, f, indent=1)
"""


# Canonical sky130A GDS (layer, datatype) numbers per geometry class — what
# Magic's sky130A tech maps. The legacy .lyt emits geometry on OTHER numbers
# (met1 -> 1/0, pin -> 3/2), so name-based counting passes both ways; only the
# canonical-number check separates patched from legacy.
EXPECTED = {
    "routing_met1": (68, 20),
    "routing_met2": (69, 20),
    "pin_met2": (69, 16),
    "special_met4": (71, 20),
    "via_met1_met2": (68, 44),
}


def classify(counts):
    """Sum shape counts per canonical (layer, datatype) class.

    KLayout's LayerInfo str is '<name> (L/D)' for mapped layers or 'L/D' for
    unmapped ones — parse the numeric pair out of either form.
    """
    import re

    by_num = {}
    for name, n in counts.items():
        m = re.search(r"\(?(\d+)/(\d+)\)?\s*$", name)
        if m:
            key = (int(m.group(1)), int(m.group(2)))
            by_num[key] = by_num.get(key, 0) + n
    out = {cls: by_num.get(pair, 0) for cls, pair in EXPECTED.items()}
    out["any"] = sum(counts.values())
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--platform", default="sky130hs")
    ap.add_argument("--flow-dir", default=os.environ.get("FLOW_DIR") or os.path.join(
        os.environ.get("ORFS_ROOT", ""), "flow"))
    ap.add_argument("--lyt", default=None, help="explicit .lyt to probe (overrides platform)")
    ap.add_argument("--keep", action="store_true", help="keep the scratch dir for debugging")
    args = ap.parse_args()

    lyt = args.lyt or os.path.join(args.flow_dir, "platforms", args.platform,
                                   f"{args.platform}.lyt")
    if not os.path.isfile(lyt):
        print(f"ERROR: .lyt not found: {lyt} (set ORFS_ROOT/FLOW_DIR or --lyt)",
              file=sys.stderr)
        return 3
    klayout = os.environ.get("KLAYOUT_CMD") or shutil.which("klayout")
    if not klayout:
        print("ERROR: klayout not found (KLAYOUT_CMD/PATH) — cannot verify the GDS "
              "import path", file=sys.stderr)
        return 3

    tmp = tempfile.mkdtemp(prefix="sky130hs_canary_")
    try:
        lef_path = os.path.join(tmp, "canary.lef")
        def_path = os.path.join(tmp, "canary.def")
        pya_path = os.path.join(tmp, "canary_impl.py")
        out_path = os.path.join(tmp, "counts.json")
        open(lef_path, "w").write(CANARY_LEF)
        open(def_path, "w").write(CANARY_DEF)
        open(pya_path, "w").write(CANARY_PYA)

        cmd = [klayout, "-b",
               "-rd", f"lyt_path={lyt}",
               "-rd", f"lef_path={lef_path}",
               "-rd", f"def_path={def_path}",
               "-rd", f"out_path={out_path}",
               "-r", pya_path]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0 or not os.path.isfile(out_path):
            print(f"ERROR: klayout canary import failed (rc={proc.returncode}):",
                  file=sys.stderr)
            print(proc.stdout[-2000:], file=sys.stderr)
            print(proc.stderr[-2000:], file=sys.stderr)
            return 3
        counts = json.load(open(out_path))
        classes = classify(counts)
        missing = [k for k in EXPECTED if not classes[k]]
        print(f"canary layer counts: {json.dumps(counts)}")
        print(f"geometry classes (canonical sky130A numbers): {json.dumps(classes)}")
        if missing:
            print(f"CANARY FAIL: DEF-derived geometry missing from its canonical "
                  f"sky130A layer for class(es): {', '.join(missing)} — legacy "
                  f"lefdef options in {lyt} emit unmappable layer numbers; run "
                  "tools/patch_sky130hs_lyt.py and regenerate GDS from finish "
                  "(failure-patterns #33 / RMD-P0-04)", file=sys.stderr)
            return 2
        print("CANARY PASS: routing, pin, special routing, and via geometry all "
              "land on their canonical sky130A GDS layers")
        return 0
    finally:
        if args.keep:
            print(f"scratch kept: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
