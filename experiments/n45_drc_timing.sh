#!/usr/bin/env bash
# Measure nangate45's DRC wall time on designs that FINISH, to set a timeout
# that drops no verdict.
#
# Why it pays: 67 dev designs stall forever inside the ANTENNA block and burn
# the full 7200 s each -- about 11 hours of the re-run at 12 workers. A bound
# above the slowest finisher saves that and loses nothing. The window is narrow:
# the largest design that finishes is 36 880 cells and the smallest that stalls
# is 39 782, a 7% gap.
#
# The budget here is deliberately the pilot's full 7200 s. Measuring with a
# shorter one would measure MY timeout rather than the design -- which is
# exactly how the C00458 equivalence attempt failed earlier today, at 1800 s on
# a design the corpus had needed longer for.
#
# Positive control: 9 of the 15 designs have violations. A design that exits in
# seconds with 0 violations did not check anything, and must not be counted as
# "fast".
set -uo pipefail
D=/proj/workarea/yangao
OUT=$D/_n45timing
LOG=$D/n45_drc_timing.log
WORKERS=${WORKERS:-4}
say(){ printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG"; }

[ -s "$D/_n45timing.select" ] || { say "缺选题"; exit 1; }
rm -rf "$OUT"; mkdir -p "$OUT/runs"
while read -r n; do
  [ -n "$n" ] && ln -s "$D/n45_full/runs/$n" "$OUT/runs/$n"
done < "$D/_n45timing.select"
say "=== nangate45 DRC 计时  $(ls "$OUT/runs" | wc -l) 份  worker=$WORKERS  预算 7200s"

R2G_DRC_TIMEOUT=7200 timeout 10800 python3 -I "$D/signoff45.py" \
    --runs "$OUT/runs" --out "$OUT/timing.json" \
    --workers "$WORKERS" --timeout 8000 --force >> "$LOG" 2>&1
say "=== 跑完 rc=$?"

python3 -I - <<'PY' | tee -a "$LOG"
import json
from pathlib import Path
D = Path("/proj/workarea/yangao")
rows = json.loads((D / "_n45timing/timing.json").read_text())
rows.sort(key=lambda r: r.get("seconds") or 0)
print("%-46s %10s %7s %7s" % ("设计", "判决", "违例", "秒"))
ok = []
for r in rows:
    s = r.get("seconds") or 0
    st = r.get("drc_status")
    v = r.get("drc_violations")
    flag = ""
    # positive control: finished fast AND found nothing AND was expected dirty
    if st == "clean" and s < 10:
        flag = "  <- 可疑：太快且 0 违例"
    if st in ("clean", "violations"):
        ok.append(s)
    print("%-46s %10s %7s %7.0f%s" % (r["design"][:46], st, v, s, flag))
if ok:
    ok.sort()
    print()
    print("出判决的 %d 份：最快 %.0fs  中位 %.0fs  最慢 %.0fs" % (len(ok), ok[0], ok[len(ok)//2], ok[-1]))
    print("→ 建议 DRC 超时 = 最慢的 2 倍 = %.0fs" % (ok[-1] * 2))
    print("   （2 倍是对机器负载波动留的余量；低于最慢值就会丢判决）")
    saved = 67 * (7200 - ok[-1] * 2) / 12 / 3600
    print("   那 67 份卡死的设计省下: %.1f 小时" % max(saved, 0))
bad = [r for r in rows if r.get("drc_status") not in ("clean", "violations")]
if bad:
    print()
    print("没出判决的 %d 份（它们不该出现在这里，选题只取了有判决的）:" % len(bad))
    for r in bad:
        print("   %s  %s  %.0fs" % (r["design"][:46], r.get("drc_status"), r.get("seconds") or 0))
PY
