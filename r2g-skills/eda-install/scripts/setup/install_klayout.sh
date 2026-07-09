#!/usr/bin/env bash
# Tier: klayout (optional) — GDS viewer + the nangate45/asap7/gf180/ihp DRC & LVS
# rule engine. Root-free via the conda 'eda' env (litex-hub).
set -uo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/../flow/_env.sh" 1>&2
# shellcheck source=/dev/null
source "$HERE/_setup_lib.sh"
setup_parse "$@"

if [[ "$FORCE" != "1" && -n "${KLAYOUT_CMD:-}" ]]; then
  log "klayout already satisfied ($KLAYOUT_CMD)"; exit 0
fi
conda_env_install klayout || die "klayout install failed (see HINT above)"
log "klayout installed into conda env '$CONDA_ENV' — run write_env_local.sh to pin the path"
