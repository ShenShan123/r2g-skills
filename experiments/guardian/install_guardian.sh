#!/usr/bin/env bash
# install_guardian.sh 203|216 -- add ONE cron line (every minute, flock'd, niced, time-bounded) running guardian.py.
# Idempotent: the line carries the marker "# r2g-guardian" and is replaced, never duplicated; other lines are kept.
set -euo pipefail
S=$1
if [ "$S" = 203 ]; then
  G=/data/yangao/guardian
  ARGS="--watch-dir /data --runs-root /data/yangao --cgroup-act-gib 240 --disk-stop-gib 80 --disk-resume-gib 120"
else
  G=/proj/workarea/yangao/guardian
  ARGS="--watch-dir /proj/workarea --runs-root /proj/workarea/yangao/asap7_baseline --avail-floor-gib 150 --disk-stop-gib 500 --disk-resume-gib 800"
fi
LINE="* * * * * /usr/bin/flock -n $G/cron.lock /usr/bin/timeout 300 /usr/bin/nice -n 10 /usr/bin/python3 $G/guardian.py --state $G/state $ARGS > $G/last_pass.log 2>&1 # r2g-guardian"
( { crontab -l 2>/dev/null || true; } | { grep -v -e '# r2g-guardian' -e '^# t$' || true; } ; echo "$LINE" ) | crontab -
crontab -l | grep -c '# r2g-guardian'
