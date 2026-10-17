"""Which frozen parameters were never checked against the corpus?

Period, utilization, the area allowance, uncertainty, I/O and the pass condition
all have measurements behind them. These four do not -- they were inherited or
set from a single instruction -- so this asks whether they BIND: a parameter that
never binds needs no justification, and one that binds on hundreds of designs
needs one.
"""
import collections
import glob
import json
from pathlib import Path

D = Path("/proj/workarea/yangao")
CORP = {"nangate45": "n45_full", "gf180": "gf180_full", "sky130hd": "full_out"}
dev = {l.strip() for l in open(D / "splits/dev.txt") if l.strip()}

print("=== 1. 阶段超时（现定 6 小时）在试跑里卡住过多少设计\n")
for plat, root in CORP.items():
    secs, to, n = [], 0, 0
    for f in glob.glob(str(D / root / "runs" / "*" / "result.json")):
        if "__".join(Path(f).parent.name.split("__")[:-2]) not in dev:
            continue
        try:
            d = json.load(open(f))
        except Exception:
            continue
        n += 1
        s = d.get("seconds")
        if isinstance(s, (int, float)):
            secs.append(s)
        if (d.get("failure_class") or "") == "timeout":
            to += 1
    secs.sort()
    over = {t: sum(1 for x in secs if x > t) for t in (3600, 10800, 21600)}
    print("  %-10s 受试 %d  流程耗时 中位 %.0fs p99 %.0fs 最大 %.0fs"
          % (plat, n, secs[len(secs)//2], secs[int(len(secs)*.99)], secs[-1]))
    print("            超过 1h/3h/6h 的设计数: %s   被判 timeout 的 %d 份"
          % (over, to))

print("\n=== 2. 单设计内存上限（现定 30 GB）在试跑里卡住过多少\n")
for plat, root in CORP.items():
    mb = []
    oom = 0
    for f in glob.glob(str(D / root / "runs" / "*" / "result.json")):
        if "__".join(Path(f).parent.name.split("__")[:-2]) not in dev:
            continue
        try:
            d = json.load(open(f))
        except Exception:
            continue
        v = d.get("peak_disk_bytes")
        if (d.get("failure_class") or "") == "oom":
            oom += 1
    print("  %-10s 被判 oom 的 %d 份" % (plat, oom))

print("\n=== 3. LVS 这一侧：各平台用什么检查器、无判决多少\n")
for plat, root in CORP.items():
    chk = collections.Counter()
    st = collections.Counter()
    for f in glob.glob(str(D / root / "runs" / "*" / "result.json")):
        if "__".join(Path(f).parent.name.split("__")[:-2]) not in dev:
            continue
        try:
            d = json.load(open(f))
        except Exception:
            continue
        chk[d.get("lvs_checker")] += 1
        st[d.get("lvs_status")] += 1
    print("  %-10s 检查器 %s" % (plat, dict(chk.most_common(2))))
    print("            判决 %s" % dict(st.most_common(6)))
