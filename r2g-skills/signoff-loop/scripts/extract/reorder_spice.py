#!/usr/bin/env python3
"""Reorder a KLayout-written SPICE netlist bottom-up, for Netgen.

KLayout's NetlistSpiceWriter emits circuits top-down, so every subcircuit is
called before it is defined. Netgen then creates a placeholder cell from the
first call ("Call to undefined subcircuit ... Creating placeholder") and later
reports "Pin count mismatch between cell and instance". Reversing the block
order gives a definition-before-use netlist; the blocks themselves are copied
byte for byte.

Two textual normalisations, both so that Netgen reads the names KLayout meant:
  * backslash escapes are removed ("\$2" -> "$2", "a\[0\]" -> "a[0]"):
    Netgen does not read KLayout's escapes and merged "\$2 A" into one token,
    which shifted every terminal of the device that followed.
  * every line is upper-cased: KLayout's CDL reader upper-cases the schematic
    while the extracted layout keeps the GDS label case, and KLayout's own
    comparer matches names case-INsensitively -- the baseline ruler, so the
    fallback must not be stricter than it on case.

Prints the (upper-cased) name of the circuit matching --top.

Usage: reorder_spice.py <in> <out> --top <design_name>
"""
import re
import sys


def main() -> int:
    src, dst = sys.argv[1], sys.argv[2]
    top = sys.argv[sys.argv.index("--top") + 1]
    head, blocks, cur = [], [], None
    for line in open(src, errors="replace"):
        line = re.sub(r"\\(.)", r"\1", line).upper()
        if re.match(r"(?i)^\.subckt\s", line):
            cur = [line]
            continue
        if cur is None:
            head.append(line)
            continue
        cur.append(line)
        if re.match(r"(?i)^\.ends\b", line):
            blocks.append(cur)
            cur = None
    if cur is not None:
        print("unterminated .SUBCKT", file=sys.stderr)
        return 2
    names = [re.split(r"\s+", b[0].strip())[1] for b in blocks]
    hit = [n for n in names if n.lower() == top.lower()]
    if len(hit) != 1:
        print("top %r found %d times" % (top, len(hit)), file=sys.stderr)
        return 3
    with open(dst, "w") as f:
        f.writelines(head)
        for b in reversed(blocks):
            f.writelines(b)
    print(hit[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
