#!/usr/bin/env bash
# Does splitting the FreePDK45 deck recover the 67 lost nangate45 verdicts?
#
# All 67 stall inside the ANTENNA block, the deck's LAST section, so roughly 370
# other rules already ran and their result was discarded when the run was killed
# -- klayout writes its report only at the end, and only drc_signoff.log
# survives. The deck already exposes four independent switches (FEOL / BEOL /
# OFFGRID / ANTENNA), so running it once per group gives each its own budget and
# its own verdict: the same shape as gf180's per-table execution.
#
# Tested on the SMALLEST stalled design (39 782 cells). If that one cannot be
# recovered, none of the larger 66 can.
#
# A recovered design is NOT clean. It is clean_except_antenna -- the antenna
# result is unknown, and unknown is not a pass.
#
# Selection is a symlink farm rather than a new flag: signoff45.py takes a runs
# DIRECTORY, and another session is editing that file.
set -uo pipefail
D=/proj/workarea/yangao
DECK=/tmp/openyield-nangate45-gds-reference/drc/FreePDK45.lydrc
RUN=${1:-C00358__audio_top__sonic-speed-parallelism__nangate45__p2}
OUT=$D/_n45split
rm -rf "$OUT"; mkdir -p "$OUT/runs"
ln -s "$D/n45_full/runs/$RUN" "$OUT/runs/$RUN" || exit 1

[ -f "$DECK" ] || { echo "找不到 deck $DECK"; exit 1; }
for grp in NOANT ANTONLY; do
  case $grp in
    NOANT)   sed 's/^ANTENNA = true/ANTENNA = false/' "$DECK" > "$OUT/deck_$grp.lydrc";;
    ANTONLY) sed -e 's/^FEOL    = true/FEOL    = false/' \
                 -e 's/^BEOL    = true/BEOL    = false/' \
                 -e 's/^OFFGRID = true/OFFGRID = false/' "$DECK" > "$OUT/deck_$grp.lydrc";;
  esac
  # prove the switch landed: a sed that matched nothing would run the stock
  # deck twice and "prove" the split works
  echo "--- $grp 开关:  $(grep -hE '^(FEOL|BEOL|OFFGRID|ANTENNA)' "$OUT/deck_$grp.lydrc" | tr -s ' ' | tr '\n' ' ')"
done

for grp in NOANT ANTONLY; do
  echo "=== $grp 跑 ${RUN:0:46}"
  t0=$(date +%s)
  R2G_DRC_DECK_OVERRIDE=$OUT/deck_$grp.lydrc \
  R2G_DRC_TIMEOUT=3600 \
  timeout 4200 python3 -I "$D/signoff45.py" \
      --runs "$OUT/runs" --out "$OUT/$grp.json" \
      --workers 1 --timeout 4000 --force \
      > "$OUT/$grp.log" 2>&1
  echo "    rc=$?  用时 $(( $(date +%s)-t0 ))s"
  if [ -f "$OUT/$grp.json" ]; then
    python3 -I -c "
import json
for r in json.load(open('$OUT/$grp.json')):
    print('      判决:', r.get('drc_status'), ' 违例:', r.get('drc_violations'),
          ' 用时:', r.get('seconds'))
"
  else
    echo "      没有产出 json"; tail -4 "$OUT/$grp.log" | sed 's/^/      /'
  fi
done
