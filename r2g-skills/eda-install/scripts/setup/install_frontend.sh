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

if [[ "$FORCE" != "1" && -n "${IVERILOG_EXE:-}" && -n "${VVP_EXE:-}" ]] \
   && have_cmd make && have_cmd awk && type -P time >/dev/null; then
  log "frontend already satisfied (iverilog=$IVERILOG_EXE)"
  bash "$HERE/install_slang.sh" "$@"
  exit $?
fi
conda_env_install iverilog verilator make gawk time || die "frontend/runtime install failed (see HINT above)"
if [[ "$DRY" != 1 ]]; then
  conda="$(ensure_conda)" || die 'conda unavailable after frontend installation'
  base="$(dirname "$(dirname "$conda")")"
  bigv="$(pick_big_volume)" || die 'installation prefix unavailable'
  # Expose only runtime utilities, not the entire conda bin directory: do not
  # replace the user's Python or compiler when recalling the EDA pin file.
  runtime="$bigv/runtime-bin"
  mkdir -p "$runtime" "$bigv/.r2g-install"
  for tool in make gawk time; do
    [[ -x "$base/envs/$CONDA_ENV/bin/$tool" ]] || die "missing runtime utility: $tool"
    ln -sf "$base/envs/$CONDA_ENV/bin/$tool" "$runtime/$tool"
  done
  ln -sf "$base/envs/$CONDA_ENV/bin/gawk" "$runtime/awk"
  printf 'export R2G_RUNTIME_BIN=%q\nexport PATH="$R2G_RUNTIME_BIN:$PATH"\n' "$runtime" > "$bigv/.r2g-install/runtime.env"
fi
bash "$HERE/install_slang.sh" "$@" || die 'Slang frontend installation failed'
log "frontend installed into conda env '$CONDA_ENV' — run write_env_local.sh to pin paths"
