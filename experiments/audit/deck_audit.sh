#!/usr/bin/env bash
# What does each platform's DRC deck ACTUALLY check, and what does it disclaim?
#
# nangate45's official FreePDK45.lydrc turned out to carry three self-
# disclaimers: POLY.2 and POLY.3 are commented out ("This section errors out for
# some reason") and the via-enclosure families METALx.3/x.4 are annotated "do
# not appear to correctly identify violations". A deck's rule COUNT is therefore
# not its coverage, and the same question has to be asked of the other two.
set -uo pipefail
for p in nangate45 sky130hd gf180; do
  echo "===== $p"
  for f in $(ls "$ORFS_ROOT/flow/platforms/$p/drc/"* 2>/dev/null | head -3); do
    [ -f "$f" ] || continue
    n=$(grep -c 'output(' "$f" 2>/dev/null || echo 0)
    echo "  $(basename "$f")  $(wc -l < "$f") lines, $n rule outputs"
    dis=$(grep -nE 'TODO|FIXME|not .*correctly|does not appear' "$f" 2>/dev/null | head -4)
    [ -n "$dis" ] && echo "$dis" | sed 's/^/     disclaim: /'
    off=$(grep -cE '^[[:space:]]*#+[^#]*output\(' "$f" 2>/dev/null || echo 0)
    echo "     commented-out rule outputs: $off"
  done
done
