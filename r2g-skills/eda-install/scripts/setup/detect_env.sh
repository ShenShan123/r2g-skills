#!/usr/bin/env bash
# Emit a KEY=VALUE machine + toolchain summary for the bootstrap planner.
#
# Layer 1 of the toolchain bootstrap (detect → plan → install → pin → verify).
# See docs/superpowers/plans/r2g-skills-bootstrap-2026-07-08.md.
#
# It sources the shared scripts/flow/_env.sh for tool discovery (ORFS + every
# binary + PDK), then adds the *machine* facts _env.sh does not gather:
#   - OS family + package manager + sudo availability   (drives channel choice)
#   - conda / mamba presence                            (no-sudo install path)
#   - a python that already has torch+torch_geometric+pandas (graph stage)
#   - a big writable volume with room for the PDK (~8GB) + torch venv, preferring
#     /proj over a full $HOME  (CLAUDE.md hard rule: never install large into $HOME)
#
# CONTRACT: stdout is CLEAN KEY=VALUE lines only (same style as
# resolve_platform_paths.sh) — every key is always emitted (empty value == absent),
# so downstream parsing is total. All diagnostics go to stderr. This script never
# exits non-zero for a missing tool: absence is data, not an error.
set -uo pipefail

_SETUP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_FLOW_DIR="$(cd "$_SETUP_DIR/../flow" && pwd)"

# Discover ORFS + tool binaries + PDK exactly as the flow scripts do. Redirect the
# sourced script's chatter to stderr so our stdout stays a clean KEY=VALUE contract.
# shellcheck source=/dev/null
source "$_FLOW_DIR/_env.sh" 1>&2

emit() { printf '%s=%s\n' "$1" "${2:-}"; }

# --- OS family + package manager ----------------------------------------------
OS_FAMILY="unknown"
case "$(uname -s 2>/dev/null)" in
  Darwin) OS_FAMILY="macos" ;;
  Linux)  OS_FAMILY="$( . /etc/os-release 2>/dev/null; echo "${ID:-linux}" )" ;;
esac

PKG_MGR="none"
for _m in apt-get dnf yum brew; do
  if command -v "$_m" >/dev/null 2>&1; then
    case "$_m" in apt-get) PKG_MGR="apt" ;; *) PKG_MGR="$_m" ;; esac
    break
  fi
done

# --- sudo availability ---------------------------------------------------------
# Already root, or passwordless sudo works → treat as having sudo.
HAVE_SUDO=0
if [[ "${R2G_DIRECT:-0}" == "1" ]]; then
  # Direct provisioning cannot use sudo or package-manager fallback. Do not
  # invoke a privileged capability probe whose result cannot change the plan.
  :
elif [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  HAVE_SUDO=1
elif command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
  HAVE_SUDO=1
fi

# --- conda / mamba -------------------------------------------------------------
HAVE_CONDA=""
if [[ "${R2G_DIRECT:-0}" != "1" ]]; then
for _c in mamba conda; do
  if _hit="$(command -v "$_c" 2>/dev/null)" && [[ -n "$_hit" ]]; then HAVE_CONDA="$_hit"; break; fi
done
if [[ -z "$HAVE_CONDA" ]]; then
  for _c in "$HOME/miniconda3/bin/conda" "$HOME/miniconda3/condabin/conda" \
            "/proj/$USER/miniconda3/bin/conda" "${R2G_PREFIX:-}/miniconda3/bin/conda"; do
    [[ -n "$_c" && -x "$_c" ]] && { HAVE_CONDA="$_c"; break; }
  done
fi
fi

# --- graph-stage python (torch + torch_geometric + pandas) --------------------
# Same import probe run_graphs.sh uses. Honors R2G_GRAPH_PYTHON first.
GRAPH_PYTHON=""
for _p in "${R2G_GRAPH_PYTHON:-}" python3; do
  [[ -z "$_p" ]] && continue
  if "$_p" -c "import torch, torch_geometric, pandas" >/dev/null 2>&1; then
    GRAPH_PYTHON="$(command -v "$_p" 2>/dev/null || echo "$_p")"
    break
  fi
done

# --- big volume for PDK + torch venv ------------------------------------------
# First writable dir with >= R2G_MIN_FREE_GB (default 15) free. Prefer /proj over
# $HOME so a full home partition is never chosen (CLAUDE.md hard rule). An explicit
# --prefix / $R2G_PREFIX wins if it qualifies.
MIN_FREE_GB="${R2G_MIN_FREE_GB:-15}"
BIG_VOLUME=""
BIG_VOLUME_FREE_GB=0
for _cand in "${R2G_PREFIX:-}" "/proj/$USER" "/proj/workarea/$USER" "$HOME" "/tmp"; do
  [[ -z "$_cand" ]] && continue
  [[ -d "$_cand" && -w "$_cand" ]] || continue
  _freekb="$(df -Pk "$_cand" 2>/dev/null | awk 'NR==2{print $4}')"
  [[ -n "$_freekb" ]] || continue
  _freegb=$(( _freekb / 1024 / 1024 ))
  if [[ "$_freegb" -ge "$MIN_FREE_GB" ]]; then
    BIG_VOLUME="$_cand"; BIG_VOLUME_FREE_GB="$_freegb"; break
  fi
done

# --- emit (every key always present) ------------------------------------------
emit OS_FAMILY          "$OS_FAMILY"
emit PKG_MGR            "$PKG_MGR"
emit HAVE_SUDO          "$HAVE_SUDO"
emit HAVE_CONDA         "$HAVE_CONDA"
emit PYTHON3            "$(command -v python3 2>/dev/null || true)"
emit BIG_VOLUME         "$BIG_VOLUME"
emit BIG_VOLUME_FREE_GB "$BIG_VOLUME_FREE_GB"
emit MIN_FREE_GB        "$MIN_FREE_GB"

emit ORFS_ROOT          "${ORFS_ROOT:-}"
emit FLOW_DIR           "${FLOW_DIR:-}"
emit OPENROAD_EXE       "${OPENROAD_EXE:-}"
emit YOSYS_EXE          "${YOSYS_EXE:-}"
emit IVERILOG_EXE       "${IVERILOG_EXE:-}"
emit VVP_EXE            "${VVP_EXE:-}"
emit VERILATOR_EXE      "${VERILATOR_EXE:-}"
emit KLAYOUT_CMD        "${KLAYOUT_CMD:-}"
emit MAGIC_EXE          "${MAGIC_EXE:-}"
emit NETGEN_EXE         "${NETGEN_EXE:-}"
emit STA_EXE            "${STA_EXE:-}"
emit PDK_ROOT           "${PDK_ROOT:-}"
emit SKY130A_DIR        "${SKY130A_DIR:-}"
emit GRAPH_PYTHON       "$GRAPH_PYTHON"

true
