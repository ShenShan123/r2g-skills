#!/usr/bin/env bash
# Stop EVERY sweep/campaign instance, by name, not by a pid noted earlier.
#
# Twice today I stopped "the" sweep chain by killing the pids I had just looked
# up, and both times a second instance survived -- the first a setsid'd one from
# a chain I thought I had ended, the second a sweep_all.sh that had been waiting
# four hours and resumed the u60/u70/u80 points (already dropped from the spec)
# the moment I freed the machine. It also kept the queued wide preflight from
# ever starting, since that correctly refuses to stack on a live driver.
#
# The fix is not "look harder": a pid noted at time T says nothing about time
# T+1. Match by NAME, loop until the count is zero, and print what is left.
#
# Reads /proc/<pid>/cmdline rather than `pgrep -f`, which matches this script's
# own command line -- documented in EXPERIMENT_RUNBOOK_zh.md, and the reason an
# earlier kill loop killed the shell it was running in.
set -uo pipefail
PATTERNS=${PATTERNS:-"sweep_all.sh util_sweep.sh orfs_baseline.py orfs_spec.py u50_both.sh"}

matching() {
  local out=""
  for p in /proc/[0-9]*; do
    local pid=${p#/proc/}
    [ "$pid" = "$$" ] && continue
    # 2>/dev/null on the redirect is not enough: bash reports the failed
    # redirection itself, not tr, so the stderr has to be silenced on the
    # SUBSHELL. A process exiting between the glob and the read is normal and
    # must not look like an error.
    local c; c=$( { tr '\0' ' ' < "$p/cmdline"; } 2>/dev/null ) || continue
    case "$c" in *kill_all_sweeps*) continue;; esac
    for pat in $PATTERNS; do
      case "$c" in *"$pat"*) out="$out $pid"; break;; esac
    done
  done
  echo "$out"
}

for round in 1 2 3; do
  pids=$(matching)
  [ -z "${pids// /}" ] && { echo "第 $round 轮：没有实例在跑"; break; }
  echo "第 $round 轮：$(echo $pids | wc -w) 个实例 ->$pids"
  for pid in $pids; do kill -TERM "$pid" 2>/dev/null; done
  sleep 5
done

left=$(matching)
if [ -n "${left// /}" ]; then
  echo "！！仍有实例存活，改用 KILL:$left"
  for pid in $left; do kill -9 "$pid" 2>/dev/null; done
  sleep 3
  left=$(matching)
fi
if [ -n "${left// /}" ]; then
  echo "！！KILL 之后仍存活:$left -- 手工处理，不要以为停了"
  exit 1
fi
echo "全部已停 ✓"

n=0
for c in $(podman ps --format '{{.Names}}' 2>/dev/null); do
  m=$(podman inspect "$c" --format '{{range .Mounts}}{{.Source}} {{end}}' 2>/dev/null)
  case "$m" in
    *_usweep*|*rerun_*|*_preflight*) podman rm -f "$c" >/dev/null 2>&1 && n=$((n+1));;
  esac
done
echo "清掉 $n 个相关容器；剩余 $(podman ps -q 2>/dev/null | wc -l) 个（别的任务的，没动）"
