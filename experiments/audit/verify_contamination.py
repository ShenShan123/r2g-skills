"""Did my own re-runs change any verdict in the corpus they measured?

Three test scripts wrote into the corpus through symlinks: 15 designs' 6_drc.lyrdb
were rewritten by the DRC timing measurement. Same deck, same GDS, so the
verdicts SHOULD be identical -- but "should" is not "checked", and the recovery
control that passed 1029 of 1029 ran BEFORE those rewrites. A difference now
would look like evidence against the recovery rather than like contamination I
introduced, which is exactly the confusion worth spending five minutes to avoid.

Compares each rewritten report's clean/not-clean against the result.json verdict
recorded before the rewrite.
"""
import glob
import json
import re
from pathlib import Path

D = Path("/proj/workarea/yangao")
ITEM = re.compile(r"<item>")
sel = D / "_n45timing.select"
if not sel.is_file():
    raise SystemExit("缺 %s" % sel)
names = [l.strip() for l in sel.read_text().splitlines() if l.strip()]

# When did the measurement that rewrites these start? Read it, never guess.
#
# The first version of this check compared each report's mtime against a
# HARDCODED epoch I made up, concluded all 15 had been rewritten, and reported
# "no verdict changed" -- while stat showed the files still dated 2026-10-09,
# i.e. still the originals. The check had verified the originals against
# result.json: the recovery control run a second time, not the contamination
# question at all. A control is only as good as the field it reads, and a field
# I invent is not a field.
_log = D / "n45_drc_timing.log"
START = _log.stat().st_mtime if _log.is_file() else None
import datetime as _dt
if START:
    # the log's FIRST line carries the start; its mtime moves as it is appended
    for _l in _log.read_text().splitlines():
        if "DRC 计时" in _l:
            try:
                START = _dt.datetime.strptime(
                    _l.split()[0] + " " + _l.split()[1],
                    "%Y-%m-%d %H:%M:%S").timestamp()
            except Exception:                                    # noqa: BLE001
                pass
            break
print("测量开始于 %s；只有晚于它的报告才是被重写的\n"
      % (_dt.datetime.fromtimestamp(START).strftime("%Y-%m-%d %H:%M:%S")
         if START else "(读不到，无法区分)"))

print("计时测量重写过的 %d 份设计，核对判决是否仍一致\n" % len(names))
print("%-46s %12s %8s %8s" % ("设计", "记录判决", "报告违例", "一致"))
agree = dis = skip = 0
for n in names:
    rd = D / "n45_full/runs" / n
    rep = rd / "6_drc.lyrdb"
    try:
        st = json.loads((rd / "result.json").read_text()).get("drc_status")
    except Exception:
        skip += 1
        continue
    if not rep.is_file():
        print("%-46s %12s %8s %8s" % (n[:46], st, "(无报告)", "跳过"))
        skip += 1
        continue
    k = len(ITEM.findall(rep.read_text(errors="replace")))
    rewritten = bool(START) and rep.stat().st_mtime > START
    if not rewritten:
        print("%-46s %12s %8d %8s" % (n[:46], st, k, "原件·未验"))
        skip += 1
        continue
    want_clean = (st == "clean")
    ok = want_clean == (k == 0)
    agree += ok
    dis += (not ok)
    print("%-46s %12s %8d %8s" % (n[:46], st, k, "是" if ok else "**否**"))
print()
print("一致 %d  不一致 %d  跳过 %d" % (agree, dis, skip))
if dis:
    print("\n有不一致 -- 同一个 deck 同一个 GDS 跑出不同判决，说明存在不确定性，")
    print("这本身是个需要解释的发现，不要当成污染一笔带过。")
elif agree:
    print("\n被重写的那 %d 份判决未变；1029/1029 那个对照仍然成立。" % agree)
else:
    print("\n还没有任何一份被重写 -- 污染问题仍然开着，测量跑完后再跑这个检查。")
