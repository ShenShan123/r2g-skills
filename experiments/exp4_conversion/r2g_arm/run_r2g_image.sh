#!/usr/bin/env bash
# Exp 4 (conversion) -- R2G arm, IMAGE task, one design: shim + def-graph's run_images.sh. 0 model calls.
#
#   run_r2g_image.sh <skill_root> <design_dir> <platform_dir> <project_dir> <out_dir>
#
# design_dir holds the same two layout files the LLM image converters get (3_place.def, 5_route.def)
# plus config.mk (DESIGN_NAME only). run_images.sh needs an ORFS backend run, so the shim writes one
# (layout after the validation study's make_defgraph_project.sh, reduced to the files this product
# reads): backend/RUN_pilot500/results/{3_place.odb, 6_final.def(=5_route.def), 6_final.odb} +
# run-meta.json, ODBs by OpenROAD read_lef + read_def + write_db. No SPEF/SDC is given (the image
# product does not use them). R2G_SIGNOFF_GATE=warn: there are no DRC/LVS reports for these
# re-run layouts, the gate still runs and records its verdict.
# Output: <out_dir>/features.npz (as built), labels.npz = def-graph's labels minus `ir_drop` (power-
# related channels are out of scope), image_meta.json + verify.json (def-graph's own self-check).
set -euo pipefail
S=$(cd "$1" && pwd); D=$(cd "$2" && pwd); P=$(cd "$3" && pwd); J="$4"; O="$5"
OR=${OPENROAD_EXE:-openroad}
mkdir -p "$O"; [[ -e "$J" ]] && { echo "project exists: $J" >&2; exit 2; }
t0=$(date +%s.%N)
RUNDIR="$J/backend/RUN_pilot500"; RUN="$RUNDIR/results"; mkdir -p "$J/constraints" "$RUN"
DN=$(sed -nE 's/^\s*export\s+DESIGN_NAME\s*=\s*(\S+).*/\1/p' "$D/config.mk" | head -1)
printf 'export DESIGN_NAME = %s\nexport PLATFORM = nangate45\n' "$DN" > "$J/constraints/config.mk"
cp "$D/5_route.def" "$RUN/6_final.def"
for s in 3_place 6_final; do
  src="$D/$s.def"; [[ $s == 6_final ]] && src="$RUN/6_final.def"
  printf 'read_lef %s/NangateOpenCellLibrary.tech.lef\nread_lef %s/NangateOpenCellLibrary.macro.mod.lef\nread_def %s\nwrite_db %s\n' \
    "$P" "$P" "$src" "$RUN/$s.odb" > "$RUNDIR/make_$s.tcl"
  "$OR" -no_splash -exit "$RUNDIR/make_$s.tcl" > "$RUNDIR/make_$s.log" 2>&1
  [[ -s "$RUN/$s.odb" ]] || { echo "ODB build failed: $s" >&2; exit 3; }
done
printf '{"design_name": "%s", "platform": "nangate45", "flow_variant": "base", "source": "exp4 pilot500 shim"}\n' "$DN" > "$RUNDIR/run-meta.json"
t1=$(date +%s.%N)
rc=0
R2G_SIGNOFF_GATE=warn bash "$S/scripts/flow/run_images.sh" "$J" nangate45 > "$J/run_images.log" 2>&1 || rc=$?
t2=$(date +%s.%N)
if [[ -f "$J/images/features.npz" && -f "$J/images/labels.npz" ]]; then
  cp "$J/images/features.npz" "$O/features.npz"
  python3 - "$J/images/labels.npz" "$O/labels.npz" <<'EOF'
import sys, numpy as np
z = np.load(sys.argv[1], allow_pickle=False)
np.savez_compressed(sys.argv[2], **{k: z[k] for k in z.files if k != "ir_drop"})
EOF
  cp "$J/images/image_meta.json" "$O/" 2>/dev/null || true
  cp "$J/images/verify.json" "$O/r2g_verify_images.json" 2>/dev/null || true
fi
python3 - "$O/r2g_timing.json" "$t0" "$t1" "$t2" "$rc" <<'EOF'
import json, sys
p, t0, t1, t2, rc = sys.argv[1], *map(float, sys.argv[2:5]), int(sys.argv[5])
json.dump({"shim_seconds": round(t1 - t0, 2), "skill_seconds": round(t2 - t1, 2), "run_images_rc": rc}, open(p, "w"), indent=1)
EOF
exit $rc
