#!/usr/bin/env python3
"""Generate sky130 equivalent_pins, including the complex gates.

The deck's 40 entries all name NANGATE cells ("*AND2_1"), so sky130 has none.
A first pass covered only and/or/nand/nor/xor/xnor and deliberately skipped
complex gates, on the grounds that declaring ALL of a21oi's inputs
interchangeable would be wrong -- and it would: A1/A2 feed the AND stage and
swap with each other, B1 does not. That skip is why a21oi_2 still reported
NoMatch on two designs after the substrate and device-less fixes landed.

sky130's names carry the group sizes: a21oi is an AND2 into an OR with one more
input, so pins A1,A2 form one commutative group and B1 a group of one. Emit one
equivalent_pins call per group of size >= 2, and only for pins the liberty
actually declares, so a naming assumption cannot invent an equivalence that
would make LVS accept a wrong layout.

Patterns are emitted UPPERCASE. KLayout's CDL reader upcases every name, so the
schematic circuit is SKY130_FD_SC_HD__A21OI_2; lowercase patterns taken straight
from the liberty match nothing and change no verdict, which is what the deck's
original Nangate entries ("*AND2_1") were already telling us.
"""
import collections, pathlib, re, sys

lib = pathlib.Path(sys.argv[1]).read_text(errors="replace")

cells, cur, pin = {}, None, None
for line in lib.splitlines():
    m = re.match(r'\s*cell\s*\(\s*"?([^")\s]+)"?\s*\)', line)
    if m:
        cur, pin = m.group(1), None; cells[cur] = []; continue
    m = re.match(r'\s*pin\s*\(\s*"?([^")\s]+)"?\s*\)', line)
    if m and cur:
        pin = m.group(1); continue
    if cur and pin and re.match(r'\s*direction\s*:\s*"?input"?', line):
        cells[cur].append(pin); pin = None

SUPPLY = {"VPWR", "VGND", "VNB", "VPB"}
GROUP_LETTERS = "ABCD"
simple = re.compile(r"__(and|or|nand|nor|xor|xnor)(\d)(?:_|$)")
# a21oi, a22o, o31ai, a2bb2o, ... the digit run gives the group sizes
complexg = re.compile(r"__([ao])((?:\d|bb?)+)(oi|ai|o|a|i)?(?:_|$)")

out, skipped = [], collections.Counter()
for cell, pins in sorted(cells.items()):
    ins = sorted(p for p in pins if p not in SUPPLY)
    base = cell.split("__")[-1]
    m = simple.search(cell)
    if m:
        want = int(m.group(2))
        if len(ins) == want:
            out.append(f'equivalent_pins("*{base.upper()}", ' +
                       ", ".join(f'"{p}"' for p in ins) + ")")
        else:
            skipped[f"{m.group(1)}{want}:pins={len(ins)}"] += 1
        continue
    m = complexg.search(cell)
    if m:
        sizes = [int(c) for c in m.group(2) if c.isdigit()]
        emitted = 0
        for gi, size in enumerate(sizes):
            if size < 2 or gi >= len(GROUP_LETTERS):
                continue
            letter = GROUP_LETTERS[gi]
            group = [f"{letter}{k}" for k in range(1, size + 1)]
            # only if the liberty really declares every pin in the group
            if all(p in ins for p in group):
                out.append(f'equivalent_pins("*{base.upper()}", ' +
                           ", ".join(f'"{p}"' for p in group) + ")")
                emitted += 1
            else:
                skipped[f"{base}:group{letter}_absent"] += 1
        if emitted == 0:
            skipped[f"{base}:no_group"] += 1
        continue
    skipped[base.rstrip("0123456789_")] += 1

print(f"# {len(out)} equivalence declarations from "
      f"{pathlib.Path(sys.argv[1]).name}", file=sys.stderr)
print(f"# not declared: {dict(list(skipped.most_common(8)))}", file=sys.stderr)
for l in out:
    print(l)
