#!/usr/bin/env bash
# R2G-only adapter: the existing congestion and image stages, with their verifier.
set -euo pipefail
D=$(realpath "$1"); P=$(realpath "$2"); O=$(realpath -m "$3")
S=/r2g/def-graph/scripts/extract
mkdir -p "$O/_r2g/labels" "$O/_r2g/images"
export R2G_PLATFORM
R2G_PLATFORM=$(python3 - "$D/config.mk" <<'PY'
import re, sys
print(re.search(r'^\s*(?:export\s+)?PLATFORM\s*[:?+]?=\s*(\S+)', open(sys.argv[1]).read(), re.M)[1])
PY
)
case "$R2G_PLATFORM" in
  nangate45) export TECH_LEF="$P/NangateOpenCellLibrary.tech.lef" SC_LEF="$P/NangateOpenCellLibrary.macro.mod.lef" ;;
  sky130hd) export TECH_LEF="$P/sky130_fd_sc_hd.tlef" SC_LEF="$P/sky130_fd_sc_hd_merged.lef" ;;
  gf180) export TECH_LEF="$P/gf180mcu_5LM_1TM_9K_9t_tech.lef" SC_LEF="$P/gf180mcu_5LM_1TM_9K_9t_sc.lef" ;;
  *) exit 2 ;;
esac
export R2G_EMIT_GRID=1
python3 "$S/labels/extract_congestion.py" "$D/6_final.def" "$O/_r2g/labels/cell_congestion.csv"
python3 "$S/images/build_images.py" --feature-def "$D/3_place.def" --feature-stage place \
  --labels-dir "$O/_r2g/labels" --out-dir "$O/_r2g/images" --lef "$SC_LEF"
python3 "$S/images/verify_images.py" "$O/_r2g/images" --labels-dir "$O/_r2g/labels" \
  --final-def "$D/6_final.def" --lef "$SC_LEF" --json "$O/_r2g/verify.json"
cp "$O/_r2g/images/features.npz" "$O/features.npz"
cp "$O/_r2g/images/image_meta.json" "$O/image_meta.json"
python3 - "$O/_r2g/images/labels.npz" "$O/labels.npz" <<'PY'
import numpy as np, sys
with np.load(sys.argv[1], allow_pickle=False) as arrays:
    np.savez_compressed(sys.argv[2], **{k: arrays[k] for k in ('congestion', 'congestion_raw')})
PY
