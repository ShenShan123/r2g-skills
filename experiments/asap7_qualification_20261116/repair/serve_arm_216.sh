#!/usr/bin/env bash
# serve_arm_216.sh NAME [JOBS] -- on 216: pull repair arm NAME's finished runs from 203 and sign them off.
set -euo pipefail
NAME=$1; JOBS=${2:-4}; B=/proj/workarea/yangao/asap7_baseline; A=$B/repairs/$NAME
mkdir -p $A/runs $A/signoff; cd $B
echo "JOBS=$JOBS" > $A/serve.env       # resume_arms.sh replays this
module load calibre/2025.1_16.10 >/dev/null 2>&1
setsid nohup python3 pull_203.py --remote-runs /data/yangao/asap7_baseline/repairs/$NAME/runs --local-runs $A/runs \
  --loop 120 --settle 300 >> $A/pull.log 2>&1 < /dev/null &
setsid nohup python3 signoff_batch.py --runs $A/runs --out $A/signoff --jobs $JOBS --drc-budget 21600 --watch \
  >> $A/signoff.out 2>&1 < /dev/null &
echo "serving $NAME"
