#!/usr/bin/env bash
# Assemble the gf180 DRC signoff deck from the PDK rule tables.
#
# gf180 is the one supported platform whose foundry DRC is NOT an ORFS
# `.lydrc`. ORFS ships no deck for it (platforms/gf180 has no drc/ dir and its
# config.mk sets KLAYOUT_TECH_FILE/KLAYOUT_LEF_FILE but no KLAYOUT_DRC_FILE),
# so run_drc.sh's three-step deck resolution finds nothing and takes its honest
# "DRC not supported" exit. The foundry deck lives in the PDK as ~30 rule
# tables plus a preamble, and the official driver (run_drc.py) needs `docopt`,
# which is absent. This script does the assembly run_drc.py would do.
#
# WHAT IS EXCLUDED, AND WHY -- density.drc (8 rules).
# Those rules read, verbatim:
#     Rule M1.4: Metal1 coverage over the entire die shall be >30%
#     Rule PL.8: Poly2 coverage over the entire die shall be 14%.
# They are die-wide minimum-coverage rules. Measured on a 2,068-cell design at
# CORE_UTILIZATION=20 they produced 35,393 violations against 3 from the rest
# of the deck -- they measure the floorplan utilisation and the fact that
# ORFS's metal-fill step (`6_1_fill`, FILL_CONFIG=platforms/gf180/fill.json)
# did not run, not any property of the layout. Including them buries every real
# violation. The fix is to run the fill step, not to relax the rule; until then
# a gf180 DRC verdict from this deck is "clean except metal density", and that
# qualification belongs in any claim made from it.
#
# antenna.drc (33 rules) IS included: it cost +103s of the +108s that the two
# tables add together on the probe design, found 0 violations, and nangate45's
# bundled deck runs its antenna section too -- dropping it here would make the
# two platforms' DRC verdicts non-comparable.
set -euo pipefail

PDK_DRC="${1:-}"
OUT="${2:-}"
if [[ -z "$PDK_DRC" || -z "$OUT" ]]; then
  echo "usage: $0 <pdk>/libs.tech/klayout/drc <out-deck-dir>" >&2
  echo "  e.g. $0 \$CONDA/share/pdk/gf180mcuC/libs.tech/klayout/drc ~/gf180_deck_signoff" >&2
  exit 2
fi
RD="$PDK_DRC/rule_decks"
[[ -f "$RD/main.drc" ]] || { echo "ERROR: no $RD/main.drc" >&2; exit 1; }

rm -rf "$OUT"; mkdir -p "$OUT"
cp "$RD/layers_def.drc" "$OUT/"
# main.drc is only the preamble (mode selection + layer derivation) -- running
# it alone executes no rules at all, which is how three early measurements came
# back meaningless. tail.drc closes the deck and must come last.
TABLES=$(cd "$RD" && ls *.drc | grep -vE 'density|^main\.drc$|layers_def|tail')
{ cat "$RD/main.drc"
  for f in $TABLES; do cat "$RD/$f"; done
  cat "$RD/tail.drc"
} > "$OUT/main.drc"

n=$(grep -c 'Executing rule' "$OUT/main.drc" || true)
echo "assembled $OUT/main.drc: $n rule statements, $(wc -c < "$OUT/main.drc") bytes"
# 691 with the PDK this was built against: 658 base + 33 antenna. Fewer execute
# at run time (482 under metal_level=5LM/metal_top=9K/mim_option=B) because the
# option switches gate the variant rules -- that is expected, not a loss.
[[ "$n" -ge 600 ]] || { echo "ERROR: only $n rules -- assembly is wrong" >&2; exit 1; }
