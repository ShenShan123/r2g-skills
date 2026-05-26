#!/usr/bin/env bash
# Shared environment-discovery helper for the r2g-rtl2gds flow scripts.
#
# Sourced (not executed) by run_orfs.sh / run_drc.sh / run_lvs.sh / run_rcx.sh /
# run_magic_drc.sh / run_netgen_lvs.sh / check_env.sh.
#
# Resolution order (first hit wins for each value):
#   1. Value already set in the caller's environment
#   2. Value from a user env file ($R2G_ENV_FILE if set, else skipped)
#   3. Value from a user env file shipped inside the skill (references/env.local.sh)
#   4. Value sourced from $ORFS_ROOT/env.sh (if ORFS_ROOT found)
#   5. Value sourced from /opt/openroad_tools_env.sh (if present)
#   6. Autodetected path: `command -v <tool>` or well-known install locations
#
# After sourcing, these variables are set (and exported) when discoverable:
#   ORFS_ROOT        — path to the OpenROAD-flow-scripts checkout (must contain flow/)
#   FLOW_DIR         — $ORFS_ROOT/flow
#   OPENROAD_EXE     — openroad binary
#   YOSYS_EXE        — yosys binary
#   KLAYOUT_CMD      — klayout binary (optional)
#   MAGIC_EXE        — magic binary (optional)
#   NETGEN_EXE       — netgen / netgen-lvs binary (optional)
#   STA_EXE          — opensta/sta binary (optional)
#   IVERILOG_EXE     — iverilog binary (optional)
#   VVP_EXE          — vvp binary (optional)
#   VERILATOR_EXE    — verilator binary (optional)
#   PDK_ROOT         — directory containing sky130A etc. (optional)
#   SKY130A_DIR      — $PDK_ROOT/sky130A if present (optional)
#
# Users can override any value by exporting it before sourcing this file, or
# by placing a shell snippet at $R2G_ENV_FILE or at
# <skill>/references/env.local.sh.

_r2g_saved_opts="$-"
set +eu  # tolerate unset vars and detect misses from sourced snippets
_R2G_ENV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_R2G_SKILL_DIR="$(cd "$_R2G_ENV_DIR/../.." && pwd)"

