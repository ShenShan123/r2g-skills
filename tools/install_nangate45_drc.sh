#!/usr/bin/env bash
set -euo pipefail
# Install the honest 300:1 FreePDK45 antenna DRC deck into the ORFS checkout.
# Real-fixes-only policy: we do NOT relax the antenna ratio to mask violations.
# Mirrors tools/install_nangate45_lvs.sh.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
# shellcheck source=/dev/null
source "$REPO/r2g-skills/signoff-loop/scripts/flow/_env.sh" >/dev/null 2>&1 || true
: "${FLOW_DIR:?ORFS FLOW_DIR not found; set ORFS_ROOT}"
SRC="$REPO/r2g-skills/signoff-loop/assets/platforms/nangate45/drc/FreePDK45.lydrc"
DST_DIR="$FLOW_DIR/platforms/nangate45/drc"
DST="$DST_DIR/FreePDK45.lydrc"
mkdir -p "$DST_DIR"
[[ -f "$DST" && ! -f "$DST.orig-300ratio" ]] && cp "$DST" "$DST.bak-$(date +%s)" || true
cp "$SRC" "$DST"
n=$(grep -c 'antenna_check(gate, metal.*300.0' "$DST" || true)
if [[ "$n" -ne 10 ]]; then
  echo "ERROR: installed deck is not 300:1 (found $n/10 antenna lines at 300.0)" >&2
  exit 1
fi
echo "Installed honest 300:1 FreePDK45.lydrc → $DST"
