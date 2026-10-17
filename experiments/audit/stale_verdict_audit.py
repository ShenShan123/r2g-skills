"""How many "no verdict" designs actually have a completed report on disk?

result.json is the verdict of record, and for C03506 it says
drc_status=timeout / violations=0 while a 6_drc.lyrdb sits beside it holding 10
violations -- written a day later, against the SAME unmodified GDS, and matching
rule-for-rule what the official runner independently found.

If that is common, the baseline understates itself: those designs are counted as
failures for having no verdict when a verdict exists. The fix is not to trust
the file blindly either -- a report written against a DIFFERENT GDS is not that
design's verdict -- so the GDS mtime gates it.
"""
import collections
import glob
import json
from pathlib import Path

D = Path("/proj/workarea/yangao")
CORP = {"nangate45": "n45_full", "gf180": "gf180_full", "sky130hd": "full_out"}
dev = {l.strip() for l in open(D / "splits/dev.txt") if l.strip()}
NOVERDICT = ("timeout", "drc_failed", None, "skipped")

for plat, root in CORP.items():
    tot = recov = stale_gds = none = 0
    examples = []
    for f in glob.glob(str(D / root / "runs" / "*" / "result.json")):
        rd = Path(f).parent
        if "__".join(rd.name.split("__")[:-2]) not in dev:
            continue
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if d.get("drc_status") not in NOVERDICT:
            continue
        tot += 1
        reports = sorted(glob.glob(str(rd / "*.lyrdb")) +
                         glob.glob(str(rd / "reports" / "*.lyrdb")))
        if not reports:
            none += 1
            continue
        gds = rd / "6_final.gds"
        if not gds.is_file():
            none += 1
            continue
        tg = gds.stat().st_mtime
        # a report must be NEWER than the layout it judges
        ok = [r for r in reports if Path(r).stat().st_mtime >= tg]
        if ok:
            recov += 1
            if len(examples) < 3:
                examples.append((rd.name, Path(ok[0]).name))
        else:
            stale_gds += 1
    print("%-10s 无判决 %4d 份：磁盘上有可用报告 %3d  报告早于版图(作废) %3d  "
          "完全没有 %3d" % (plat, tot, recov, stale_gds, none))
    for n, r in examples:
        print("      例: %s  <- %s" % (n[:52], r))
