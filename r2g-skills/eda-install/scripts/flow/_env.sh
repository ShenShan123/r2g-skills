#!/usr/bin/env bash
# Shared environment-discovery helper for the r2g-skills flow scripts.
#
# Shared by BOTH sub-skills (byte-identical copy in each). Sourced (not executed) by the
# signoff-loop flow runners (run_orfs/drc/lvs/rcx/magic/netgen, check_env) and the def-graph
# dataset runners (run_labels/run_features/run_graphs, resolve_platform_paths).
#
# Resolution order (first hit wins for each value):
#   1. Value already set in the caller's environment
#   2. Value from a user env file ($R2G_ENV_FILE if set, else skipped)
#   3. Value from a user env file shipped inside the skill (references/env.local.sh)
#   4. Value sourced from $ORFS_ROOT/env.sh (if ORFS_ROOT found)
#   5. Ordered user-local/direct-bundle/ORFS candidates
#   6. Optional conda candidates (legacy fallback)
#   7. PATH and host-wide candidates (`/opt`/`/usr/bin`) as final fallback
#
# /opt/openroad_tools_env.sh is sourced only to expose a host fallback. It may
# not override values from steps 1-4, and it cannot outrank user-local tools.
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
_r2g_hermetic="${R2G_HERMETIC:-0}"

