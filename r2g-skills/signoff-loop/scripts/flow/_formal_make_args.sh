#!/usr/bin/env bash
# Source from flow/checker runners. No manifest means legacy behavior unchanged.
r2g_formal_make_args() {
  R2G_FORMAL_MAKE_ARGS=()
  [[ -n "${R2G_TASK_MANIFEST:-}" ]] || return 0
  local args_file helper
  helper="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/t2a_effective_config.py"
  args_file=$(mktemp) || return 76
  if ! python3 "$helper" --project "$1" --make-args >"$args_file"; then
    rm -f -- "$args_file"
    return 76
  fi
  mapfile -d '' -t R2G_FORMAL_MAKE_ARGS <"$args_file"
  rm -f -- "$args_file"
}
