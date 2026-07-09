#!/usr/bin/env bash
# Tier: pdk (optional) — the sky130A PDK for Magic DRC / Netgen LVS.
# Root-free via conda `open_pdks.sky130a` into the 'eda' env on the big volume
# (~8GB — deliberately NOT $HOME). Never `volare` (its httpx needs socksio behind
# a SOCKS proxy and the GitHub release API rate-limits unauthenticated listing).
set -uo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/../flow/_env.sh" 1>&2
# shellcheck source=/dev/null
source "$HERE/_setup_lib.sh"
setup_parse "$@"

if [[ "$FORCE" != "1" && -n "${SKY130A_DIR:-}" ]]; then
  log "sky130A PDK already satisfied ($SKY130A_DIR)"; exit 0
fi
conda_env_install open_pdks.sky130a || die "open_pdks.sky130a install failed (see HINT above)"

# The package stages the tree under <conda-base>/envs/<env>/share/pdk/sky130A.
_conda="$(ensure_conda 2>/dev/null || true)"
if [[ -n "$_conda" ]]; then
  _base="$(dirname "$(dirname "$_conda")")"
  log "PDK_ROOT=$_base/envs/$CONDA_ENV/share/pdk (write_env_local.sh pins it; sky130A under it)"
fi
