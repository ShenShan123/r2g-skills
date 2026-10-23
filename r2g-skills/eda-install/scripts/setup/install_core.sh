#!/usr/bin/env bash
# Tier: core (required) — OpenROAD-flow-scripts + the openroad & yosys binaries.
#
# Default (no root): download the r2g toolchain bundle -- the exact OpenROAD +
# Yosys build every r2g dataset was produced with, relocatable, ~100 MB -- verify
# its sha256, unpack it, and shallow-clone ORFS at the commit that build was
# validated against (references/toolchain_bundle.json pins all three). Needs a
# host glibc >= the bundle's floor (2.35: Ubuntu 22.04+, Debian 12+).
#
# The conda litex-hub path this tier used to take is gone: that channel froze
# openroad/yosys in Feb 2024 (yosys 0.38) and conda-forge has no openroad, so
# conda binaries + a current ORFS could not synthesize at all (fresh-user test,
# 2026-10-23: the ORFS canonicalize step fails on the nangate45 liberty).
#
# With --build (needs root for DependencyInstaller): clone ORFS recursively and
# build openroad+yosys from source under the checkout (30-60 min).
set -uo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/../flow/_env.sh" 1>&2
# shellcheck source=/dev/null
source "$HERE/_setup_lib.sh"
setup_parse "$@"

BUILD=0
for _a in "${SETUP_REST[@]:-}"; do [[ "$_a" == "--build" ]] && BUILD=1; done
PIN_JSON="$HERE/../../references/toolchain_bundle.json"

# Already satisfied only by a checkout we can actually run in: ORFS writes into
# its own flow/ tree, so a read-only system checkout (another user's /opt) does
# not count, however complete it looks.
if [[ "$FORCE" != "1" && -n "${ORFS_ROOT:-}" && -w "${ORFS_ROOT:-/nonexistent}/flow" \
      && -n "${OPENROAD_EXE:-}" && -n "${YOSYS_EXE:-}" ]]; then
  log "core already satisfied (ORFS=$ORFS_ROOT, openroad=$OPENROAD_EXE)"; exit 0
fi

_bigv="$(pick_big_volume)" || die "no writable volume with >= ${R2G_MIN_FREE_GB:-15}GB free — pass --prefix DIR"
_orfs="$_bigv/OpenROAD-flow-scripts"
if [[ -n "${ORFS_ROOT:-}" && -f "$ORFS_ROOT/flow/Makefile" && ! -w "$ORFS_ROOT/flow" ]]; then
  hint "ignoring read-only ORFS at $ORFS_ROOT (ORFS writes into flow/); installing our own into $_orfs"
fi
have_cmd git || die "git required to fetch ORFS"

_pin() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$PIN_JSON" "$1"; }

if [[ "$BUILD" == "1" ]]; then
  [[ -f "$_orfs/flow/Makefile" ]] || run git clone --recursive "$ORFS_URL" "$_orfs" || die "ORFS clone failed"
  _have_sudo || hint "--build without root: etc/DependencyInstaller.sh installs system libs and may fail"
  run bash "$_orfs/etc/DependencyInstaller.sh"
  run bash "$_orfs/build_openroad.sh" --local || die "build_openroad.sh failed"
  log "ORFS built from source at $_orfs (openroad+yosys under tools/install/)"
  OR_EXE=""; YS_EXE=""; STA=""          # under tools/install: _env.sh finds them
