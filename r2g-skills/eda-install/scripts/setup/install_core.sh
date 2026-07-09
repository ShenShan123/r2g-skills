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
_orfs="${ORFS_ROOT:-$_bigv/OpenROAD-flow-scripts}"

# 1) ORFS checkout (git only, no build) — provides flow/ + platforms/.
if [[ ! -f "$_orfs/flow/Makefile" ]]; then
  have_cmd git || die "git required to clone ORFS"
  run git clone --recursive "$ORFS_URL" "$_orfs" || die "ORFS clone failed"
fi

# 2) the openroad + yosys binaries
if [[ "$BUILD" == "1" ]]; then
  _have_sudo || hint "--build without root: etc/DependencyInstaller.sh installs system libs and may fail"
  run bash "$_orfs/etc/DependencyInstaller.sh"
  run bash "$_orfs/build_openroad.sh" --local || die "build_openroad.sh failed"
  log "ORFS built from source at $_orfs (openroad+yosys under tools/install/)"
else
  conda_env_install openroad yosys || die "openroad/yosys conda install failed (or pass --build for a source build)"
  log "ORFS cloned (no build) at $_orfs; openroad/yosys from conda '$CONDA_ENV' — write_env_local.sh pins ORFS_ROOT + binaries"
fi