# --- 1. User-provided env snippets ---------------------------------------
if [[ -n "${R2G_ENV_FILE:-}" && -f "$R2G_ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$R2G_ENV_FILE"
fi

if [[ -f "$_R2G_SKILL_DIR/references/env.local.sh" ]]; then
  # shellcheck disable=SC1090,SC1091
  source "$_R2G_SKILL_DIR/references/env.local.sh"
fi

# --- 2. Locate ORFS ------------------------------------------------------
_r2g_find_orfs() {
  local candidates=(
    "${ORFS_ROOT:-}"
    "$HOME/OpenROAD-flow-scripts"
    "/opt/OpenROAD-flow-scripts"
    "/opt/EDA4AI/OpenROAD-flow-scripts"
    "/proj/workarea/user5/OpenROAD-flow-scripts"
  )
  # Plus any sibling of the skill dir
  candidates+=("$(cd "$_R2G_SKILL_DIR/../.." 2>/dev/null && pwd)/OpenROAD-flow-scripts")
  local c
  for c in "${candidates[@]}"; do
    [[ -z "$c" ]] && continue
    if [[ -f "$c/flow/Makefile" ]]; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

if [[ -z "${ORFS_ROOT:-}" ]] || [[ ! -f "$ORFS_ROOT/flow/Makefile" ]]; then
  if _detected="$(_r2g_find_orfs)"; then
    ORFS_ROOT="$_detected"
  fi
fi

if [[ -n "${ORFS_ROOT:-}" ]]; then
  export ORFS_ROOT
  export FLOW_DIR="$ORFS_ROOT/flow"
  # Source ORFS-provided env script if present
  if [[ -f "$ORFS_ROOT/env.sh" ]]; then
    # shellcheck disable=SC1090,SC1091
    source "$ORFS_ROOT/env.sh"
  fi
fi

# --- 3. System-wide env script (if any) ----------------------------------
if [[ -f /opt/openroad_tools_env.sh ]]; then
  # shellcheck disable=SC1091
  source /opt/openroad_tools_env.sh
fi

# --- 4. Autodetect each tool binary --------------------------------------
_r2g_detect() {
  # Sets $1 to first hit of: $1 (if already set) > `command -v $2` > candidate list
  local var="$1"; shift
  local primary="$1"; shift
  local current="${!var:-}"
  if [[ -n "$current" && -x "$current" ]]; then
    export "$var=$current"
    return 0
  fi
  local hit
  hit="$(command -v "$primary" 2>/dev/null || true)"
  if [[ -n "$hit" ]]; then
    export "$var=$hit"
    return 0
  fi
  local cand
  for cand in "$@"; do
    if [[ -x "$cand" ]]; then
      export "$var=$cand"
      return 0
    fi
  done
  return 1
}

# ORFS ships its own openroad/yosys under tools/install/; prefer those when found
_r2g_orfs_openroad=""
_r2g_orfs_yosys=""
if [[ -n "${ORFS_ROOT:-}" ]]; then
  _r2g_orfs_openroad="$ORFS_ROOT/tools/install/OpenROAD/bin/openroad"
  _r2g_orfs_yosys="$ORFS_ROOT/tools/install/yosys/bin/yosys"
fi

_r2g_detect OPENROAD_EXE  openroad   \
  "$_r2g_orfs_openroad" /usr/local/bin/openroad /usr/bin/openroad

_r2g_detect YOSYS_EXE     yosys      \
  "$_r2g_orfs_yosys" /opt/pdk_klayout_openroad/oss-cad-suite/bin/yosys \
  /usr/local/bin/yosys /usr/bin/yosys

_r2g_detect IVERILOG_EXE  iverilog   \
  /opt/pdk_klayout_openroad/oss-cad-suite/bin/iverilog /usr/bin/iverilog

_r2g_detect VVP_EXE       vvp        \
  /opt/pdk_klayout_openroad/oss-cad-suite/bin/vvp /usr/bin/vvp

_r2g_detect VERILATOR_EXE verilator  \
  /opt/pdk_klayout_openroad/oss-cad-suite/bin/verilator /usr/bin/verilator

_r2g_detect KLAYOUT_CMD   klayout    \
  /usr/local/bin/klayout /usr/bin/klayout

_r2g_detect MAGIC_EXE     magic      \
  /usr/local/bin/magic /usr/bin/magic

# Netgen ships under several names; try each in turn
if [[ -z "${NETGEN_EXE:-}" ]]; then
  for _cand in netgen-lvs netgen; do
    if _hit="$(command -v "$_cand" 2>/dev/null)"; then
      if [[ -n "$_hit" ]]; then export NETGEN_EXE="$_hit"; break; fi
    fi
  done
fi
: "${NETGEN_EXE:=}"
[[ -z "$NETGEN_EXE" && -x /usr/bin/netgen-lvs ]] && export NETGEN_EXE=/usr/bin/netgen-lvs
[[ -z "$NETGEN_EXE" && -x /usr/local/bin/netgen ]] && export NETGEN_EXE=/usr/local/bin/netgen

_r2g_detect STA_EXE       sta        \
  /usr/local/bin/opensta /usr/local/bin/sta /usr/bin/opensta

if [[ -z "${STA_EXE:-}" ]]; then
  _r2g_detect STA_EXE     opensta    /usr/local/bin/opensta /usr/bin/opensta
fi

# --- 5. PDK autodetect ---------------------------------------------------
if [[ -z "${PDK_ROOT:-}" ]]; then
  for _p in /opt/pdks "$HOME/pdks" /usr/local/share/pdks; do
    if [[ -d "$_p" ]]; then export PDK_ROOT="$_p"; break; fi
  done
fi

if [[ -n "${PDK_ROOT:-}" && -d "$PDK_ROOT/sky130A" ]]; then
  export SKY130A_DIR="$PDK_ROOT/sky130A"
fi

unset _r2g_orfs_openroad _r2g_orfs_yosys _cand _hit _p _detected
# Restore caller's options
case "$_r2g_saved_opts" in
  *e*) set -e ;;
esac
case "$_r2g_saved_opts" in
  *u*) set -u ;;
esac
unset _r2g_saved_opts
true
