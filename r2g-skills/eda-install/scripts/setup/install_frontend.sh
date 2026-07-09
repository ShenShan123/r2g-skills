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

if [[ "$FORCE" != "1" && -n "${IVERILOG_EXE:-}" && -n "${VVP_EXE:-}" ]]; then
  log "frontend already satisfied (iverilog=$IVERILOG_EXE)"; exit 0
fi
conda_env_install iverilog verilator || die "frontend install failed (see HINT above)"
log "frontend installed into conda env '$CONDA_ENV' — run write_env_local.sh to pin paths"
