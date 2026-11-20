#!/usr/bin/env bash
# Raw pilot500 design dir -> def-graph project with ONE backend run (ORFS layout), read-only on source.
# Ported from experiments/exp4_conversion/r2g_arm/make_project.sh (another agent's; not modified):
# that shim pins one DEF via R2G_DEF; the four-stage and image products also need every stage ODB,
# so this one writes a real backend/<run>/results/ the skill's run selector finds by itself.
#   make_defgraph_project.sh <design_dir> <platform_lef_dir> <project_dir>
#   results/1_2_yosys.v                      copy (canonical topology of the four-stage product)
#   results/{2_floorplan,3_place,4_cts,5_route}.odb   read_lef + read_def(<stage>.def) + write_db
#   results/6_final.def = 5_route.def, 6_final.odb (same), 6_final.spef, 6_final.sdc = constraint.sdc
# No drc/lvs/route reports exist (pilot500 re-run was not DRC/LVS checked), so callers run with
# R2G_SIGNOFF_GATE=warn: the gate still records its verdict, it is not switched off.
set -euo pipefail
D=$(cd "$1" && pwd); P=$(cd "$2" && pwd); J="$3"; OR=${OPENROAD_EXE:-openroad}
[[ -e "$J" ]] && { echo "project exists: $J" >&2; exit 2; }
RUNDIR="$J/backend/RUN_pilot500"; RUN="$RUNDIR/results"; mkdir -p "$J/constraints" "$RUN"
DN=$(sed -nE 's/^\s*export\s+DESIGN_NAME\s*=\s*(\S+).*/\1/p' "$D/config.mk" | head -1)
printf 'export DESIGN_NAME = %s\nexport PLATFORM = nangate45\n' "$DN" > "$J/constraints/config.mk"
cp "$D/constraint.sdc" "$J/constraints/constraint.sdc"
cp "$D/1_2_yosys.v" "$RUN/1_2_yosys.v"; cp "$D/6_final.spef" "$RUN/6_final.spef"
cp "$D/constraint.sdc" "$RUN/6_final.sdc"; cp "$D/5_route.def" "$RUN/6_final.def"
for s in 2_floorplan 3_place 4_cts 5_route; do
  printf 'read_lef %s/NangateOpenCellLibrary.tech.lef\nread_lef %s/NangateOpenCellLibrary.macro.mod.lef\nread_def %s\nwrite_db %s\n' \
    "$P" "$P" "$D/$s.def" "$RUN/$s.odb" > "$RUNDIR/make_$s.tcl"
  "$OR" -no_splash -no_init -exit -threads 1 "$RUNDIR/make_$s.tcl" > "$RUNDIR/make_$s.log" 2>&1
  [[ -s "$RUN/$s.odb" ]] || { echo "ODB build failed: $s" >&2; exit 3; }
done
cp "$RUN/5_route.odb" "$RUN/6_final.odb"
# Provenance the skill binds stage inputs to (stage contract v2): the artifacts THIS shim produced,
# hashed when produced (the honest "flow" here is DEF -> ODB conversion of the pilot500 run).
python3 - "$RUNDIR" "$DN" <<'PY'
import hashlib, json, os, sys
run, dn = sys.argv[1], sys.argv[2]
art = {"floorplan": "2_floorplan.odb", "place": "3_place.odb", "cts": "4_cts.odb", "route": "5_route.odb", "finish": "6_final.odb"}
with open(os.path.join(run, "stage_artifact_manifest.jsonl"), "w") as f:
    for st, a in art.items():
        h = hashlib.sha256(open(os.path.join(run, "results", a), "rb").read()).hexdigest()
        f.write(json.dumps(dict(stage=st, artifact=a, sha256=h, contract_version=2, recorder="exp4 pilot500 shim (read_def+write_db)")) + "\n")
json.dump(dict(design_name=dn, platform="nangate45", flow_variant="base", source="pilot500 shim"), open(os.path.join(run, "run-meta.json"), "w"), indent=1)
PY
echo "$J"
