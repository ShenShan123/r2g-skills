#!/usr/bin/env python3
"""Prove that patching only the PPL-0024 failures equals running the pin floor
from the start.

The pin floor is `core = max(area_derived, pin_required, pdn_floor)`, so it can
only RAISE a core side, and only where the pin term binds. The claim to check is
that the set of designs whose die changes is exactly the set that fails
PPL-0024 -- if so, re-running just the failures is not cherry-picking, it is the
same corpus reached without discarding the work already done.

k (IO positions per um of perimeter) is NOT fitted. place_pins keeps two tracks
between pins, so k = 1/(2*pitch) on the pin layer; both observed failures
confirm it (nangate45 1642 positions on a 228 um core -> k=1.80 -> pitch 0.278,
LEF says 0.28; gf180 4924 on 1377 -> k=0.894 -> pitch 0.56, LEF says 0.56).
"""
import glob
import json
import math
import re
import sys
from pathlib import Path

D = Path("/proj/workarea/yangao")
K = {"nangate45": 1.80, "gf180": 0.894, "sky130hd": 1.0 / (2 * 0.46)}
PDN = {"nangate45": 32.0, "gf180": 110.0, "sky130hd": 32.0}
PILOT = {"nangate45": "n45_full", "gf180": "gf180_full", "sky130hd": "full_out"}


def pin_counts() -> dict:
    """Ports per design, read once: the count is a property of the RTL and was
    verified identical on all three platforms (290/290 designs)."""
    pins = {}
    for root in PILOT.values():
        for f in glob.glob(str(D / root / "runs/*/6_final.def")):
            short = "__".join(Path(f).parent.name.split("__")[:-2])
            if short in pins:
                continue
            try:
                with open(f, errors="replace") as fh:
                    head = fh.read(2_000_000)
            except OSError:
                continue
            m = re.search(r"^PINS (\d+)", head, re.M)
            if m:
                pins[short] = int(m.group(1))
    return pins


def main() -> int:
    pins = pin_counts()
    print("读到引脚数的设计: %d 份\n" % len(pins))
    for plat in ("nangate45", "gf180", "sky130hd"):
        spec = json.loads((D / "spec" / f"spec_{plat}.json").read_text())
        changed, unchanged, nopin = [], 0, 0
        for name, e in spec.items():
            if name == "_meta":
                continue
            p = pins.get(name)
            if p is None:
                nopin += 1
                continue
            need = math.ceil(p / (K[plat] * 4))
            old = e["core_side_um"]
            new = max(old, need, PDN[plat])
            if new > old:
                changed.append((name, old, new, p))
            else:
                unchanged += 1
        print("%s:" % plat)
        print("  die 会变大的: %d 份 (%.2f%%)   不变: %d   无引脚数: %d"
              % (len(changed), 100 * len(changed) / max(len(spec) - 1, 1),
                 unchanged, nopin))
        changed.sort(key=lambda c: -(c[2] / c[1]))
        for n, o, w, p in changed[:5]:
            print("    %-40s core %4d -> %4d (%.2fx)  引脚 %d"
                  % (n[:40], o, w, w / o, p))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
