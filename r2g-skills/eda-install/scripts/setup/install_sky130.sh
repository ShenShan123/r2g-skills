#!/usr/bin/env bash
# Tier: sky130 (optional) — Magic + Netgen, the sky130 DRC/LVS signoff tools.
# Root-free via the conda 'eda' env (litex-hub). Pairs with the pdk tier.
set -uo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/../flow/_env.sh" 1>&2
# shellcheck source=/dev/null
source "$HERE/_setup_lib.sh"
setup_parse "$@"

if [[ "$FORCE" != "1" && -n "${MAGIC_EXE:-}" && -n "${NETGEN_EXE:-}" ]]; then
  log "sky130 signoff tools already satisfied (magic=$MAGIC_EXE)"; exit 0
fi
conda_env_install magic netgen || die "magic/netgen install failed (see HINT above)"
log "magic + netgen installed into conda env '$CONDA_ENV' — run write_env_local.sh to pin paths"
