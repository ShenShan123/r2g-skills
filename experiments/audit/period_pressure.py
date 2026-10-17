"""How much extra silicon does a tighter period actually demand?

result.json's `cells`/`area_um2` are the SYNTHESIS numbers and they are
period-insensitive, because ORFS's yosys flow is not timing-driven -- which is
why a first look said the period applies no pressure. The pressure lands AFTER
placement: the resizer upsizes cells and inserts buffers to close timing, and
CTS builds a bigger clock tree for a faster clock.

DEF COMPONENTS cannot be read directly either: it includes FILL and TAPCELL,
which are inserted to satisfy density rules and MOVE OPPOSITE to real cells (more
logic leaves less room to fill). So real cells are counted with fill and tap
excluded.
"""
import re
import sys
from collections import Counter
from pathlib import Path

RUNS = Path("/proj/workarea/yangao/n45_p0/runs")
DESIGN = sys.argv[1] if len(sys.argv) > 1 else "C00059__update_state_for_neuron__flexman"
FILLTAP = re.compile(r"(FILLCELL|FILL_|TAPCELL|TAP_|DECAP|ANTENNA)", re.I)
COMP = re.compile(r"^\s*-\s+(\S+)\s+(\S+)")

print("设计 %s  平台 nangate45" % DESIGN)
print("%-8s %10s %10s %10s %10s %10s" %
      ("周期", "DEF总数", "填充/tap", "真实单元", "buffer", "面积um2"))
base = None
for per in ("1", "1.2", "1.5", "1.8", "2", "3"):
    d = RUNS / ("%s__nangate45__p%s" % (DESIGN, per))
    f = d / "6_final.def"
    if not f.is_file():
        continue
    masters = Counter()
    inside = False
    area = None
    for line in f.read_text(errors="replace").splitlines():
        if line.startswith("COMPONENTS"):
            inside = True
            continue
        if line.startswith("END COMPONENTS"):
            inside = False
            continue
        if line.startswith("DIEAREA"):
            n = [int(x) for x in re.findall(r"-?\d+", line)]
            if len(n) >= 4:
                area = abs(n[2] - n[0]) * abs(n[3] - n[1]) / 1e6   # dbu 1000/um
        if inside:
            m = COMP.match(line)
            if m:
                masters[m.group(2)] += 1
    tot = sum(masters.values())
    ft = sum(v for k, v in masters.items() if FILLTAP.search(k))
    real = tot - ft
    buf = sum(v for k, v in masters.items()
              if re.search(r"^(BUF_|CLKBUF_|INV_)", k, re.I))
    if base is None:
        base = (real, buf)
    print("%-8s %10d %10d %10d %10d %10s" % (per, tot, ft, real, buf, area))
print()
# compare tight vs loose
d1 = RUNS / ("%s__nangate45__p1" % DESIGN) / "6_final.def"
d3 = RUNS / ("%s__nangate45__p3" % DESIGN) / "6_final.def"
print("（最后一行是最松的周期；真实单元/buffer 相对它的增幅才是时序施加的压力）")
