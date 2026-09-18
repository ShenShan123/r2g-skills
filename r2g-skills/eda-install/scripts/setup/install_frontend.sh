#!/usr/bin/env bash
# Tier: frontend (required) — iverilog/vvp + verilator for lint & simulation.
# Root-free via the conda 'eda' env (litex-hub); yosys comes with the core tier.
set -uo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/../flow/_env.sh" 1>&2         # present-state detection (IVERILOG_EXE/VVP_EXE)
# shellcheck source=/dev/null
source "$HERE/_setup_lib.sh"
setup_parse "$@"

if [[ "${R2G_DIRECT:-0}" == "1" ]]; then
  _root="${R2G_TOOLCHAIN_ROOT:-${R2G_PREFIX:-}}"
  [[ -n "$_root" ]] || die "direct frontend requires R2G_TOOLCHAIN_ROOT or R2G_PREFIX"
  _preview=(); [[ "$DRY" == "1" ]] && _preview=(--dry-run)
  _offline=(); [[ "${R2G_DIRECT_OFFLINE:-0}" == "1" ]] && _offline=(--offline)
  python3 "$HERE/direct_archive.py" --component frontend --prefix "$_root" \
    "${_preview[@]}" "${_offline[@]}" || die "locked direct frontend installation failed"
  exit 0
fi

if [[ "$FORCE" != "1" && -n "${IVERILOG_EXE:-}" && -n "${VVP_EXE:-}" ]]; then
  log "frontend already satisfied (iverilog=$IVERILOG_EXE)"; exit 0
fi
conda_env_install iverilog verilator || die "frontend install failed (see HINT above)"
log "frontend installed into conda env '$CONDA_ENV' — run write_env_local.sh to pin paths"
