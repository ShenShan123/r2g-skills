#!/usr/bin/env bash
# Tier: core (required) — OpenROAD-flow-scripts + the openroad & yosys binaries.
#
# Default (no-sudo): git-clone ORFS (for flow/Makefile + platforms/) but do NOT
# build it, and get openroad/yosys from conda litex-hub — _env.sh happily uses a
# build-less ORFS by falling back from $ORFS_ROOT/tools/install to the conda bins.
# With --build (needs root for DependencyInstaller): build openroad+yosys from
# source under the ORFS checkout (~30 min).
set -uo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/../flow/_env.sh" 1>&2
# shellcheck source=/dev/null
source "$HERE/_setup_lib.sh"
setup_parse "$@"

BUILD=0
for _a in "${SETUP_REST[@]:-}"; do [[ "$_a" == "--build" ]] && BUILD=1; done

if [[ "$FORCE" != "1" && -n "${ORFS_ROOT:-}" && -n "${OPENROAD_EXE:-}" && -n "${YOSYS_EXE:-}" ]]; then
  log "core already satisfied (ORFS=$ORFS_ROOT, openroad=$OPENROAD_EXE)"; exit 0
fi

_bigv="$(pick_big_volume)" || die "no writable volume with >= ${R2G_MIN_FREE_GB:-15}GB free — pass --prefix DIR"
# An ORFS_ROOT that _env.sh merely AUTODETECTED (another user's system-wide
# checkout under /opt, say) is not ours to use: ORFS writes into its own flow/
# tree (designs/, results/, logs/), so a read-only one fails the first run with
# "mkdir ... Permission denied" -- which is exactly what a fresh-user install on
# 208 did (2026-10-22), after reporting it had "cloned" ORFS there. Reuse an
# existing checkout only when it is writable; otherwise clone our own.
_orfs="$_bigv/OpenROAD-flow-scripts"
_how="cloned"
if [[ -n "${ORFS_ROOT:-}" && -f "$ORFS_ROOT/flow/Makefile" ]]; then
  if [[ -w "$ORFS_ROOT/flow" ]]; then
    _orfs="$ORFS_ROOT"; _how="reused (writable existing checkout)"
  else
    hint "ignoring read-only ORFS at $ORFS_ROOT (ORFS writes into flow/); cloning our own into $_orfs"
  fi
fi

# 1) ORFS checkout (git only, no build) — provides flow/ + platforms/.
if [[ ! -f "$_orfs/flow/Makefile" ]]; then
  have_cmd git || die "git required to clone ORFS"
  if [[ "$BUILD" == "1" ]]; then
    run git clone --recursive "$ORFS_URL" "$_orfs" || die "ORFS clone failed"
  else
    # no-build: only flow/ (Makefile, scripts, platforms) is used -- the binaries
    # come from conda. A --recursive clone pulled the OpenROAD and Yosys source
    # trees and every submodule (> 4.5 GB, over an hour behind a proxy on the
    # fresh-user test, 2026-10-23) for nothing.
    run git clone --depth 1 "$ORFS_URL" "$_orfs" || die "ORFS clone failed"
  fi
elif [[ "$_how" == "cloned" ]]; then
  _how="reused (already cloned here)"
fi

# 2) the openroad + yosys binaries
if [[ "$BUILD" == "1" ]]; then
  _have_sudo || hint "--build without root: etc/DependencyInstaller.sh installs system libs and may fail"
  run bash "$_orfs/etc/DependencyInstaller.sh"
  run bash "$_orfs/build_openroad.sh" --local || die "build_openroad.sh failed"
  log "ORFS built from source at $_orfs (openroad+yosys under tools/install/)"
else
  conda_env_install openroad yosys || die "openroad/yosys conda install failed (or pass --build for a source build)"
  log "ORFS $_how (no build) at $_orfs; openroad/yosys from conda '$CONDA_ENV' — write_env_local.sh pins ORFS_ROOT + binaries"
fi

# Hand the CHOSEN toolchain to bootstrap's pin step. A child cannot export into
# its parent, and without this the pin re-ran autodetection and pinned whatever
# ORFS/openroad it found first on the system instead of the ones just installed.
if [[ "$DRY" != "1" ]]; then
  _bin=""
  _conda="$(ensure_conda 2>/dev/null || true)"
  [[ -n "$_conda" ]] && _bin="$(cd "$(dirname "$_conda")/.." && pwd)/envs/$CONDA_ENV/bin"
  mkdir -p "$_bigv/.r2g-install"
  {
    echo "export ORFS_ROOT=\"$_orfs\""
    if [[ "$BUILD" != "1" && -x "$_bin/openroad" ]]; then
      echo "export OPENROAD_EXE=\"$_bin/openroad\""
      echo "export YOSYS_EXE=\"$_bin/yosys\""
    fi
  } > "$_bigv/.r2g-install/core.env"
  log "toolchain choice recorded for the pin step: $_bigv/.r2g-install/core.env"
fi
