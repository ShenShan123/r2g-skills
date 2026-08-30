#!/usr/bin/env bash
# Tier: graph (optional) — the torch + torch_geometric + pandas venv the def-graph
# PyG graph-assembly stage needs (run_graphs.sh). Root-free venv + pip (CPU wheels)
# on the big volume, never $HOME. Prefer a Python shipped by the direct bundle so
# a Debian host without python3-venv does not force the legacy Conda path.
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
_direct_root="${R2G_TOOLCHAIN_ROOT:-${R2G_PREFIX:-}}"
_base_python="${R2G_GRAPH_BASE_PYTHON:-}"
if [[ -z "$_base_python" && -n "$_direct_root" ]]; then
  for _candidate in "$_direct_root/oss-cad-suite/py3bin/python3.11" \
                    "$_direct_root/oss-cad-suite/py3bin/python3"; do
    if [[ -x "$_candidate" ]]; then _base_python="$_candidate"; break; fi
  done
fi
if [[ -z "$_base_python" ]]; then _base_python="$(command -v python3 2>/dev/null || true)"; fi
[[ -n "$_base_python" && -x "$_base_python" ]] || die "python3 (or direct-bundle Python) required to build the graph venv"

# The OSS CAD Suite Python is a relocatable wrapper, not a normal ELF Python;
# `venv` would copy an interpreter that cannot find libpython/loader at runtime.
# Keep the graph environment isolated with a target site-packages directory and
# a tiny wrapper that re-enters the bundled interpreter with that directory on
# PYTHONPATH.  A normal system Python still uses the conventional venv path.
if [[ "$_base_python" == *"/oss-cad-suite/py3bin/python"* ||
      "$_base_python" == *"/oss-cad-suite/py3bin/python3"* ||
      "$_base_python" == *"/oss-cad-suite/py3bin/python3.11"* ]]; then
  _site="$_venv/lib/python3.11/site-packages"
  _pip="$(dirname "$_base_python")/pip3.11"
  [[ -x "$_pip" ]] || _pip="$(dirname "$_base_python")/pip3"
  [[ -x "$_pip" ]] || die "direct-bundle pip not found beside $_base_python"
  run mkdir -p "$_site" "$_venv/bin"
  run "$_pip" install --upgrade --target "$_site" torch --index-url "$TORCH_CPU_INDEX" \
    || die "torch install failed"
  run "$_pip" install --upgrade --target "$_site" torch_geometric pandas \
    || die "torch_geometric/pandas install failed"
  if [[ "$DRY" == "1" ]]; then
    printf '+ portable graph wrapper %s\n' "$_venv/bin/python"
  else
    # A previous failed venv may leave python/python3/python3.11 symlinks that
    # point at one another. Remove only these exact entry points before writing
    # the portable wrapper, preserving the installed site-packages.
    rm -f -- "$_venv/bin/python" "$_venv/bin/python3" "$_venv/bin/python3.11"
    cat >"$_venv/bin/python" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="$_site\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$_base_python" "\$@"
EOF
    chmod 755 "$_venv/bin/python"
    ln -sfn python "$_venv/bin/python3"
    ln -sfn python "$_venv/bin/python3.11"
  fi
  _graph_python="$_venv/bin/python"
else
  run "$_base_python" -m venv --clear "$_venv" \
    || die "venv create failed (install python3-venv or set R2G_GRAPH_BASE_PYTHON)"
  run "$_venv/bin/pip" install --upgrade pip                            || die "pip upgrade failed"
  run "$_venv/bin/pip" install torch --index-url "$TORCH_CPU_INDEX"     || die "torch install failed"
  run "$_venv/bin/pip" install torch_geometric pandas                  || die "torch_geometric/pandas install failed"
  _graph_python="$_venv/bin/python"
fi
log "graph environment → $_venv ; set R2G_GRAPH_PYTHON=$_graph_python (write_env_local.sh pins it)"
