#!/usr/bin/env bash
# Tier: pdk — pinned SKY130/GF180 PDKs for the selected strict platforms.
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

selected="${R2G_STRICT_PLATFORMS:-}"
selected=" ${selected//,/ } "
pdks=()
if [[ -z "${R2G_STRICT_PLATFORMS:-}" || "$selected" == *" sky130hd "* || "$selected" == *" sky130hs "* ]]; then
  pdks+=(sky130A)
fi
[[ "$selected" == *" gf180 "* ]] && pdks+=(gf180mcuC)
[[ ${#pdks[@]} != 0 ]] || { log 'selected platforms do not require an external PDK'; exit 0; }

present=1
for pdk in "${pdks[@]}"; do
  if [[ ! -f "${PDK_ROOT:-}/$pdk/libs.tech/magic/$pdk.tech" \
        || ! -f "${PDK_ROOT:-}/$pdk/libs.tech/netgen/${pdk}_setup.tcl" ]]; then
    present=0
  fi
done
if [[ "$FORCE" != 1 && "$present" == 1 ]]; then
  log "selected PDKs already satisfied ($PDK_ROOT); strict capability checks still apply"
  exit 0
fi
# Keep selected PDKs under one root, including when only one was previously
# installed elsewhere. Each conda package remains idempotent at its pinned build.
pin="$HERE/../../references/pdk_packages.json"
for pdk in "${pdks[@]}"; do
  spec="$(python3 - "$pin" "$pdk" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))[sys.argv[2]]
print('='.join(d[k] for k in ('name', 'version', 'build')))
PY
  )" || die "invalid PDK package pin: $pdk"
  conda_env_install "$spec" || die "$pdk install failed (see HINT above)"
  if [[ "$DRY" != 1 ]]; then
    conda="$(ensure_conda)" || die 'conda unavailable after PDK installation'
    base="$(dirname "$(dirname "$conda")")"
    python3 - "$pin" "$pdk" "$base/envs/$CONDA_ENV" <<'PY' || die "PDK package identity mismatch: $pdk"
import json, pathlib, sys
d = json.load(open(sys.argv[1]))[sys.argv[2]]
prefix = pathlib.Path(sys.argv[3])
meta = prefix / 'conda-meta' / ('-'.join(d[k] for k in ('name', 'version', 'build')) + '.json')
actual = json.loads(meta.read_text())
if actual.get('sha256') != d['sha256']:
    raise SystemExit('Installed PDK package does not match the experiment package SHA-256')
for rel in ('magic/{0}.tech', 'netgen/{0}_setup.tcl'):
    if not (prefix / 'share/pdk' / sys.argv[2] / 'libs.tech' / rel.format(sys.argv[2])).is_file():
        raise SystemExit('Installed PDK is missing signoff collateral')
PY
  fi
done

# The package stages the tree under <conda-base>/envs/<env>/share/pdk/sky130A.
_conda="$(ensure_conda 2>/dev/null || true)"
if [[ "$DRY" != 1 && -n "$_conda" ]]; then
  _base="$(dirname "$(dirname "$_conda")")"
  _bigv="$(pick_big_volume)" || die 'installation prefix unavailable'
  mkdir -p "$_bigv/.r2g-install"
  printf 'export PDK_ROOT=%q\n' "$_base/envs/$CONDA_ENV/share/pdk" > "$_bigv/.r2g-install/pdk.env"
  log "PDK_ROOT=$_base/envs/$CONDA_ENV/share/pdk (write_env_local.sh pins it; sky130A under it)"
fi
