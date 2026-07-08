#!/usr/bin/env bash
# Thin wrapper that runs ORFS -> LVS -> RCX for a list of named designs.
# Each design gets its own batch_logs/ dir inside design_cases/<name>/.
# Uses per-DESIGN_NAME flock (same convention as batch_run.sh) so concurrent
# runs of *different* designs are safe but re-running the same one isn't.
#
# Usage:
#   bash tools/run_two_designs.sh <design_name_1> [design_name_2 ...]
#
# Exit code reflects the worst status across designs. Per-stage exit codes
# land in <case>/batch_logs/exit_codes.txt exactly like batch_run.sh writes.

set -uo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CASES_DIR="$BASE_DIR/design_cases"
FLOW="$BASE_DIR/r2g-skills/signoff-loop/scripts/flow"

# Let the skill auto-discover tools via its own env machinery (see README:
# "The skill autodetects every tool on first use"). Older batch_run.sh
# source'd /opt/openroad_tools_env.sh — we keep that as a fallback for
# setups that still rely on it.
if [[ -f /opt/openroad_tools_env.sh ]]; then
  # shellcheck disable=SC1091
  source /opt/openroad_tools_env.sh
fi

: "${ORFS_TIMEOUT:=7200}"
: "${LVS_TIMEOUT:=3600}"
: "${RCX_TIMEOUT:=3600}"

run_one() {
  local name="$1"
  local case_dir="$CASES_DIR/$name"
  local cfg="$case_dir/constraints/config.mk"
  if [[ ! -f "$cfg" ]]; then
    echo "[$(date '+%H:%M:%S')] SKIP $name — no config.mk"
    return 1
  fi

  local platform design_name
  platform=$(grep -E '^export\s+PLATFORM'   "$cfg" | head -1 | sed 's/.*=\s*//' | tr -d ' ')
  design_name=$(grep -E '^export\s+DESIGN_NAME' "$cfg" | head -1 | sed 's/.*=\s*//' | tr -d ' ')

  local log_dir="$case_dir/batch_logs"
  mkdir -p "$log_dir"

  echo "[$(date '+%H:%M:%S')] START $name (design=$design_name platform=$platform)"

  local lock_dir="/tmp/orfs_locks"
  mkdir -p "$lock_dir"
  local lock_file="$lock_dir/${design_name}.lock"

  (
    flock -x 200

    # ORFS backend — run_orfs.sh executes stages individually; wrap it in
    # setsid+timeout so a wedged klayout/openroad grandchild gets killed.
    setsid timeout --signal=TERM --kill-after=60 "$((ORFS_TIMEOUT * 6))" \
      env ORFS_TIMEOUT="$ORFS_TIMEOUT" \
      bash "$FLOW/run_orfs.sh" "$case_dir" "$platform" \
      > "$log_dir/orfs.log" 2>&1
    local orfs_exit=$?

    if [[ $orfs_exit -ne 0 ]]; then
      echo "$orfs_exit -1 -1" > "$log_dir/exit_codes.txt"
      return $orfs_exit
    fi

    setsid timeout --signal=TERM --kill-after=30 "$((LVS_TIMEOUT + 60))" \
      env LVS_TIMEOUT="$LVS_TIMEOUT" \
      bash "$FLOW/run_lvs.sh" "$case_dir" "$platform" \
      > "$log_dir/lvs.log" 2>&1
    local lvs_exit=$?

    setsid timeout --signal=TERM --kill-after=30 "$((RCX_TIMEOUT + 60))" \
      env RCX_TIMEOUT="$RCX_TIMEOUT" \
      bash "$FLOW/run_rcx.sh" "$case_dir" "$platform" \
      > "$log_dir/rcx.log" 2>&1
    local rcx_exit=$?

    echo "$orfs_exit $lvs_exit $rcx_exit" > "$log_dir/exit_codes.txt"
  ) 200>"$lock_file"

  local codes
  codes=$(cat "$log_dir/exit_codes.txt" 2>/dev/null || echo "1 -1 -1")
  local o l r
  o=$(echo "$codes" | awk '{print $1}')
  l=$(echo "$codes" | awk '{print $2}')
  r=$(echo "$codes" | awk '{print $3}')

  local status="ORFS:$o LVS:$l RCX:$r"
  echo "[$(date '+%H:%M:%S')] DONE  $name — $status"
  [[ "$o" == "0" && "$l" == "0" && "$r" == "0" ]]
}

worst=0
for name in "$@"; do
  run_one "$name" &
done
wait
for name in "$@"; do
  codes=$(cat "$CASES_DIR/$name/batch_logs/exit_codes.txt" 2>/dev/null || echo "1 -1 -1")
  for c in $codes; do
    [[ "$c" != "0" ]] && worst=1
  done
done
exit "$worst"
