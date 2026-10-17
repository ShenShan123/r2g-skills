#!/usr/bin/env bash
# Run the wide preflight once the machine is actually free.
#
# "Actually" is the point. An earlier chain polled once for "is a driver
# running", and between one sweep point ending and the next one's driver
# starting there is a gap with no match -- it read that gap as "finished" and
# started a second campaign on top of a live one. So this requires N CONSECUTIVE
# idle samples, which a gap between two points of the same chain cannot produce.
set -uo pipefail
D=/proj/workarea/yangao
LOG=$D/queue_wide.log
NEED=${NEED:-5}            # consecutive idle samples required
GAP=${GAP:-60}             # seconds between samples
MAXWAIT=${MAXWAIT:-21600}  # give up after 6h rather than wait forever
say(){ printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG"; }

drivers() {
  local n=0
  for p in /proc/[0-9]*; do
    local c; c=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null) || continue
    case "$c" in *orfs_spec.py*|*orfs_baseline.py*) n=$((n+1));; esac
  done
  echo "$n"
}

say "等机器空闲：需要连续 $NEED 次采样（每 ${GAP}s）都没有驱动在跑"
idle=0; waited=0
while [ "$idle" -lt "$NEED" ]; do
  n=$(drivers)
  if [ "$n" = 0 ]; then
    idle=$((idle+1))
    say "  空闲 $idle/$NEED"
  else
    [ "$idle" -gt 0 ] && say "  又有 $n 个驱动起来了，计数归零"
    idle=0
  fi
  [ "$idle" -lt "$NEED" ] || break
  sleep "$GAP"; waited=$((waited+GAP))
  if [ "$waited" -ge "$MAXWAIT" ]; then
    say "等了 $((waited/3600))h 还没空闲 -- 放弃，手动起"
    exit 1
  fi
done

say "机器空闲，起宽预检"
bash "$D/preflight_wide.sh"
say "宽预检结束 rc=$?"
