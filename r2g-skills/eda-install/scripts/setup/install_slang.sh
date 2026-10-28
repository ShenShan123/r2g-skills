#!/usr/bin/env bash
# The historical sv_frontend recipe needs a separate, compatible Yosys/Slang.
# Do not replace ORFS's Yosys or add the suite's Python to PATH.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/_setup_lib.sh"
setup_parse "$@"
pin="$HERE/../../references/slang_bundle.json"
get() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$pin" "$1"; }
bigv="$(pick_big_volume)" || die 'no writable installation prefix'
release="$(get release)"
dest="$bigv/oss-cad-suite-$release"
archive="$bigv/downloads/oss-cad-suite-$release.tgz"
url="$(get url)"
digest="$(get sha256)"
if [[ "$DRY" == 1 ]]; then
  log "Slang: pinned OSS CAD Suite $release -> $dest (sha256=$digest)"
  exit 0
fi
valid() {
  [[ -x "$dest/bin/yosys" ]] || return 1
  python3 - "$pin" "$dest" <<'PY'
import hashlib, json, pathlib, sys
p = pathlib.Path(sys.argv[2])
for name, expected in json.load(open(sys.argv[1]))['files'].items():
    path = p / name
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise SystemExit(1)
PY
}
if ! valid; then
  mkdir -p "$bigv/downloads"
  if [[ ! -f "$archive" ]] || ! echo "$digest  $archive" | sha256sum -c --status; then
    curl -fL --retry 3 --connect-timeout 30 --max-time 1800 -o "$archive.part" "$url"
    echo "$digest  $archive.part" | sha256sum -c --status || die 'Slang archive SHA-256 mismatch'
    mv "$archive.part" "$archive"
  fi
  mkdir -p "$dest"
  tar --no-same-owner --strip-components=1 -xzf "$archive" -C "$dest"
fi
valid || die 'Slang binaries differ from the historical experiment'
timeout 60 "$dest/bin/yosys" -Q -T -p 'plugin -i slang; help read_slang' >/dev/null \
  || die 'Yosys/Slang does not start'
mkdir -p "$bigv/.r2g-install"
printf 'export R2G_SLANG_YOSYS=%q\n' "$dest/bin/yosys" > "$bigv/.r2g-install/slang.env"
log "historical Yosys/Slang ready at $dest/bin/yosys"
