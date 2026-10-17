"""The 27 designs where LVS fails but DRC passes, median 7657 cells.

Size explains the 61 designs that fail both checkers. It does not explain these:
they are small enough that 1033 designs their size have a 1% no-verdict rate.
So there is a second cause, and calling the LVS problem "a scale limit" was half
wrong -- correct for the 61, unexplained for these.
"""
import collections
import glob
import json
import re
from pathlib import Path

D = Path("/proj/workarea/yangao")
dev = {l.strip() for l in open(D / "splits/dev.txt") if l.strip()}

rows = []
for f in glob.glob(str(D / "n45_full/runs/*/result.json")):
    rd = Path(f).parent
    if "__".join(rd.name.split("__")[:-2]) not in dev:
        continue
    try:
        d = json.load(open(f))
    except Exception:
        continue
    if d.get("lvs_status") not in ("timeout", "lvs_failed"):
        continue
    if d.get("drc_status") in ("timeout", "drc_failed"):
        continue                      # the overlap set, already explained
    rows.append((d.get("cells") or 0, rd, d))

rows.sort()
print("只有 LVS 无判决的 %d 份（DRC 判出来了）\n" % len(rows))
buckets = collections.Counter()
samples = {}
for c, rd, d in rows:
    log = rd / "lvs_signoff.log"
    if not log.is_file():
        buckets["没有日志"] += 1
        continue
    t = log.read_text(errors="replace")
    key = None
    for pat, name in (
            (r"No rule to make target", "make 找不到目标（stage chain）"),
            (r"Timeout|timed out|Terminated", "超时/被杀"),
            (r"out of memory|Killed|bad_alloc", "内存"),
            (r"cdl|CDL", "CDL 相关"),
            (r"ERROR|Error:", "其它 ERROR"),
    ):
        if re.search(pat, t, re.I):
            key = name
            break
    key = key or "无可识别错误"
    buckets[key] += 1
    if key not in samples:
        tail = [l for l in t.strip().splitlines() if l.strip()][-4:]
        samples[key] = (c, rd.name, tail)

for k, v in buckets.most_common():
    print("  %3d  %s" % (v, k))
print("\n每类样本:")
for k, (c, n, tail) in samples.items():
    print("\n  [%s]  %d 单元  %s" % (k, c, n[:44]))
    for l in tail:
        print("      %s" % l[:108])
