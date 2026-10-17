#!/usr/bin/env python3
"""Every condition that must hold before the frozen baseline re-run starts.

Each gate here exists because its absence produced a real defect on 2026-10-17,
and every one of those defects was silent -- the run would have completed and
produced plausible numbers answering a different question. Checking them by
memory is what failed; this checks them by machine.

Exit 0 = launch. Non-zero = do not launch, and the output says which gate and
why.

RUN IT WITHOUT A PIPE:

    python3 -I rerun_preflight_gates.py; echo $?          # right
    python3 -I rerun_preflight_gates.py | head -28; echo $?   # WRONG -- $? is head's

A pipeline reports its LAST command's status, so `| head` or `| tee` throws the
gate's verdict away and reports 0 while the gate printed "do not launch". That
happened on this script's first run, minutes after the same defect was fixed in
the preflight's conformance gate -- which had grepped the output's prose instead
of reading its exit code. Redirect to a file if the output needs keeping:

    python3 -I rerun_preflight_gates.py > gates.out 2>&1; echo $?

import glob
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

D = Path("/proj/workarea/yangao")
PLATS = ("nangate45", "gf180", "sky130hd")
FROZEN_UTIL = 0.50
SPLIT_SHA = {
    "dev": "1e5005104eafd96e8d24bf611e43c7a92cc08df53a8751c84b75fdcd0a58b486",
    "held_out": "215843425abf61e3e8d28e036c0447999e002fb6907e1965334f4b26fbf5f283",
}
fails = []


def gate(name, ok, detail):
    print("  [%s] %-42s %s" % ("PASS" if ok else "FAIL", name, detail))
    if not ok:
        fails.append(name)


print("=== 冻结 baseline 重跑：启动前闸门\n")

# 1. the split is the one that was frozen
for s, want in SPLIT_SHA.items():
    f = D / "splits" / ("%s.txt" % s)
    if not f.is_file():
        gate("划分 %s 存在" % s, False, "缺 %s" % f)
        continue
    got = hashlib.sha256(f.read_bytes()).hexdigest()
    gate("划分 %s sha256" % s, got == want,
         "%d 份  %s" % (len(f.read_text().split()), got[:16] + "…"))

# 2. the spec table was built with the utilization we are about to run
for p in PLATS:
    f = D / "spec" / ("spec_%s.json" % p)
    if not f.is_file():
        gate("规格表 %s" % p, False, "缺 %s -- 先跑 make_spec_table.py" % f)
        continue
    tbl = json.loads(f.read_text())
    meta = tbl.get("_meta") or {}
    u = meta.get("utilization")
    n = len([k for k in tbl if not k.startswith("_")])
    gate("规格表 %s 利用率" % p, u == FROZEN_UTIL,
         "表记 %s，冻结 %s，%d 份设计" % (u, FROZEN_UTIL, n))

# 3. the area allowance exists and is in range -- without it every die is small
f = D / "spec/area_allowance.json"
if f.is_file():
    al = json.loads(f.read_text())
    missing = [p for p in PLATS if not isinstance(al.get(p), (int, float))]
    gate("面积余量", not missing,
         "%s" % {p: al.get(p) for p in PLATS} if not missing
         else "缺 %s" % missing)
else:
    gate("面积余量", False, "缺 %s -- 先跑 derive_area_allowance.py" % f)

# 4. one campaign at a time
live = []
for pr in glob.glob("/proc/[0-9]*"):
    try:
        c = Path(pr, "cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf8", "replace")
    except OSError:
        continue
    if "orfs_spec.py" in c or "orfs_baseline.py" in c:
        live.append(pr.split("/")[-1])
gate("没有其它 campaign 在跑", not live,
     "空闲" if not live else "%d 个驱动在跑: %s" % (len(live), live[:4]))

# 5. the driver emits what the document freezes -- on real output if any exists
checked = False
for p in PLATS:
    root = D / "_preflight" / p / "runs"
    if not root.is_dir() or not any(root.iterdir()):
        continue
    r = subprocess.run(
        [sys.executable, "-I", str(D / "conformance_check.py"),
         "--root", "_preflight/%s" % p, "--platform", p],
        capture_output=True, text=True)
    gate("一致性 %s" % p, r.returncode == 0,
         (r.stdout or r.stderr).strip().splitlines()[-1][:60]
         if (r.stdout or r.stderr) else "rc=%d" % r.returncode)
    checked = True
if not checked:
    gate("一致性检查", False,
         "没有预检产出可查 -- 先跑 preflight_util.sh 或 preflight_wide.sh")

# 6. the corpus package the pilot used
pkg = D / "rtl-certified-handoff-0002"
gate("语料包", pkg.is_dir(),
     str(pkg) if pkg.is_dir() else "缺 %s" % pkg)

# 7. disk -- 1249 x 3 designs retaining GDS
try:
    st = os.statvfs(D)
    free_tb = st.f_bavail * st.f_frsize / 1e12
    gate("磁盘", free_tb > 1.0, "%.1f TB 可用（估需约 0.4 TB）" % free_tb)
except OSError as exc:
    gate("磁盘", False, str(exc))

# 8. the reaper's grace must exceed the stage timeout, or it reaps live work
reap = D / "reaper.sh"
if reap.is_file():
    txt = reap.read_text()
    import re
    m = re.search(r"GRACE_MIN=\$\{GRACE_MIN:-(\d+)\}", txt)
    g = int(m.group(1)) if m else -1
    gate("回收器宽限 > 阶段超时", g * 60 > 21600,
         "GRACE_MIN=%d 分 vs 阶段超时 360 分" % g)
else:
    gate("回收器", False, "缺 reaper.sh")

print()
if fails:
    print("不要启动。未通过 %d 项: %s" % (len(fails), ", ".join(fails)))
    raise SystemExit(1)
print("全部通过 —— 可以启动 rerun_baseline.sh")
