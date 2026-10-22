#!/usr/bin/env bash
# nangate45 LVS FALLBACK: KLayout extracts, Netgen compares.
#
# For a design whose KLayout LVS gave NO verdict (the 0.30.7 comparer crashes
# with SIGSEGV in sort_circuit / derive_node_identities on ~24 nangate45 dev
# designs; four other KLayout fixes were tried and failed). It never replaces
# a verdict KLayout did give. Both arms use it under the same rule, so it is
# apparatus, never an r2g repair.
#
# Only the comparison algorithm changes. Inputs are the ones the baseline LVS
# read (the run's 6_final.gds, and 6_final.cdl + the platform's cell CDL
# concatenated exactly as run_lvs.sh does); extraction and netlist
# normalisation are the platform deck's own lines 1-384, in the same image with
# the same KLayout 0.30.7 (FreePDK45_extract.lylvs); the comparer is the
# Netgen 1.5.272 the sky130hd LVS uses.
#
# Usage: lvs45_fallback.sh <run_dir> <out_dir>      (TIMEOUT per step, s)
# Prints one line:  verdict=<match|mismatch|no_verdict> <detail>
set -uo pipefail
D=/proj/workarea/yangao
RUN=$(readlink -f "$1"); mkdir -p "$2"; OUT=$(readlink -f "$2")
IMAGE=localhost/r2g-orfs:26Q3
CONDA=$D/miniconda3/envs/eda
L=$D/lvs45
TIMEOUT=${TIMEOUT:-21600}
for f in 6_final.gds config.mk; do
  [ -s "$RUN/$f" ] || { echo "verdict=no_verdict missing_$f"; exit 3; }
done
TOP=$(sed -n 's/^export DESIGN_NAME *= *//p' "$RUN/config.mk" | tr -d ' ')
NAME=lvsng_$(printf '%s' "$RUN" | md5sum | cut -c1-12)
podman rm -f "$NAME" >/dev/null 2>&1
timeout --kill-after=120 $(( 2 * TIMEOUT + 600 )) \
podman run --rm --name "$NAME" -v "$RUN":/rundir:ro,Z -v "$OUT":/out:Z \
  -v "$L":"$L":ro,Z -v "$CONDA":"$CONDA":ro,Z "$IMAGE" bash -lc "
  M=\$ORFS_ROOT/flow/platforms/nangate45/cdl/NangateOpenCellLibrary.cdl
  # The run's own 6_final.cdl is what the baseline LVS read. A run whose
  # sign-off never wrote it back (C02309 lsu) gets it from 6_final.odb with the
  # same write_cdl -masters step as run_lvs.sh's frozen-layout path.
  if [ -s /rundir/6_final.cdl ]; then cp /rundir/6_final.cdl /out/6_final.cdl
  elif [ -s /rundir/6_final.odb ]; then
    printf 'read_db /rundir/6_final.odb\\nwrite_cdl -masters %s /out/6_final.cdl\\n' \"\$M\" > /out/write_cdl.tcl
    openroad -no_init -exit /out/write_cdl.tcl > /out/write_cdl.log 2>&1
    echo cdl_regenerated_from_odb >> /out/rc.txt
  fi
  [ -s /out/6_final.cdl ] || { echo no_cdl >> /out/rc.txt; exit 0; }
  cat /out/6_final.cdl \$M > /out/concat.cdl
  klayout -v > /out/tools.txt; $CONDA/bin/netgen -batch quit 2>/dev/null | head -1 >> /out/tools.txt
  timeout $TIMEOUT klayout -b -r $L/FreePDK45_extract.lylvs -rd in_gds=/rundir/6_final.gds \
    -rd cdl_file=/out/concat.cdl -rd report_file=/out/extract.lvsdb -rd target_netlist=/out/target.cir \
    -rd extracted_out=/out/layout.cir -rd schematic_out=/out/schematic.cir > /out/extract.log 2>&1
  echo \"extract_rc=\$?\" >> /out/rc.txt
  [ -s /out/layout.cir ] && [ -s /out/schematic.cir ] || exit 0
  cd /out
  tl=\$(python3 -I $L/reorder_spice.py layout.cir layout_bu.cir --top $TOP) || exit 0
  ts=\$(python3 -I $L/reorder_spice.py schematic.cir schematic_bu.cir --top $TOP) || exit 0
  echo \"top_layout=\$tl top_schematic=\$ts\" >> /out/rc.txt
  timeout $TIMEOUT $CONDA/bin/netgen -batch lvs \"layout_bu.cir \$tl\" \"schematic_bu.cir \$ts\" \
    $L/nangate45_netgen_setup.tcl netgen.rpt -json > netgen.log 2>&1
  echo \"netgen_rc=\$?\" >> /out/rc.txt
" > "$OUT/podman.log" 2>&1
rc=$?
[ $rc -eq 124 ] && podman rm -f "$NAME" >/dev/null 2>&1
if [ ! -s "$OUT/layout.cir" ]; then
  echo "verdict=no_verdict extract_failed($(tr '\n' ' ' < "$OUT/rc.txt" 2>/dev/null))"; exit 4
fi
final=$(grep -h "^Final result" "$OUT/netgen.rpt" 2>/dev/null | tail -1)
# Match ONLY on Netgen's unqualified success line. Any other final result is a
# mismatch -- including "with port errors"/"property errors" and "Top level
# cell failed pin matching", which is how Netgen words the deliberate pin-swap
# negative control (its report also says "Netlists do not match" and names the
# two swapped nets). No final line at all means Netgen never finished: no verdict.
case "$final" in
  "Final result: Circuits match uniquely.") echo "verdict=match $final" ;;
  "Final result:"*) echo "verdict=mismatch $final" ;;
  *) echo "verdict=no_verdict netgen($(tr '\n' ' ' < "$OUT/rc.txt" 2>/dev/null))" ;;
esac
