#!/usr/bin/env bash
# smoke_signoff.sh: sign off every finished smoke-pilot design on 216 (keep-going, 30 min DRC budget) and print a table.
module load calibre/2025.1_16.10 >/dev/null 2>&1
B=/proj/workarea/yangao/asap7_baseline
cd $B
for d in smoke6/runs/*; do
  n=$(basename "$d" | cut -d_ -f1)
  [ -f "$d/6_final.gds" ] || continue
  [ -f "signoff_smoke/$n/signoff.json" ] && continue
  python3 asap7_signoff.py "$d" "signoff_smoke/$n" --keep-going --drc-budget 1800 > /dev/null 2>&1
done
python3 - <<'PY'
import json, glob
for f in sorted(glob.glob("/proj/workarea/yangao/asap7_baseline/signoff_smoke/*/signoff.json")):
    r = json.load(open(f))
    print("%-8s %-22s drc=%-10s results=%-4s lvs=%-9s A=%ss B=%ss total=%ss %s" % (
        f.split("/")[-2], r["top"][:22], r["drc"], r.get("drc_results"), r["lvs"],
        (r.get("drc_group_a") or {}).get("wall_s"), (r.get("drc_group_b") or {}).get("wall_s"), r.get("wall_s"),
        r.get("drc_violations") or r.get("error", "")))
PY
