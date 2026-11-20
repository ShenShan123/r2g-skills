#!/usr/bin/env bash
# Exp 4 v2 -- R2G arm, one design, full product: shim + def-graph's own stages. 0 model calls.
#
#   run_r2g_v2.sh <skill_root> <design_dir> <platform_dir> <project_dir> <out_dir>
#
# design_dir = the v2 layout every arm gets (PROTOCOL_v2.md section 4). The shim writes ONE ORFS
# backend run the skill's run selector finds by itself (layout of the validation study's
# make_defgraph_project.sh): backend/RUN_v2/results/{6_final.def,6_final.odb,6_final.spef,6_final.sdc,
# 3_place.odb} and, when the four-stage inputs are present (Nangate45), 1_2_yosys.v +
# {2_floorplan,4_cts,5_route}.odb, plus run-meta.json and stage_artifact_manifest.jsonl. Every ODB is
# OpenROAD read_lef(TECH_LEF, SC_LEF, ADDITIONAL_LEFS resolved by the skill's own resolver for the
# design's config.mk) + read_def + write_db of the given DEF. No DRC/LVS reports exist for these
# re-run layouts, so R2G_SIGNOFF_GATE=warn: the gate runs and records its verdict.
# Products: run_graphs.sh (R2G_GRAPH_KIND=both, all views b-f), run_images.sh, and on Nangate45
# run_stage_dataset.sh. Output layout = PROTOCOL_v2.md section 4 (images: ir_drop dropped).
set -uo pipefail
S=$(cd "$1" && pwd); D=$(cd "$2" && pwd); P=$(cd "$3" && pwd); J="$4"; O="$5"
OR=${OPENROAD_EXE:-openroad}
mkdir -p "$O"; [[ -e "$J" ]] && { echo "project exists: $J" >&2; exit 2; }
T=$O/r2g_timing.tsv; : > "$T"
step() { local n=$1; shift; local t0=$(date +%s.%N); "$@" > "$J.$n.log" 2>&1; local rc=$?;
         echo -e "$n\t$rc\t$(python3 -c "import time;print(round(time.time()-$t0,2))")" >> "$T"; return $rc; }
shim() {
  set -e
  RUNDIR="$J/backend/RUN_v2"; RUN="$RUNDIR/results"; mkdir -p "$J/constraints" "$RUN"
  DN=$(sed -nE 's/^\s*export\s+DESIGN_NAME\s*=\s*(\S+).*/\1/p' "$D/config.mk" | head -1)
  PL=$(sed -nE 's/^\s*export\s+PLATFORM\s*=\s*(\S+).*/\1/p' "$D/config.mk" | head -1)
  printf 'export DESIGN_NAME = %s\nexport PLATFORM = %s\n' "$DN" "$PL" > "$J/constraints/config.mk"
  cp "$D/constraint.sdc" "$J/constraints/constraint.sdc"
  RES=$(bash "$S/scripts/flow/resolve_platform_paths.sh" "$J/constraints/config.mk" "$PL" 2>/dev/null || true)
  LEFS="$(echo "$RES" | sed -n 's/^TECH_LEF=//p') $(echo "$RES" | sed -n 's/^SC_LEF=//p') $(echo "$RES" | sed -n 's/^ADDITIONAL_LEFS=//p')"
  cp "$D/6_final.def" "$D/6_final.spef" "$D/6_final.sdc" "$RUN/"
  STAGES="6_final:6_final 3_place:3_place"
  if [[ -f "$D/1_2_yosys.v" ]]; then
    cp "$D/1_2_yosys.v" "$RUN/"
    STAGES="$STAGES 2_floorplan:2_floorplan 4_cts:4_cts 5_route:5_route"
  fi
  for st in $STAGES; do
    src=${st%%:*}; dst=${st##*:}
    { for l in $LEFS; do echo "read_lef $l"; done; echo "read_def $D/$src.def"; echo "write_db $RUN/$dst.odb"; } > "$RUNDIR/make_$dst.tcl"
    "$OR" -no_splash -exit "$RUNDIR/make_$dst.tcl" > "$RUNDIR/make_$dst.log" 2>&1
    [[ -s "$RUN/$dst.odb" ]] || { echo "ODB build failed: $dst" >&2; return 3; }
  done
  python3 - "$RUNDIR" "$DN" "$PL" <<'PY'
import hashlib, json, os, sys
run, dn, pl = sys.argv[1:4]
art = {"floorplan": "2_floorplan.odb", "place": "3_place.odb", "cts": "4_cts.odb", "route": "5_route.odb", "finish": "6_final.odb"}
with open(os.path.join(run, "stage_artifact_manifest.jsonl"), "w") as f:
    for st, a in art.items():
        p = os.path.join(run, "results", a)
        if os.path.isfile(p):
            f.write(json.dumps(dict(stage=st, artifact=a, sha256=hashlib.sha256(open(p, "rb").read()).hexdigest(),
                                    contract_version=2, recorder="exp4 v2 shim (read_def+write_db)")) + "\n")
json.dump(dict(design_name=dn, platform=pl, flow_variant="base", source="exp4 v2 shim"), open(os.path.join(run, "run-meta.json"), "w"), indent=1)
PY
  echo "$PL" > "$J/.platform"
}
step shim shim || { echo "shim failed" >&2; exit 3; }
PL=$(cat "$J/.platform")
export R2G_SIGNOFF_GATE=warn R2G_GRAPH_KIND=both R2G_GRAPH_VARIANTS=bcdef
step graphs bash "$S/scripts/flow/run_graphs.sh" "$J" "$PL"; rg=$?
step images bash "$S/scripts/flow/run_images.sh" "$J" "$PL"; ri=$?
r4=skip
if [[ -f "$D/1_2_yosys.v" ]]; then step four_stage bash "$S/scripts/flow/run_stage_dataset.sh" "$J" --platform "$PL"; r4=$?; fi
mkdir -p "$O/dataset" "$O/images"
for v in b c d e f; do
  for f in ${v}_graph.pt ${v}_graph_homo.pt; do [[ -f "$J/dataset/$f" ]] && cp "$J/dataset/$f" "$O/dataset/"; done
done
[[ -f "$J/dataset/graph_manifest.json" ]] && cp "$J/dataset/graph_manifest.json" "$O/dataset/"
if [[ -f "$J/images/labels.npz" ]]; then
  cp "$J/images/features.npz" "$O/images/"
  python3 - "$J/images/labels.npz" "$O/images/labels.npz" <<'EOF'
import sys, numpy as np
z = np.load(sys.argv[1], allow_pickle=False)
np.savez_compressed(sys.argv[2], **{k: z[k] for k in z.files if k != "ir_drop"})
EOF
  cp "$J/images/verify.json" "$O/images/r2g_verify_images.json" 2>/dev/null || true
fi
G=$(find "$J" -path '*generated/stages' -type d 2>/dev/null | head -1)
if [[ -n "$G" ]]; then
  for st in floorplan placement cts route; do
    [[ -f "$G/$st/heterograph.pt" ]] && { mkdir -p "$O/four_stage/generated/stages/$st"; cp "$G/$st/heterograph.pt" "$O/four_stage/generated/stages/$st/"; }
  done
  V=$(dirname "$G")/four_stage.validation.json; [[ -f "$V" ]] && cp "$V" "$O/four_stage/generated/"
fi
echo -e "rc\tgraphs=$rg images=$ri four_stage=$r4" >> "$T"
[[ $rg -eq 0 && $ri -eq 0 && ( $r4 == skip || $r4 -eq 0 ) ]]
