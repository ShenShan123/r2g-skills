#!/bin/bash
# Stage the SKY130/GF180 proof designs (sample_v2.json meta.<plat>.checker_proof) from memlab216
# (read-only) to memlab203:/data/yangao/exp4_validation_20261107/exp4v2/designs/<run>/, via ssh
# pipes through 208 (no scp), with sha256 verified on both sides. Also writes 3_place.def from
# 3_place.odb on 203 (OpenROAD read_db + write_def) for the image truth.
set -euo pipefail
H=$(cd "$(dirname "$0")/.." && pwd); W=/data/yangao/exp4_validation_20261107/exp4v2
FILES="6_final.def 6_final.spef 6_final.v 6_final.sdc 3_place.odb 5_route.odb config.mk constraint.sdc result.json"
python3 - "$H/sample_v2.json" > /tmp/exp4v2_stage.lst <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
for p in ("sky130hd", "gf180"):
    pool = {d["design"]: d for d in s["platforms"][p]}
    for r in s["meta"][p]["checker_proof"]:
        print(p, pool[r]["source_dir"].split(":", 1)[1])
PY
while read -r plat src; do
  run=$(basename "$src"); par=$(dirname "$src")
  ssh -n memlab216 "cd $par && tar cf - $(for f in $FILES; do printf '%s/%s ' "$run" "$f"; done)" | ssh memlab203 "mkdir -p $W/designs && tar -C $W/designs -xf -"
  a=$(ssh -n memlab216 "cd $src && sha256sum $FILES" | sha256sum | cut -c1-16)
  b=$(ssh -n memlab203 "cd $W/designs/$run && sha256sum $FILES" | sha256sum | cut -c1-16)
  [[ "$a" == "$b" ]] && echo "STAGED $plat $run $a" || { echo "SHA MISMATCH $run"; exit 1; }
done < /tmp/exp4v2_stage.lst
