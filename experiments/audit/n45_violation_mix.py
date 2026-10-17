"""What ARE the DRC violations on each platform?

Only two dev designs on nangate45 have a non-antenna violation, and each has
exactly one. If that holds at corpus scale then on nangate45 the antenna check
is essentially the whole DRC story, which matters three ways:

  * r2g ENABLED antenna checking there by supplying the LEF data the platform
    lacks (contribution A2). Without it nangate45's DRC would be nearly empty.
  * the same check is what costs 67 designs their verdict entirely.
  * so "split the deck to recover those 67" recovers a verdict that is mostly
    "clean except the one check that finds anything here".
"""
import collections
import glob
import re
from pathlib import Path

D = Path("/proj/workarea/yangao")
dev = {l.strip() for l in open(D / "splits/dev.txt") if l.strip()}
CAT = re.compile(r"<category>\s*'?([^'<]+)'?\s*</category>")
ITEM = re.compile(r"<item>(.*?)</item>", re.S)

for plat, root in (("nangate45", "n45_full"), ("sky130hd", "full_out"),
                   ("gf180", "gf180_full")):
    have = ant_only = non_ant = empty = 0
    allcats = collections.Counter()
    for f in glob.glob(str(D / root / "runs" / "*" / "result.json")):
        rd = Path(f).parent
        if "__".join(rd.name.split("__")[:-2]) not in dev:
            continue
        rep, gds = rd / "6_drc.lyrdb", rd / "6_final.gds"
        if not (rep.is_file() and gds.is_file()):
            continue
        if rep.stat().st_mtime < gds.stat().st_mtime:
            continue
        have += 1
        cats = collections.Counter()
        for it in ITEM.findall(rep.read_text(errors="replace")):
            m = CAT.search(it)
            cats[m.group(1).strip() if m else "?"] += 1
        allcats.update(cats)
        if not cats:
            empty += 1
        elif all("ANTENNA" in k.upper() for k in cats):
            ant_only += 1
        else:
            non_ant += 1
    print("%-10s 有报告 %4d 份：无违例 %4d  仅天线违例 %4d  含非天线违例 %4d"
          % (plat, have, empty, ant_only, non_ant))
    if allcats:
        tot = sum(allcats.values())
        ant = sum(v for k, v in allcats.items() if "ANTENNA" in k.upper())
        print("     违例条数合计 %d，其中天线 %d (%.1f%%)" % (tot, ant, 100 * ant / tot))
        for k, v in allcats.most_common(5):
            print("        %6d  %s" % (v, k))
