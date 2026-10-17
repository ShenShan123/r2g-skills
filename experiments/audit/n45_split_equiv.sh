#!/usr/bin/env bash
# Is the deck split a CLEAN decomposition, or does disabling one group change
# another group's result?
#
# The split's whole premise is that running FEOL/BEOL/OFFGRID/ANTENNA separately
# gives the same verdicts as the monolithic deck. A doubt appeared: with
# ANTENNA=false the log attributes FEOL's first operation to FreePDK45.lydrc:91
# (inside the classify_by_width helper) where the full-deck run attributes it to
# line 114, and the full-deck run never reports line 91 at all. The decks are
# byte-identical but for that one switch, so the execution path really does
# differ -- klayout's deep mode is lazy and the antenna block's connect()
# statements materialize layers that the rest of the deck then reuses.
#
# Whether that changes any VERDICT is the only question that matters, and it is
# answered by comparing violations on a design where BOTH runs complete -- so a
# small one, picked from the designs that already have a DRC verdict.
#
# If the non-antenna violations differ, the split is not a decomposition and the
# 67 designs cannot be recovered this way.
set -uo pipefail
D=/proj/workarea/yangao
DECK=/tmp/openyield-nangate45-gds-reference/drc/FreePDK45.lydrc
OUT=$D/_n45equiv
RUN=${1:-}
if [ -z "$RUN" ]; then
  # smallest design WITH a DRC verdict and at least one violation, so the
  # comparison has something to compare; fall back to any clean one
  RUN=$(python3 -I - <<'PY'
import glob, json
from pathlib import Path
D = Path("/proj/workarea/yangao")
best = None
for f in glob.glob(str(D / "n45_full/runs/*/result.json")):
    try: d = json.load(open(f))
    except Exception: continue
    if d.get("drc_status") != "violations": continue
    c = d.get("cells") or 0
    if c and (best is None or c < best[0]):
        best = (c, Path(f).parent.name)
print(best[1] if best else "")
PY
)
fi
[ -n "$RUN" ] || { echo "挑不到设计"; exit 1; }
echo "受试: $RUN"

rm -rf "$OUT"; mkdir -p "$OUT/runs"
ln -s "$D/n45_full/runs/$RUN" "$OUT/runs/$RUN"
sed 's/^ANTENNA = true/ANTENNA = false/' "$DECK" > "$OUT/deck_NOANT.lydrc"
echo "NOANT 开关: $(grep -h '^ANTENNA' "$OUT/deck_NOANT.lydrc")"

for grp in FULL NOANT; do
  echo "=== $grp"
  t0=$(date +%s)
  if [ "$grp" = FULL ]; then unset R2G_DRC_DECK_OVERRIDE || true
  else export R2G_DRC_DECK_OVERRIDE=$OUT/deck_NOANT.lydrc; fi
  R2G_DRC_TIMEOUT=1800 timeout 2100 python3 -I "$D/signoff45.py" \
      --runs "$OUT/runs" --out "$OUT/$grp.json" --workers 1 \
      --timeout 2000 --force > "$OUT/$grp.log" 2>&1
  echo "    rc=$? 用时 $(( $(date +%s)-t0 ))s"
  [ -f "$OUT/$grp.json" ] && python3 -I -c "
import json
for r in json.load(open('$OUT/$grp.json')):
    print('    判决 %s  违例 %s' % (r.get('drc_status'), r.get('drc_violations')))
"
  # keep the per-run report so the violation TYPES can be compared, not just counts
  for f in "$D"/n45_signoff_proj/"$RUN"/backend/RUN_1/drc/*; do
    [ -f "$f" ] && cp "$f" "$OUT/${grp}_$(basename $f)" 2>/dev/null
  done
done
echo "=== 违例种类对比（非天线部分必须完全一致）"
for g in FULL NOANT; do
  r=$(ls "$OUT/${g}"_*.lyrdb "$OUT/${g}"_*.xml 2>/dev/null | head -1)
  if [ -n "$r" ]; then
    echo "  $g: $(grep -oE '<category>[^<]+' "$r" 2>/dev/null | sed 's/<category>//' | sort | uniq -c | tr '\n' ' ')"
  else
    echo "  $g: 没有报告文件"
  fi
done