_r2g_hostwide() {
  case "${1:-}" in
    /usr/*|/opt/*|/bin/*|/sbin/*) return 0 ;;
  esac
  return 1
}

# Preserve true caller overrides across sourced environment scripts.  The
# documented precedence says an exported caller value wins, but historically
# /opt/openroad_tools_env.sh could overwrite KLAYOUT_CMD/OPENROAD_EXE after the
# fact, making hermetic checks silently run the host tools instead of their
# requested binaries.
declare -A _r2g_caller_env=()
for _r2g_var in ORFS_ROOT OPENROAD_EXE YOSYS_EXE KLAYOUT_CMD MAGIC_EXE NETGEN_EXE \
                STA_EXE IVERILOG_EXE VVP_EXE VERILATOR_EXE PDK_ROOT SKY130A_DIR; do
  [[ -n "${!_r2g_var:-}" ]] || continue
  [[ "$_r2g_hermetic" == "1" ]] && _r2g_hostwide "${!_r2g_var}" && continue
  _r2g_caller_env["$_r2g_var"]="${!_r2g_var}"
done

# --- 1. User-provided env snippets ---------------------------------------
if [[ -n "${R2G_ENV_FILE:-}" && -f "$R2G_ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$R2G_ENV_FILE"
fi

if [[ -f "$_R2G_SKILL_DIR/references/env.local.sh" ]]; then
  # shellcheck disable=SC1090,SC1091
  source "$_R2G_SKILL_DIR/references/env.local.sh"
fi

# A generated env.local.sh can carry the hermetic marker even when the parent
# shell did not export it.  Refresh the mode after sourcing snippets, then
# discard any inherited host-wide values before the system layer and fallback
# probes can snapshot them as higher-priority pins.
_r2g_hermetic="${R2G_HERMETIC:-$_r2g_hermetic}"
if [[ "$_r2g_hermetic" == "1" ]]; then
  for _r2g_var in ORFS_ROOT OPENROAD_EXE YOSYS_EXE KLAYOUT_CMD MAGIC_EXE NETGEN_EXE \
                  STA_EXE IVERILOG_EXE VVP_EXE VERILATOR_EXE PDK_ROOT SKY130A_DIR; do
    _r2g_value="${!_r2g_var:-}"
    [[ -n "$_r2g_value" ]] && _r2g_hostwide "$_r2g_value" && unset "$_r2g_var"
    _r2g_value="${_r2g_caller_env[$_r2g_var]:-}"
    [[ -n "$_r2g_value" ]] && _r2g_hostwide "$_r2g_value" && \
      unset "_r2g_caller_env[$_r2g_var]"
  done
fi

# ORFS_ROOT is used immediately below to derive FLOW_DIR/FLOW_HOME.  Restore an
# explicit caller pin before that derivation; waiting until the later system
# layer would let env.local.sh's older checkout select its flow scripts even
# though the caller chose a clean/source-frozen tree.
if [[ -n "${_r2g_caller_env[ORFS_ROOT]:-}" ]]; then
  export ORFS_ROOT="${_r2g_caller_env[ORFS_ROOT]}"
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
  # Plus a checkout sitting beside the repo. The skill lives at
  # <repo>/r2g-skills/<skill>, so three levels up from the skill dir is the repo's
  # parent (where an OpenROAD-flow-scripts checkout is commonly a sibling of the repo).
  candidates+=("$(cd "$_R2G_SKILL_DIR/../../.." 2>/dev/null && pwd)/OpenROAD-flow-scripts")
  local c
  for c in "${candidates[@]}"; do
    [[ -z "$c" ]] && continue
    [[ "$_r2g_hermetic" == "1" ]] && _r2g_hostwide "$c" && continue
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
    # ORFS's helper prints its legacy OPENROAD path while setting PATH.  Keep
    # the environment exports but suppress that stale diagnostic; the direct
    # bundle pins below are the authoritative executable paths.
    source "$ORFS_ROOT/env.sh" >/dev/null
    # ORFS's helper derives FLOW_HOME from its own source path.  Restore the
    # caller-selected root so a clean/source-frozen checkout cannot silently
    # execute scripts from a sibling dirty checkout.
    export FLOW_HOME="$ORFS_ROOT/flow"
  fi
fi

# Remember values supplied by the caller, env file, or ORFS before consulting
# the host-wide environment.  The latter is only a fallback; it must not
# override a user-local conda/toolchain installation.
declare -A _r2g_pre_system_env=()
for _r2g_var in ORFS_ROOT OPENROAD_EXE YOSYS_EXE KLAYOUT_CMD MAGIC_EXE NETGEN_EXE \
                STA_EXE IVERILOG_EXE VVP_EXE VERILATOR_EXE PDK_ROOT SKY130A_DIR; do
  [[ -n "${!_r2g_var:-}" ]] && _r2g_pre_system_env["$_r2g_var"]="${!_r2g_var}"
done

# --- 3. System-wide env script (if any) ----------------------------------
if [[ "$_r2g_hermetic" != "1" && -f /opt/openroad_tools_env.sh ]]; then
  # shellcheck disable=SC1091
  source /opt/openroad_tools_env.sh
fi

for _r2g_var in "${!_r2g_caller_env[@]}"; do
  export "$_r2g_var=${_r2g_caller_env[$_r2g_var]}"
done
for _r2g_var in "${!_r2g_pre_system_env[@]}"; do
  [[ -n "${_r2g_caller_env[$_r2g_var]:-}" ]] && continue
  export "$_r2g_var=${_r2g_pre_system_env[$_r2g_var]}"
done
for _r2g_var in ORFS_ROOT OPENROAD_EXE YOSYS_EXE KLAYOUT_CMD MAGIC_EXE NETGEN_EXE \
                STA_EXE IVERILOG_EXE VVP_EXE VERILATOR_EXE PDK_ROOT SKY130A_DIR; do
  [[ -n "${_r2g_caller_env[$_r2g_var]:-}" || -n "${_r2g_pre_system_env[$_r2g_var]:-}" ]] && continue
  unset "$_r2g_var"
done

# --- 4. Autodetect each tool binary --------------------------------------
_r2g_detect() {
  # Sets $1 to first hit of: explicit value > ordered candidate list > PATH.
  # Candidate paths put user-local/conda/ORFS binaries ahead of host defaults;
  # callers that want another executable must pin it explicitly.
  local var="$1"; shift
  local primary="$1"; shift
  local current="${!var:-}"
  if [[ -n "$current" && -x "$current" ]] && \
     { [[ "$_r2g_hermetic" != "1" ]] || ! _r2g_hostwide "$current"; }; then
    export "$var=$current"
    return 0
  fi
  local cand
  for cand in "$@"; do
    [[ "$_r2g_hermetic" == "1" ]] && _r2g_hostwide "$cand" && continue
    if [[ -x "$cand" ]]; then
      export "$var=$cand"
      return 0
    fi
  done
  local hit
  hit="$(command -v "$primary" 2>/dev/null || true)"
  if [[ -n "$hit" ]] && \
     { [[ "$_r2g_hermetic" != "1" ]] || ! _r2g_hostwide "$hit"; }; then
    export "$var=$hit"
    return 0
  fi
  return 1
}

# Direct user toolchain bundle.  A direct bundle is the preferred reproducible
# layout: every non-system EDA payload lives below one user-owned root (normally
# `$R2G_PREFIX`).  The wrappers carry their own loader/library paths, so flows do
# not need `/opt` or a package-manager activation to run.
_r2g_toolchain_root="${R2G_TOOLCHAIN_ROOT:-${R2G_PREFIX:-}}"

# Direct OpenROAD/KLayout payloads are ELF binaries whose private libraries are
# kept beside them.  Put those directories first for every flow invocation so
# the canonical pins can point at the immutable ELF itself (rather than a shell
# wrapper whose digest would not cover the payload).
_r2g_prepend_lib() {
  local lib="$1"
  [[ -d "$lib" ]] || return 0
  case ":${LD_LIBRARY_PATH:-}:" in
    *":$lib:"*) return 0 ;;
  esac
  export LD_LIBRARY_PATH="$lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
}
_r2g_prepend_lib "$_r2g_toolchain_root/openroad/lib"
_r2g_prepend_lib "$_r2g_toolchain_root/openroad-matched/lib"
_r2g_prepend_lib "$_r2g_toolchain_root/klayout/lib"

# conda-env signoff tools (legacy fallback): `conda create -n eda …` puts
# iverilog/vvp/magic/netgen in <conda-base>/envs/$R2G_CONDA_ENV/bin, which PATH-based
# detection misses in a fresh shell — and the base may live on a big volume, NOT under
# $HOME (the 2026-07-09 relocation to /proj/workarea/$USER/miniconda3; a pin
# regeneration then lost the only pointers — failure-patterns.md #29). Probe the
# well-known bases once; the tool candidates and the PDK probe below share the list.
_r2g_conda_bases=("${CONDA_PREFIX:-}" "${R2G_PREFIX:-}/miniconda3" "$HOME/miniconda3" "$HOME/miniforge3"
                  "/proj/$USER/miniconda3" "/proj/workarea/$USER/miniconda3")
_r2g_env="${R2G_CONDA_ENV:-eda}"
_r2g_conda_bin=""
for _base in "${_r2g_conda_bases[@]}"; do
  [[ -z "$_base" ]] && continue
  if [[ -d "$_base/envs/$_r2g_env/bin" ]]; then
    _r2g_conda_bin="$_base/envs/$_r2g_env/bin"
    break
  fi
done

# ORFS ships its own openroad/yosys under tools/install/; prefer those when found.
# A no-sudo bootstrap may instead install both into the conda env under
# R2G_PREFIX; that user-owned pair must outrank /opt and /usr host fallbacks.
_r2g_orfs_openroad=""
_r2g_orfs_yosys=""
if [[ -n "${ORFS_ROOT:-}" ]]; then
  _r2g_orfs_openroad="$ORFS_ROOT/tools/install/OpenROAD/bin/openroad"
  _r2g_orfs_yosys="$ORFS_ROOT/tools/install/yosys/bin/yosys"
fi

_r2g_detect OPENROAD_EXE  openroad   \
  "$_r2g_toolchain_root/openroad-matched/bin/openroad" \
  "$_r2g_toolchain_root/openroad-matched/bin/openroad.bin" \
  "$_r2g_toolchain_root/openroad/bin/openroad.bin" \
  "$_r2g_toolchain_root/openroad/bin/openroad" \
  "$_r2g_orfs_openroad" "$_r2g_conda_bin/openroad" \
  /usr/local/bin/openroad /usr/bin/openroad

_r2g_detect YOSYS_EXE     yosys      \
  "$_r2g_toolchain_root/yosys/bin/yosys" \
  "$_r2g_orfs_yosys" "$_r2g_conda_bin/yosys" \
  /opt/pdk_klayout_openroad/oss-cad-suite/bin/yosys \
  /usr/local/bin/yosys /usr/bin/yosys

_r2g_detect IVERILOG_EXE  iverilog   \
  "$_r2g_toolchain_root/oss-cad-suite/bin/iverilog" \
  "$_r2g_conda_bin/iverilog" /opt/pdk_klayout_openroad/oss-cad-suite/bin/iverilog /usr/bin/iverilog

_r2g_detect VVP_EXE       vvp        \
  "$_r2g_toolchain_root/oss-cad-suite/bin/vvp" \
  "$_r2g_conda_bin/vvp" /opt/pdk_klayout_openroad/oss-cad-suite/bin/vvp /usr/bin/vvp

_r2g_detect VERILATOR_EXE verilator  \
  "$_r2g_toolchain_root/oss-cad-suite/bin/verilator" \
  "$_r2g_conda_bin/verilator" /opt/pdk_klayout_openroad/oss-cad-suite/bin/verilator /usr/bin/verilator

_r2g_detect KLAYOUT_CMD   klayout    \
  "$_r2g_toolchain_root/klayout/bin/klayout.bin" \
  "$_r2g_toolchain_root/klayout/bin/klayout" \
  /usr/local/bin/klayout /usr/bin/klayout "$_r2g_conda_bin/klayout"

# The host environment script exports its distribution Magic explicitly.  A
# compatible user-local build must outrank that autodetected system default,
# while a true caller override (restored above) still wins.
if [[ -z "${_r2g_caller_env[MAGIC_EXE]:-}" && -x "$_r2g_toolchain_root/magic/bin/magic" ]]; then
  export MAGIC_EXE="$_r2g_toolchain_root/magic/bin/magic"
elif [[ -z "${_r2g_caller_env[MAGIC_EXE]:-}" && -x "$HOME/.local/bin/magic" ]]; then
  export MAGIC_EXE="$HOME/.local/bin/magic"
fi
_r2g_detect MAGIC_EXE     magic      \
  "$_r2g_toolchain_root/magic/bin/magic" "$HOME/.local/bin/magic" \
  "$_r2g_conda_bin/magic" /usr/local/bin/magic /usr/bin/magic

# Netgen ships under several names; try each in turn
if [[ -z "${NETGEN_EXE:-}" ]]; then
  if [[ -x "$_r2g_toolchain_root/netgen/bin/netgen" ]]; then
    export NETGEN_EXE="$_r2g_toolchain_root/netgen/bin/netgen"
  elif [[ -x "$HOME/.local/bin/netgen" ]]; then
    export NETGEN_EXE="$HOME/.local/bin/netgen"
  fi
  [[ -z "${NETGEN_EXE:-}" && -n "$_r2g_conda_bin" && -x "$_r2g_conda_bin/netgen-lvs" ]] && export NETGEN_EXE="$_r2g_conda_bin/netgen-lvs"
  [[ -z "${NETGEN_EXE:-}" && -n "$_r2g_conda_bin" && -x "$_r2g_conda_bin/netgen" ]] && export NETGEN_EXE="$_r2g_conda_bin/netgen"
  for _cand in netgen-lvs netgen; do
    [[ -n "${NETGEN_EXE:-}" ]] && break
    if _hit="$(command -v "$_cand" 2>/dev/null)"; then
      if [[ -n "$_hit" ]]; then export NETGEN_EXE="$_hit"; break; fi
    fi
  done
fi
: "${NETGEN_EXE:=}"
if [[ "$_r2g_hermetic" != "1" ]]; then
  [[ -z "$NETGEN_EXE" && -x /usr/bin/netgen-lvs ]] && export NETGEN_EXE=/usr/bin/netgen-lvs
  [[ -z "$NETGEN_EXE" && -x /usr/local/bin/netgen ]] && export NETGEN_EXE=/usr/local/bin/netgen
fi

_r2g_detect STA_EXE       sta        \
  "$_r2g_toolchain_root/sta/bin/sta" \
  /usr/local/bin/opensta /usr/local/bin/sta /usr/bin/opensta

if [[ -z "${STA_EXE:-}" ]]; then
  _r2g_detect STA_EXE     opensta    /usr/local/bin/opensta /usr/bin/opensta
fi

# --- 5. PDK autodetect ---------------------------------------------------
# conda-staged sky130A (open_pdks.sky130a → <conda>/envs/<env>/share/pdk, e.g. from
# eda-install's pdk tier). Only adopt a candidate that actually contains sky130A, so a
# conda PDK is discovered without ever shadowing an explicit/well-known PDK_ROOT that
# already has it.
if [[ -z "${PDK_ROOT:-}" || ! -d "${PDK_ROOT}/sky130A" ]]; then
  if [[ -n "$_r2g_toolchain_root" && -d "$_r2g_toolchain_root/pdks/sky130A" ]]; then
    export PDK_ROOT="$_r2g_toolchain_root/pdks"
  fi
fi

if [[ -z "${PDK_ROOT:-}" || ! -d "${PDK_ROOT}/sky130A" ]]; then
  for _base in "${_r2g_conda_bases[@]}"; do
    [[ -z "$_base" ]] && continue
    for _cand in "$_base/envs/$_r2g_env/share/pdk" "$_base/share/pdk"; do
      if [[ -d "$_cand/sky130A" ]]; then export PDK_ROOT="$_cand"; break 2; fi
    done
  done
fi

# hand-staged PDK on a big volume (a tree rsync'd out of a retired conda env — the
# 2026-07-09 layout: /proj/workarea/$USER/sky130_pdk/share/pdk/sky130A). Same
# contains-sky130A gate as above (failure-patterns.md #29).
if [[ -z "${PDK_ROOT:-}" || ! -d "${PDK_ROOT}/sky130A" ]]; then
  for _p in "/proj/workarea/$USER/sky130_pdk/share/pdk" "/proj/$USER/sky130_pdk/share/pdk" \
            "$HOME/sky130_pdk/share/pdk"; do
    if [[ -d "$_p/sky130A" ]]; then export PDK_ROOT="$_p"; break; fi
  done
fi

# Host-wide PDKs are the final fallback.  User-local/conda PDKs above must win
# so a bootstrap cannot silently mix a personal toolchain with /opt collateral.
if [[ "$_r2g_hermetic" != "1" && -z "${PDK_ROOT:-}" ]]; then
  for _p in /opt/pdks "$HOME/pdks" /usr/local/share/pdks; do
    if [[ -d "$_p" ]]; then export PDK_ROOT="$_p"; break; fi
  done
fi

if [[ -n "${PDK_ROOT:-}" && -d "$PDK_ROOT/sky130A" ]]; then
  export SKY130A_DIR="$PDK_ROOT/sky130A"
fi

unset _r2g_orfs_openroad _r2g_orfs_yosys _cand _hit _p _detected _base _r2g_env \
      _r2g_conda_bases _r2g_conda_bin _r2g_var _r2g_caller_env _r2g_pre_system_env \
      _r2g_toolchain_root _r2g_prepend_lib _r2g_hermetic _r2g_value
# Restore caller's options
case "$_r2g_saved_opts" in
  *e*) set -e ;;
esac
case "$_r2g_saved_opts" in
  *u*) set -u ;;
esac
unset _r2g_saved_opts
true
