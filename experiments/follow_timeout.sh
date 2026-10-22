#!/usr/bin/env bash
# Follow the 6h timeout re-run and hand each newly judged antenna-only,
# LVS-clean design to the antenna repair, as it appears.
#
# A campaign instance reads its pool line by line and stops at EOF, so lines
# appended later are never seen. Instead, every round collects the new
# candidates into a fresh pool file and starts a small instance on it; the
# shared lock dir guarantees no design is run twice. Exits once the timeout
# re-run has finished and a final sweep finds nothing new.
D=/proj/workarea/yangao; cd $D
round=0
while :; do
  alive=$(pgrep -fc "signoff45\.py --runs.*_timeout_rerun_sel")
  python3 -I - "$round" <<'PY'
import json, re, sys, time
from pathlib import Path
start = time.mktime(time.strptime("2026-10-21 12:41", "%Y-%m-%d %H:%M"))
R = Path("rerun_nangate45_20261018/runs")
taken = {p.stem for p in Path("r2g_repair_n45/verdicts").glob("*.json")} | {p.name for p in Path("r2g_repair_n45/locks").iterdir()}
for p in Path(".").glob("ant_pool_timeout*.txt"):
    taken |= {l.strip() for l in p.read_text().splitlines() if l.strip()}
sel = []
for n in (l.strip() for l in open("/tmp/timeout_rerun.txt") if l.strip()):
    f = R / n / "result.json"
    if n in taken or not f.is_file() or f.stat().st_mtime < start: continue
    d = json.loads(f.read_text())
    if d.get("drc_status") != "violations" or d.get("lvs_status") != "clean": continue
    lb = R / n / "6_drc.lyrdb"
    cats = set(re.findall(r"<category>\s*'?([A-Za-z0-9_.]+)", lb.read_text(errors="replace"))) if lb.is_file() else set()
    if cats and all("ANTENNA" in x.upper() for x in cats): sel.append(n)
if sel:
    Path("ant_pool_timeout_%s.txt" % sys.argv[1]).write_text("".join(n + "\n" for n in sel))
print(len(sel))
PY
  n=$(ls ant_pool_timeout_${round}.txt 2>/dev/null | wc -l)
  if [ "$n" -gt 0 ]; then
    k=$(wc -l < ant_pool_timeout_${round}.txt)
    echo "$(date +%H:%M) round $round: $k new -> instance" >> follow_timeout.log
    WORKERS=$(( k < 4 ? k : 4 )) CORES=8 setsid nohup bash repair_antenna_n45_v2.sh ant_pool_timeout_${round}.txt \
      >> r2g_repair_n45.out_timeout 2>&1 < /dev/null &
    round=$((round + 1))
  fi
  [ "$alive" -eq 0 ] && [ "$n" -eq 0 ] && { echo "$(date +%H:%M) timeout re-run finished, nothing new; exit" >> follow_timeout.log; exit 0; }
  sleep 900
done
