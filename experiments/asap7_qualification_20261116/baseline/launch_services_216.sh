#!/usr/bin/env bash
# launch_services_216.sh -- on 216: pull finished 203 runs (216 connects to 203; nothing via 208) and sign off every
# finished run from both servers with Calibre (flat DRC A+B, LVS), GDS regenerated on 216 for 203 runs.
set -euo pipefail
B=/proj/workarea/yangao/asap7_baseline; cd $B
module load calibre/2025.1_16.10 >/dev/null 2>&1
mkdir -p inbox203/runs signoff
setsid nohup python3 pull_203.py --remote-runs /data/yangao/asap7_baseline/baseline/runs --local-runs $B/inbox203/runs \
  --loop 300 --settle 600 > $B/pull_203.log 2>&1 < /dev/null &
echo "puller $!"
setsid nohup python3 signoff_batch.py --runs $B/baseline/runs --runs $B/inbox203/runs --out $B/signoff \
  --jobs 6 --drc-budget 21600 --watch > $B/signoff.out 2>&1 < /dev/null &
echo "signoff $!"
