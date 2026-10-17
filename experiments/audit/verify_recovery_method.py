"""Control for the verdict recovery: does reading the lyrdb reproduce the
verdicts that result.json ALREADY has?

Recovering 75 dual-clean designs from reports on disk changes the gf180
baseline by 10%, and "I found a file" is not evidence that the file is that
design's verdict. The control is the designs where BOTH exist: if reading the
lyrdb reproduces result.json's recorded violation count on those, the same
reading is trustworthy where result.json is missing one. If it does not, the
recovery is invalid and the 75 stay failures.
"""
import collections
import glob
import json
import re
from pathlib import Path

D = Path("/proj/workarea/yangao")
dev = {l.strip() for l in open(D / "splits/dev.txt") if l.strip()}
ITEM = re.compile(r"<item>")

agree = disagree = 0
examples = []
for f in glob.glob(str(D / "gf180_full/runs/*/result.json")):
    rd = Path(f).parent
    if "__".join(rd.name.split("__")[:-2]) not in dev:
        continue
    try:
        d = json.load(open(f))
    except Exception:
        continue
    # only designs where result.json HAS a verdict
    if d.get("drc_status") not in ("clean", "violations"):
        continue
    rep = rd / "6_drc.lyrdb"
    gds = rd / "6_final.gds"
    if not (rep.is_file() and gds.is_file()):
        continue
    if rep.stat().st_mtime < gds.stat().st_mtime:
        continue
    n = len(ITEM.findall(rep.read_text(errors="replace")))
    recorded = d.get("drc_violations")
    if recorded == n:
        agree += 1
    else:
        disagree += 1
        if len(examples) < 6:
            examples.append((rd.name, recorded, n, d.get("drc_status")))

tot = agree + disagree
print("对照：result.json 有判决、磁盘也有报告的设计 %d 份" % tot)
if tot == 0:
    print("  没有可对照的设计 -- 恢复方法无法验证，那 75 份不能计入")
else:
    print("  读 lyrdb 得到的违例数与 result.json 一致: %d 份 (%.1f%%)"
          % (agree, 100 * agree / tot))
    print("  不一致: %d 份" % disagree)
    for n, r, g, s in examples:
        print("     %s  记录=%s 读出=%s (%s)" % (n[:46], r, g, s))
    if disagree == 0:
        print("\n  -> 恢复方法在已知判决上 100%% 复现，可用于补齐缺失的判决")
    else:
        print("\n  -> 有不一致，恢复方法不可直接采信；先解释这 %d 份" % disagree)
