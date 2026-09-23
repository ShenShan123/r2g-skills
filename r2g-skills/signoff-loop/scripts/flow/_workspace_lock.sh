#!/usr/bin/env bash
# Per-workspace lock for the shared ORFS tree (full-pipeline Issue 9). Sourced.
#
# The ORFS workspace $FLOW_DIR/{designs,results,logs,objects}/<platform>/<design>/<variant>
# is shared by every run with the same (platform, DESIGN_NAME, FLOW_VARIANT). run_orfs.sh
# holds this lock for the whole flow; the signoff checkers hold it from restage to
# verdict, so two runs sharing a variant cannot grade each other's GDS (CORRECTIONS #16).
# The lock is fd-scoped: released automatically when the holding script exits.

_r2g_workspace_lockfile() {  # platform design variant -> echoes the lockfile path
  # Keyed on the SHARED ORFS workspace identity ($FLOW_DIR/.../<platform>/<design>/<variant>).
  local key h
  key="$1/$2/$3"
  h="$(printf '%s' "$key" | md5sum 2>/dev/null | awk '{print $1}')"
  [[ -z "$h" ]] && h="$(printf '%s' "$key" | tr -c 'A-Za-z0-9' '_')"
  printf '%s/r2g_ws_%s.lock' "${R2G_LOCK_DIR:-/tmp}" "$h"
}
_r2g_acquire_workspace_lock() {  # platform design variant -> holds an fd-scoped lock; 1 on contention
  command -v flock >/dev/null 2>&1 || { echo "${0##*/}: flock unavailable — skipping workspace lock" >&2; return 0; }
  local lf; lf="$(_r2g_workspace_lockfile "$1" "$2" "$3")"
  exec {R2G_WS_LOCK_FD}>"$lf" || { echo "${0##*/}: ERROR cannot open workspace lockfile $lf" >&2; return 1; }
  if ! flock -n "$R2G_WS_LOCK_FD"; then
    echo "${0##*/}: ERROR another run holds the ORFS workspace (platform=$1 design=$2 variant=$3)." >&2
    echo "  Never run two configs with the same DESIGN_NAME+FLOW_VARIANT concurrently" >&2
    echo "  (CLAUDE.md Hard Rules) — they race clean_all vs build. Lockfile: $lf" >&2
    return 1
  fi
  return 0
}
