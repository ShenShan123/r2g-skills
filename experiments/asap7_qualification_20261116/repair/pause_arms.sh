#!/usr/bin/env bash
# pause_arms.sh [--dry-run] -- clean stop of every ASAP7 repair driver / signer / puller on THIS host before a planned
# shutdown. Each process group gets TERM, then KILL after a grace period; containers named *__asap7__p* are stopped
# (216). Nothing is deleted: finished designs keep result.json / signoff.json and are skipped on resume; interrupted
# ones are redone from scratch by resume_arms.sh. Writes a snapshot of per-arm progress to $B/repairs/PAUSED_AT.
set -uo pipefail
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
if [ -d /proj/workarea/yangao/asap7_baseline ]; then HOST=216; B=/proj/workarea/yangao/asap7_baseline
elif [ -d /data/yangao/asap7_baseline ]; then HOST=203; B=/data/yangao/asap7_baseline
else echo "no asap7_baseline on this host"; exit 1; fi
R=$B/repairs
pids=$(pgrep -u "$(id -u)" -f "[o]rfs_spec.py.*--out $R/|[s]ignoff_batch.py --runs $R/|[p]ull_203.py.*repairs/" | tr '\n' ' ')
{ echo "paused $(date '+%F %T') host $HOST"; for A in $R/*/; do
    [ -f $A/launch.env ] || [ -f $A/serve.env ] || continue
    echo "$(basename $A) runs $(ls $A/runs/*/result.json 2>/dev/null | wc -l)/$(grep -c . $A/select 2>/dev/null) signed $(ls $A/signoff/*/signoff.json 2>/dev/null | wc -l)"
  done; } | tee $R/PAUSED_AT.tmp
[ $DRY = 1 ] && { echo "DRY: would TERM process groups of: $pids"; rm -f $R/PAUSED_AT.tmp; exit 0; }
mv $R/PAUSED_AT.tmp $R/PAUSED_AT
pgids=$(for p in $pids; do ps -o pgid= -p $p; done | sort -u | tr -d ' ')
for g in $pgids; do kill -TERM -- -$g 2>/dev/null; done
sleep 30
for g in $pgids; do kill -KILL -- -$g 2>/dev/null; done
if [ $HOST = 216 ]; then
  for c in $(podman ps --format '{{.Names}}' | grep '__asap7__p'); do podman stop -t 10 $c >/dev/null 2>&1; done
fi
echo "stopped groups: $pgids; remaining: $(pgrep -u "$(id -u)" -fc "[o]rfs_spec.py|[s]ignoff_batch.py|[p]ull_203.py")"
