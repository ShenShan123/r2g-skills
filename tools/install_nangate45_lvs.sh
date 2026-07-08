#!/usr/bin/env bash
# Install the bundled FreePDK45.lylvs rule into the user's ORFS nangate45
# platform directory. The upstream The-OpenROAD-Project/OpenROAD-flow-scripts
# does NOT ship an LVS rule for nangate45; ORFS's `make lvs` silently
# emits "skipped" without one. This script materializes the rule.
set -euo pipefail
SRC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/../r2g-skills/signoff-loop/assets/platforms/nangate45/lvs/FreePDK45.lylvs"
ORFS_ROOT="${ORFS_ROOT:-${1:-}}"
if [[ -z "$ORFS_ROOT" ]]; then
  if [[ -d "$HOME/OpenROAD-flow-scripts" ]]; then ORFS_ROOT="$HOME/OpenROAD-flow-scripts"
  elif [[ -d "/opt/OpenROAD-flow-scripts" ]]; then ORFS_ROOT="/opt/OpenROAD-flow-scripts"
  elif [[ -d "/proj/workarea/user5/OpenROAD-flow-scripts" ]]; then ORFS_ROOT="/proj/workarea/user5/OpenROAD-flow-scripts"
  else
    echo "ERROR: Set ORFS_ROOT or pass path as first arg" >&2
    exit 1
  fi
fi
DEST_DIR="$ORFS_ROOT/flow/platforms/nangate45/lvs"
mkdir -p "$DEST_DIR"
cp "$SRC" "$DEST_DIR/FreePDK45.lylvs"
echo "Installed: $DEST_DIR/FreePDK45.lylvs"
grep "KLAYOUT_LVS_FILE" "$ORFS_ROOT/flow/platforms/nangate45/config.mk" || true