else
  [[ -f "$PIN_JSON" ]] || die "missing $PIN_JSON (which toolchain bundle to install)"
  url="$(_pin url)"; sha="$(_pin sha256)"; name="$(_pin name)"
  commit="$(_pin orfs_commit)"; floor="$(_pin glibc_min)"

  # glibc floor: the one thing the bundle cannot carry
  host="$(getconf GNU_LIBC_VERSION 2>/dev/null | awk '{print $2}')"
  if [[ -z "$host" ]] || ! python3 -c 'import sys; a,b=(tuple(map(int,x.split("."))) for x in sys.argv[1:3]); sys.exit(0 if a>=b else 1)' "$host" "$floor"; then
    die "this system's glibc (${host:-unknown}) is older than the toolchain bundle needs ($floor; Ubuntu 22.04+ / Debian 12+). Use --build to compile from source instead."
  fi

  # 1) the binaries
  dest="$_bigv/$name"
  if [[ -x "$dest/openroad/bin/openroad.bin" && -f "$dest/BUNDLE.json" ]]; then
    log "toolchain bundle already unpacked: $dest"
  else
    mkdir -p "$_bigv/downloads"
    tgz="$_bigv/downloads/$name.tar.gz"
    if [[ ! -f "$tgz" ]] || ! echo "$sha  $tgz" | sha256sum -c --status; then
      have_cmd curl || die "curl required to download the toolchain bundle"
      log "downloading the toolchain bundle (~100 MB): $url"
      [[ "$DRY" == "1" ]] || curl -fL --retry 3 -o "$tgz.part" "$url" || die "download failed: $url"
      [[ "$DRY" == "1" ]] || mv "$tgz.part" "$tgz"
    fi
    if [[ "$DRY" != "1" ]]; then
      echo "$sha  $tgz" | sha256sum -c --status || die "toolchain bundle sha256 mismatch (expected $sha) — refusing to install"
      # --no-same-owner: as root, tar would restore the packager's uid (and fails
      # outright inside a user namespace); the files belong to whoever installs
      tar --no-same-owner -C "$_bigv" -xzf "$tgz" || die "could not unpack $tgz"
    fi
  fi
  OR_EXE="$dest/openroad/bin/openroad"; YS_EXE="$dest/yosys/bin/yosys"; STA="$dest/openroad/bin/sta"
  if [[ "$DRY" != "1" ]]; then
    "$OR_EXE" -version >/dev/null 2>&1 || die "bundled openroad does not start on this machine ($OR_EXE -version)"
    "$YS_EXE" -V >/dev/null 2>&1 || die "bundled yosys does not start on this machine ($YS_EXE -V)"
  fi

  # 2) ORFS flow/ at the commit this build was validated with (shallow, no
  #    submodules: the tools/ sources are not used, the binaries are above)
  if [[ ! -d "$_orfs/.git" ]]; then
    run git init -q "$_orfs" && run git -C "$_orfs" remote add origin "$ORFS_URL"
  fi
  if [[ "$(git -C "$_orfs" rev-parse HEAD 2>/dev/null)" != "$commit" ]]; then
    log "fetching ORFS at the validated commit ${commit:0:10} (shallow)"
    run git -C "$_orfs" fetch -q --depth 1 origin "$commit" || die "ORFS fetch failed"
    run git -C "$_orfs" checkout -q -f FETCH_HEAD || die "ORFS checkout failed"
  fi
  log "ORFS ${commit:0:10} at $_orfs; openroad/yosys from the bundle $dest"
fi

# Hand the CHOSEN toolchain to bootstrap's pin step. A child cannot export into
# its parent, and without this the pin re-ran autodetection and pinned whatever
# ORFS/openroad it found first on the system instead of the ones just installed.
if [[ "$DRY" != "1" ]]; then
  mkdir -p "$_bigv/.r2g-install"
  {
    echo "export ORFS_ROOT=\"$_orfs\""
    [[ -n "$OR_EXE" ]] && echo "export OPENROAD_EXE=\"$OR_EXE\""
    [[ -n "$YS_EXE" ]] && echo "export YOSYS_EXE=\"$YS_EXE\""
    [[ -n "$STA" ]] && echo "export STA_EXE=\"$STA\""
  } > "$_bigv/.r2g-install/core.env"
  log "toolchain choice recorded for the pin step: $_bigv/.r2g-install/core.env"
fi
