#!/usr/bin/env bash
# regen_gds.sh RUN_DIR OUT_GDS   (on 216)
# Write the final GDS of a harvested ASAP7 run with the baseline image's own ORFS `do-gds` target (KLayout 0.30.7),
# from the run's 6_final.def and its config.mk, re-staging the ASAP7 apparatus files the config references.
# Used so that every signoff input GDS -- whichever server ran the flow -- comes from the same writer.
set -euo pipefail
RUN=$(realpath "$1"); OUT=$(realpath -m "$2")
B=/proj/workarea/yangao/asap7_baseline
W=$(mktemp -d /proj/workarea/yangao/asap7_baseline/regen.XXXXXX)
trap 'rm -rf "$W"' EXIT
NAME=$(sed -n 's/^export DESIGN_NAME *= *//p' "$RUN/config.mk" | head -1)
# A native (203) run rewrote /work/ to its scratch work dir; map it back so the container reads the staged copies.
# The prefix is read from the run's own SDC_FILE (native mode rewrites /work/ to the real work dir), so any scratch
# layout maps back -- repair arms live under repairs/<arm>/scratch/, not *_scratch/ (2026-11-17).
W0=$(sed -n 's#^export SDC_FILE = \(.*\)/constraint\.sdc$#\1#p' "$RUN/config.mk" | head -1)
if [ -n "$W0" ] && [ "$W0" != "/work" ]; then sed "s#$W0/#/work/#g" "$RUN/config.mk" > "$W/config.mk"
else cp "$RUN/config.mk" "$W/config.mk"; fi
# A repair arm's router-only tech LEF (arm_tech.lef) differs from the base one ONLY in spacing rules, which the
# router reads and GDS streaming does not (layers, widths, vias identical): stream with the base LEF.
sed -i 's#/work/arm_tech\.lef#/work/asap7_tech_asym.lef#g' "$W/config.mk"
cp "$RUN/constraint.sdc" "$W/constraint.sdc"
cp "$B/apparatus/asap7_tech_asym.lef" "$W/asap7_tech_asym.lef"
cp "$B/apparatus/asap7sc7p5t_R_edge_pin_window.lef" "$W/asap7_pin_window.lef"
cp "$B/apparatus/pdn_m1m2m3.tcl" "$W/pdn_m1m2m3.tcl"
mkdir -p "$W/results/asap7/$NAME/base" "$W/logs/asap7/$NAME/base" "$W/objects/asap7/$NAME/base" "$W/reports/asap7/$NAME/base"
cp "$RUN/6_final.def" "$W/results/asap7/$NAME/base/6_final.def"
podman run --rm --name "regen_$(basename "$W")" -v "$W:/work" r2g-orfs:26Q3 bash -lc \
  "cd \$ORFS_ROOT/flow 2>/dev/null || cd /r2g_toolchain/OpenROAD-flow-scripts/flow; make DESIGN_CONFIG=/work/config.mk WORK_HOME=/work do-gds" \
  > "$W/regen.log" 2>&1 || { tail -30 "$W/regen.log" >&2; exit 1; }
G=$(ls "$W/results/asap7/$NAME/base/"6_final.gds 2>/dev/null || true)
[ -n "$G" ] || { tail -30 "$W/regen.log" >&2; echo "no GDS written" >&2; exit 1; }
mkdir -p "$(dirname "$OUT")"
cp "$G" "$OUT"
