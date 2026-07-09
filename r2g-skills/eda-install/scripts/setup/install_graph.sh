#!/usr/bin/env bash
# Tier: graph (optional) — the torch + torch_geometric + pandas venv the def-graph
# PyG graph-assembly stage needs (run_graphs.sh). Root-free venv + pip (CPU wheels)
# on the big volume, never $HOME.
set -uo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/_setup_lib.sh"
setup_parse "$@"

# Present when some python already imports the trio (same probe as run_graphs.sh).
_gp=""
for _p in "${R2G_GRAPH_PYTHON:-}" python3; do
  [[ -z "$_p" ]] && continue
  if "$_p" -c "import torch, torch_geometric, pandas" >/dev/null 2>&1; then _gp="$_p"; break; fi
done
if [[ "$FORCE" != "1" && -n "$_gp" ]]; then
  log "graph venv already satisfied ($_gp)"; exit 0
fi

_bigv="$(pick_big_volume)" || die "no writable volume with >= ${R2G_MIN_FREE_GB:-15}GB free — pass --prefix DIR"
_venv="$_bigv/$GRAPH_VENV_SUBPATH"
have_cmd python3 || die "python3 required to build the graph venv"

run python3 -m venv "$_venv"                                          || die "venv create failed"
run "$_venv/bin/pip" install --upgrade pip                            || die "pip upgrade failed"
run "$_venv/bin/pip" install torch --index-url "$TORCH_CPU_INDEX"     || die "torch install failed"
run "$_venv/bin/pip" install torch_geometric pandas                  || die "torch_geometric/pandas install failed"
log "graph venv → $_venv ; set R2G_GRAPH_PYTHON=$_venv/bin/python (write_env_local.sh pins it)"
