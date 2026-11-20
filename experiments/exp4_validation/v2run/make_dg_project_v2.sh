#!/usr/bin/env bash
# v2 shim: raw design dir -> def-graph project with ONE backend run, read-only on the source.
#   make_dg_project_v2.sh <design_dir> <platform> <platform_dir> <project_dir>
# nangate45 (pilot500): exactly defgraph/make_defgraph_project.sh (all stage ODBs from the stage
#   DEFs, 6_final.def = 5_route.def, 6_final.sdc = constraint.sdc, 1_2_yosys.v).
# sky130hd / gf180 (baseline re-runs): results/6_final.{def,spef,sdc} are byte copies of the run's;
#   6_final.odb = read_lef(tech, sc) + read_def(6_final.def) + write_db, so every label is keyed to
#   the DEF the features read; 3_place.odb / 5_route.odb are the run's own. The runs keep no
#   1_2_yosys.v or floorplan/CTS stages, so the four-stage product is not built there.
# No drc/lvs reports are given: callers run with R2G_SIGNOFF_GATE=warn (the gate records its verdict).
set -euo pipefail
D=$(cd "$1" && pwd); PL=$2; P=$(cd "$3" && pwd); J="$4"; OR=${OPENROAD_EXE:-openroad}
HERE=$(cd "$(dirname "$0")" && pwd)
if [[ $PL == nangate45 ]]; then exec bash "$HERE/../defgraph/make_defgraph_project.sh" "$D" "$P" "$J"; fi
[[ -e "$J" ]] && { echo "project exists: $J" >&2; exit 2; }
read -r TL SL <<<"$(python3 -c "import sys; sys.path.insert(0,'$HERE/..'); from platforms_v2 import PLATFORMS as P; c=P['$PL']; print(c['tech_lef'], ' '.join(c['sc_lef']))")"
RUNDIR="$J/backend/RUN_baseline"; RUN="$RUNDIR/results"; mkdir -p "$J/constraints" "$RUN"
DN=$(sed -nE 's/^\s*export\s+DESIGN_NAME\s*=\s*(\S+).*/\1/p' "$D/config.mk" | head -1)
printf 'export DESIGN_NAME = %s\nexport PLATFORM = %s\n' "$DN" "$PL" > "$J/constraints/config.mk"
cp "$D/constraint.sdc" "$J/constraints/constraint.sdc"
for f in 6_final.def 6_final.spef 6_final.sdc 3_place.odb 5_route.odb; do cp "$D/$f" "$RUN/$f"; done
{ printf 'read_lef %s/%s\n' "$P" "$TL"; for s in $SL; do printf 'read_lef %s/%s\n' "$P" "$s"; done
  printf 'read_def %s\nwrite_db %s\n' "$RUN/6_final.def" "$RUN/6_final.odb"; } > "$RUNDIR/make_6_final.tcl"
"$OR" -no_splash -no_init -exit -threads 1 "$RUNDIR/make_6_final.tcl" > "$RUNDIR/make_6_final.log" 2>&1
[[ -s "$RUN/6_final.odb" ]] || { echo "ODB build failed" >&2; exit 3; }
python3 - "$RUNDIR" "$DN" "$PL" <<'PY'
import hashlib, json, os, sys
run, dn, pl = sys.argv[1:4]
art = {"place": "3_place.odb", "route": "5_route.odb", "finish": "6_final.odb"}
with open(os.path.join(run, "stage_artifact_manifest.jsonl"), "w") as f:
    for st, a in art.items():
        h = hashlib.sha256(open(os.path.join(run, "results", a), "rb").read()).hexdigest()
        f.write(json.dumps(dict(stage=st, artifact=a, sha256=h, contract_version=2, recorder="exp4 v2 baseline shim")) + "\n")
json.dump(dict(design_name=dn, platform=pl, flow_variant="base", source="exp4 v2 baseline shim"), open(os.path.join(run, "run-meta.json"), "w"), indent=1)
PY
echo "$J"
