#!/usr/bin/env bash
# Bounded checker supervisor (RMD2-P0-01, three-platform pilot 2026-07-24).
# Sourced, not executed.
#
# Why GNU `timeout` is not enough here: `timeout … bash wrapper.sh | tee log`
# supervises the WRAPPER, not the checker. The ORFS klayout.sh wrapper starts
# KLayout as a child without exec, so at expiry timeout+wrapper exit while the
# tool child survives with PPID=1 at ~99% CPU — and the `tee` reader keeps the
# output pipe open, so the calling script never returns. The Nangate45 SHA-256
# pilot campaign froze behind exactly this until an operator SIGKILLed the
# orphan (see references/failure-patterns.md "KLayout DRC Stuck on `or`" and
# #40 for the sibling ORFS-stage variant).
#
#   r2g_bounded_run TIMEOUT_S GRACE_S LOGFILE CMD [ARG…]
#
#   * starts CMD in a NEW SESSION (setsid) so the checker and every descendant
#     share one session/process group that we record and can kill as a unit;
#   * writes output DIRECTLY to LOGFILE — no pipeline whose reader can outlive
#     the supervisor;
#   * on timeout: SIGTERM to the whole group, GRACE_S seconds of grace, then
#     SIGKILL to the group AND the session (covers a descendant that moved to
#     its own process group within the session);
#   * verifies no session member survives before returning;
#   * returns CMD's exit code, or 124 on timeout (matching GNU timeout).
#
# The caller should install the cleanup trap so cancellation (INT/TERM) or an
# unexpected exit cannot orphan the checker either:
#   trap 'r2g_bounded_cleanup' EXIT
#   trap 'r2g_bounded_cleanup; exit 130' INT
#   trap 'r2g_bounded_cleanup; exit 143' TERM

_R2G_BOUNDED_SID=""

r2g_bounded_cleanup() {
  if [[ -n "${_R2G_BOUNDED_SID:-}" ]]; then
    kill -TERM -- "-$_R2G_BOUNDED_SID" 2>/dev/null || true
    pkill -KILL -s "$_R2G_BOUNDED_SID" 2>/dev/null || true
    kill -KILL -- "-$_R2G_BOUNDED_SID" 2>/dev/null || true
    _R2G_BOUNDED_SID=""
  fi
}

r2g_bounded_run() {
  local timeout_s="$1" grace_s="$2" log="$3"
  shift 3
  local pid pgid rc timed_out=0 deadline kdeadline tries rss

  setsid "$@" >"$log" 2>&1 </dev/null &
  pid=$!
  # The asynchronously started `setsid` child has not necessarily executed
  # setsid(2) when the parent reaches this line.  Reading PGID here therefore
  # has a race: it can still report the CALLER's process group, and cleanup then
  # kills the campaign shell itself.  A background shell child is not a process
  # group leader, so GNU setsid execs in-place and the new SID/PGID is exactly
  # the PID returned by `$!`; bind to that deterministic identity directly.
  pgid="$pid"
  _R2G_BOUNDED_SID="$pgid"

  # LIM-HO-01 telemetry: peak resident-set of the WHOLE checker session, sampled
  # on the existing 1s supervision tick (one `ps` per second — noise next to a
  # running checker). 0 = never sampled (sub-second command); callers report it
  # as unavailable. A stuck full-deck DRC needs peak memory recorded so the
  # KLayout/deck scaling investigation has data, not anecdotes.
  _R2G_BOUNDED_PEAK_RSS_KB=0

  deadline=$(( SECONDS + timeout_s ))
  while kill -0 "$pid" 2>/dev/null; do
    rss="$(ps -o rss= -s "$pgid" 2>/dev/null | awk '{s+=$1} END{print s+0}')"
    [[ "${rss:-0}" -gt "$_R2G_BOUNDED_PEAK_RSS_KB" ]] && _R2G_BOUNDED_PEAK_RSS_KB="$rss"
    if (( SECONDS >= deadline )); then
      timed_out=1
      kill -TERM -- "-$pgid" 2>/dev/null || true
      kdeadline=$(( SECONDS + grace_s ))
      while kill -0 "$pid" 2>/dev/null && (( SECONDS < kdeadline )); do
        sleep 1
      done
      kill -KILL -- "-$pgid" 2>/dev/null || true
      pkill -KILL -s "$pgid" 2>/dev/null || true
      break
    fi
    sleep 1
  done
  wait "$pid" 2>/dev/null
  rc=$?

  # Verify the whole session is gone before we let the caller grade artifacts:
  # a survivor here means the verdict below would race a still-running checker.
  tries=0
  while pgrep -s "$pgid" >/dev/null 2>&1 && (( tries < 10 )); do
    pkill -KILL -s "$pgid" 2>/dev/null || true
    kill -KILL -- "-$pgid" 2>/dev/null || true
    sleep 1
    tries=$((tries + 1))
  done
  if pgrep -s "$pgid" >/dev/null 2>&1; then
    echo "ERROR: bounded run left unkillable survivor(s) in session $pgid:" >&2
    pgrep -a -s "$pgid" >&2 || true
  fi
  _R2G_BOUNDED_SID=""

  if [[ "$timed_out" == "1" ]]; then
    return 124
  fi
  return "$rc"
}
