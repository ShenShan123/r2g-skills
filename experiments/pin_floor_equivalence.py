#!/usr/bin/env python3
"""On the designs nangate45 has ALREADY run, does "die would grow under the pin
floor" coincide with "failed PPL-0024"?

If the grow-set contains designs that PASSED, the two plans are not equivalent:
patching only the failures would leave those alone, while running the floor from
the start would have enlarged their die for no reason. That would mean k is too
conservative and must be recalibrated before anything is changed.
"""
import glob
import json
import math
import re
import time
from pathlib import Path

D = Path("/proj/workarea/yangao")
K = 1.80            # nangate45: 1/(2*0.28 um M3 pitch); confirmed by two failures
PDN = 32.0
R = D / "rerun_nangate45_20261018" / "runs"


def load(p):
    try:
        return json.loads(Path(p).read_text())
    except Exception:                                            # noqa: BLE001
        time.sleep(0.2)
        try:
            return json.loads(Path(p).read_text())
        except Exception:                                        # noqa: BLE001
            return None


# Pin count is a property of the RTL, so it is the same on every platform, and
# pooling the three pilot roots covers more designs than nangate45 alone (1156
# of 2331). That wider coverage is why this script reported 15 raised designs
# while make_spec_table.py, which reads only the platform's own pilot root,
# reports 8: both are right about their own population and the two numbers are
# simply not comparable. PLATFORM_ROOTS below is therefore the knob -- set it to
# ("n45_full",) to reproduce the generator's count exactly.
PLATFORM_ROOTS = ("n45_full", "gf180_full", "full_out")

pins = {}
for root in PLATFORM_ROOTS:
    for f in glob.glob(str(D / root / "runs/*/6_final.def")):
        s = "__".join(Path(f).parent.name.split("__")[:-2])
        if s in pins:
            continue
        try:
            with open(f, errors="replace") as fh:
                head = fh.read(2_000_000)
        except OSError:
            continue
        m = re.search(r"^PINS (\d+)", head, re.M)
        if m:
            pins[s] = int(m.group(1))

spec = json.loads((D / "spec" / "spec_nangate45.json").read_text())
grow_pass, grow_fail, nogrow_ppl, done = [], [], [], 0
for f in glob.glob(str(R / "*/result.json")):
    d = load(f)
    if not d:
        continue
    done += 1
    rd = Path(f).parent
    s = "__".join(rd.name.split("__")[:-2])
    e = spec.get(s)
    p = pins.get(s)
    if not e or p is None:
        continue
    need = math.ceil(p / (K * 4))
    # Only the PIN term counts as growth here. Writing
    # max(core, need, PDN) > core also fires when the PDN floor binds, which
    # would attribute a PDN-floored die to the pin floor.
    grows = need > e["core_side_um"]
    ppl = False
    for g in sorted(glob.glob(str(rd / "logs/*/*/*/*.log")),
                    key=lambda x: -Path(x).stat().st_mtime)[:3]:
        try:
            if "PPL-0024" in Path(g).read_text(errors="replace"):
                ppl = True
                break
        except OSError:
            pass
    if grows and d.get("status") == "pass":
        grow_pass.append((s, e["core_side_um"], need, p))
    elif grows:
        grow_fail.append((s, ppl))
    elif ppl:
        nogrow_ppl.append(s)

print("已跑完 %d 份" % done)
print("  die 会变大 且 已失败 : %d  (其中 PPL-0024 %d)"
      % (len(grow_fail), sum(1 for _, x in grow_fail if x)))
print("  die 会变大 但 通过了 : %d   <<< 这个数必须是 0" % len(grow_pass))
print("  die 不变 但 PPL 失败 : %d   <<< 这个数也必须是 0" % len(nogrow_ppl))
for s, old, need, p in grow_pass[:8]:
    print("     通过却会被放大: %-38s core %d -> %d  引脚 %d" % (s[:38], old, need, p))
for s in nogrow_ppl[:5]:
    print("     失败但不会被修: %s" % s[:50])
