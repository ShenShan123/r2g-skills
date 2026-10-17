"""Pick a design with NON-ANTENNA violations for the deck-split equivalence test.

The first attempt used the smallest design with any violations. All of them were
METAL5_ANTENNA, so with ANTENNA=false the comparison became 0 vs 0 -- which
EXPERIMENT_RUNBOOK_zh.md already records as no evidence at all: "0 -> 0
consistency proves nothing, because the violation never appeared, so it cannot
be observed whether it changed."

The split is a clean decomposition only if the NON-antenna violations are
identical with the antenna block on and off, so the test needs a design that
HAS some.
"""
import collections
import glob
import json
import re
from pathlib import Path

D = Path("/proj/workarea/yangao")
dev = {l.strip() for l in open(D / "splits/dev.txt") if l.strip()}
CAT = re.compile(r"<category>\s*'?([^'<]+)'?\s*</category>")
ITEM = re.compile(r"<item>(.*?)</item>", re.S)

cands = []
for f in glob.glob(str(D / "n45_full/runs/*/result.json")):
    rd = Path(f).parent
    if "__".join(rd.name.split("__")[:-2]) not in dev:
        continue
    rep, gds = rd / "6_drc.lyrdb", rd / "6_final.gds"
    if not (rep.is_file() and gds.is_file()):
        continue
    if rep.stat().st_mtime < gds.stat().st_mtime:
        continue
    txt = rep.read_text(errors="replace")
    cats = collections.Counter()
    for it in ITEM.findall(txt):
        m = CAT.search(it)
        cats[m.group(1).strip() if m else "?"] += 1
    nonant = {k: v for k, v in cats.items() if "ANTENNA" not in k.upper()}
    if not nonant:
        continue
    try:
        cells = json.load(open(f)).get("cells") or 0
    except Exception:
        cells = 0
    cands.append((sum(nonant.values()), cells, rd.name, dict(nonant)))

cands.sort(key=lambda t: (t[1], -t[0]))      # small first, violations second
print("dev 里有非天线违例的设计: %d 份\n" % len(cands))
for n, c, name, d in cands[:8]:
    print("  %6d 单元  非天线违例 %3d  %s" % (c, n, name[:46]))
    print("          %s" % d)
if cands:
    Path(D / "_equiv_pick.txt").write_text(cands[0][2] + "\n")
    print("\n选中: %s" % cands[0][2])
