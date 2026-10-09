#!/usr/bin/env bash
# gf180 full flow + signoff, modelled on chain45.sh so the three corpora stay
# comparable: same 12 workers x 8 cores, same 10800 s stage timeout, same
# driver and the same harvest shape.
#
# Period 10 ns. From the 24-design calibration, the 15 with constrained paths
# give period_min 0.72 to 70.64; 10 ns clears 12 of them, i.e. 80%, which is
# the stated target (both passes and failures present to study) and puts gf180
# at the same stress level as sky130hd's 84.7% at 5 ns. 20 ns would clear 93%
# -- looser than either. The point estimate rests on 15 samples, so the true
# 80% mark could sit anywhere from 8 to 12 ns.
#
# Three platform-config corrections, each measured (runbook 六之六之二/四):
#   --pdn-core-floor-um 110   gf180's PDN needs a 110 um core; sky130hd's 32
#                             fails here (Metal5 wants offset 44.8 + strap 49.3)
#   R2G_PDN_CFG               drops -split_cuts {Metal3 0.128}, which made the
#                             PDN via stacks merge into 0.772 x 1.028 rectangles
#                             and violate V2.1/V3.1 -- 82% of all violations
#   R2G_TAPCELL_TCL           tapcell -distance 100 -> 30; DF.13/14_MV cap the
#                             distance to the nearest tap at 15 um for 5 V
#                             devices, and -distance is the column pitch
# Together these took a 93-cell design from 824 violations to 0, verified from
# 93 to 3,025 cells (the residual CO.6a count is identical before and after).
set -uo pipefail
D=/proj/workarea/yangao
L=$D/chain180.log
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$L"; }

PDN_CFG=$D/gf180_pdnfix/a.cfg
TAP_TCL=$D/gf180_tapfix/d30.tcl
for f in "$PDN_CFG" "$TAP_TCL" "$D/verilog_only.txt"; do
  [ -f "$f" ] || { say "缺少 $f，中止"; exit 1; }
done

say "chain180: gf180 full flow, $(wc -l < "$D/verilog_only.txt") designs @ 10ns"
say "  PDN 覆盖 $PDN_CFG"
say "  tapcell 覆盖 $TAP_TCL"
R2G_PDN_CFG="$PDN_CFG" R2G_TAPCELL_TCL="$TAP_TCL" \
python3 "$D/orfs_baseline.py" \
  --package "$D/rtl-certified-handoff-0002" \
  --select "$D/verilog_only.txt" \
  --out "$D/gf180_full" --scratch "$D/gf180_full_scratch" \
  --platforms gf180 \
  --periods 'gf180=10' \
  --workers 12 --cores-per-design 8 \
  --timeout 10800 --pdn-core-floor-um 110 \
  >> "$D/gf180_full.driver.log" 2>&1
say "flow done"

# Verify the overrides reached the flow rather than assuming it: three runs
# earlier returned identical violation counts because invented variable names
# meant the config never changed.
n_pdn=$(grep -l "PDN_TCL" $D/gf180_full/runs/*/config.mk 2>/dev/null | wc -l)
n_tap=$(grep -l "TAPCELL_TCL" $D/gf180_full/runs/*/config.mk 2>/dev/null | wc -l)
n_run=$(ls -d $D/gf180_full/runs/*/ 2>/dev/null | wc -l)
say "覆盖自检: PDN $n_pdn/$n_run, tapcell $n_tap/$n_run"
[ "$n_pdn" -eq 0 ] && say "警告: PDN 覆盖未生效，DRC 结果会回到 824 条/设计的水平"

say "backfill"
python3 "$D/backfill.py" --runs "$D/gf180_full/runs" >> "$L" 2>&1 || say "backfill 跳过"

say "DRC signoff"
python3 "$D/signoff_pass.py" --runs "$D/gf180_full/runs" \
  --projroot "$D/gf180_signoff_proj" --out "$D/gf180_full/signoff.json" \
  --platform gf180 --workers 12 --timeout 7200 \
  >> "$D/gf180_signoff.log" 2>&1 || say "DRC 签核非零退出"
say "DRC signoff done"

say "LVS signoff"
python3 "$D/lvs130.py" --runs "$D/gf180_full/runs" \
  --projroot "$D/gf180_lvs_proj" --out "$D/gf180_full/netgen_lvs.json" \
  --platform gf180 --workers 12 --timeout 5400 \
  >> "$D/gf180_lvs.log" 2>&1 || say "LVS 签核非零退出"
say "LVS signoff done"

python3 "$D/funnel.py" "$D/gf180_full" >> "$D/GF180_STATUS.md" 2>&1 || true
say "chain180 all done"
