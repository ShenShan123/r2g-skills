"""Why does nangate45's LVS fail on 88 dev designs when gf180's fails on 0?

Read-only: the logs are already on disk. The answer decides what the number
MEANS -- a stricter checker failing honestly, or a checker failing on its own
limits and taking 88 designs' verdicts with it. Those readings have opposite
implications for the baseline, so the distinction cannot be assumed.

nangate45 uses r2g's FreePDK45.lylvs through klayout; the other two use netgen
with the PDK's own setup.
"""
import collections
import glob
import json
import re
from pathlib import Path

D = Path("/proj/workarea/yangao")
dev = {l.strip() for l in open(D / "splits/dev.txt") if l.strip()}

buckets = collections.Counter()
samples = {}
sizes = []
for f in glob.glob(str(D / "n45_full/runs/*/result.json")):
    rd = Path(f).parent
    if "__".join(rd.name.split("__")[:-2]) not in dev:
        continue
    try:
        d = json.load(open(f))
    except Exception:
        continue
    if d.get("lvs_status") != "lvs_failed":
        continue
    sizes.append(d.get("cells") or 0)
    log = rd / "lvs_signoff.log"
    if not log.is_file():
        buckets["(没有日志)"] += 1
        continue
    t = log.read_text(errors="replace")
    # classify by the first thing that looks like a cause
    key = None
    for pat, name in (
            (r"Timeout|timed out|Terminated", "超时/被杀"),
            (r"No such file|not found|cannot open", "找不到文件"),
            (r"out of memory|Killed|bad_alloc", "内存"),
            (r"syntax error|parse error", "解析错误"),
            (r"Circuit .*not found|no circuit", "电路缺失"),
            (r"ERROR|Error:", "其它 ERROR"),
    ):
        if re.search(pat, t, re.I):
            key = name
            break
    key = key or "日志里没有可识别的错误"
    buckets[key] += 1
    if key not in samples:
        tail = [l for l in t.strip().splitlines() if l.strip()][-3:]
        samples[key] = (rd.name, tail)

print("nangate45 dev 里 lvs_failed 的 %d 份，按日志里的原因分类:\n"
      % sum(buckets.values()))
for k, v in buckets.most_common():
    print("  %4d  %s" % (v, k))
if sizes:
    sizes.sort()
    print("\n  单元数: 中位 %d  最小 %d  最大 %d" % (sizes[len(sizes)//2], sizes[0], sizes[-1]))
print("\n每类一个样本的日志尾:")
for k, (n, tail) in samples.items():
    print("\n  [%s] %s" % (k, n[:50]))
    for l in tail:
        print("      %s" % l[:110])
