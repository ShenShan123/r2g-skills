"""Redo the recovery control against the field that actually carries the verdict.

The first attempt compared result.json's `drc_violations` with the count read
from the lyrdb and found 273 of 1029 disagreeing -- which looked like two
different rule sets. It was not. On C00006, result.json holds
drc_status='violations' with drc_violations=0 while the log says "DRC
completed: 35 violations": the COUNT field is broken, the STATUS field is not.
A control is only as good as the field it reads.

So this compares the two as VERDICTS -- clean vs not-clean -- which is what the
baseline is actually counted on.
"""
import collections
import glob
import json
import re
from pathlib import Path

D = Path("/proj/workarea/yangao")
dev = {l.strip() for l in open(D / "splits/dev.txt") if l.strip()}
ITEM = re.compile(r"<item>")

nonzero = 0
agree = 0
dis = collections.Counter()
examples = []
for f in glob.glob(str(D / "gf180_full/runs/*/result.json")):
    rd = Path(f).parent
    if "__".join(rd.name.split("__")[:-2]) not in dev:
        continue
    try:
        d = json.load(open(f))
    except Exception:
        continue
    if (d.get("drc_violations") or 0) > 0:
        nonzero += 1
    st = d.get("drc_status")
    if st not in ("clean", "violations"):
        continue
    rep, gds = rd / "6_drc.lyrdb", rd / "6_final.gds"
    if not (rep.is_file() and gds.is_file()):
        continue
    if rep.stat().st_mtime < gds.stat().st_mtime:
        continue
    n = len(ITEM.findall(rep.read_text(errors="replace")))
    want_clean = (st == "clean")
    got_clean = (n == 0)
    if want_clean == got_clean:
        agree += 1
    else:
        dis[(st, "lyrdb=%d" % (0 if n == 0 else 1))] += 1
        if len(examples) < 5:
            examples.append((rd.name, st, n))

print("整个 dev 的 gf180 语料里 drc_violations > 0 的设计: %d 份" % nonzero)
print("  （若为 0，这个字段整列无效，只有 drc_status 可用）\n")
tot = agree + sum(dis.values())
print("对照（按判决，不按计数）: %d 份" % tot)
print("  一致: %d (%.1f%%)" % (agree, 100 * agree / tot if tot else 0))
print("  不一致: %d  %s" % (sum(dis.values()), dict(dis)))
for n, s, c in examples:
    print("     %s  状态=%s  报告违例=%d" % (n[:46], s, c))
