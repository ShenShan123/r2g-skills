#!/usr/bin/env bash
# Exp 4 (conversion) -- R2G arm shim: raw pilot500 design dir -> minimal def-graph project.
#
#   make_project.sh <design_dir> <platform_dir> <project_dir>
#
# The def-graph skill expects an ORFS project (constraints/config.mk + a backend run). The
# pilot500 directories are bare ORFS outputs, so this shim builds the smallest project the
# skill's documented reference-DEF path accepts (graph-dataset.md "R2G_DEF/R2G_ODB/R2G_SPEF ...
# enable a backend-less reference-DEF build"):
#
#   constraints/config.mk      DESIGN_NAME (from the run's own config.mk) + PLATFORM=nangate45
#   constraints/constraint.sdc the run's constraint.sdc, unchanged
#   inputs/6_final.def         byte copy of 5_route.def (the final routed + filled layout)
#   inputs/6_final.spef        byte copy of 6_final.spef
#   inputs/6_final.sdc         byte copy of constraint.sdc: run_labels.sh times against the SDC that
#                              sits beside the DEF/ODB (named 6_final.sdc); there is no ORFS
#                              6_final.sdc in these dirs, so the run's own constraint file is used.
#   inputs/6_final.odb         OpenROAD read_lef(tech, cells) + read_def(6_final.def) + write_db:
#                              the timing/IR stages read an ODB; this one is built from the SAME DEF
#                              the other stages read, so every label is keyed to one layout.
#
# Nothing else is supplied. In particular NO drc/lvs/route reports are written: the pilot500
# README says this re-run was not DRC/LVS checked, so the skill's signoff gate records its
# verdict (blockers) in reports/signoff_gate.json and the manifest's signoff_health, and -- per
# the skill's own documented rule that an explicit R2G_DEF override downgrades the gate to warn --
# the build proceeds. The gate is not switched off.
set -euo pipefail
D=$(cd "$1" && pwd); P=$(cd "$2" && pwd); J="$3"
OPENROAD=${OPENROAD_EXE:-openroad}
[[ -e "$J" ]] && { echo "project exists: $J" >&2; exit 2; }
mkdir -p "$J/constraints" "$J/inputs"
DN=$(sed -nE 's/^\s*export\s+DESIGN_NAME\s*=\s*(\S+).*/\1/p' "$D/config.mk" | head -1)
[[ -n "$DN" ]] || { echo "no DESIGN_NAME in $D/config.mk" >&2; exit 2; }
printf 'export DESIGN_NAME = %s\nexport PLATFORM = nangate45\n' "$DN" > "$J/constraints/config.mk"
cp "$D/constraint.sdc" "$J/constraints/constraint.sdc"
cp "$D/5_route.def" "$J/inputs/6_final.def"
cp "$D/6_final.spef" "$J/inputs/6_final.spef"
cp "$D/constraint.sdc" "$J/inputs/6_final.sdc"
cat > "$J/inputs/make_odb.tcl" <<EOF
read_lef $P/NangateOpenCellLibrary.tech.lef
read_lef $P/NangateOpenCellLibrary.macro.mod.lef
read_def $J/inputs/6_final.def
write_db $J/inputs/6_final.odb
EOF
"$OPENROAD" -no_splash -exit "$J/inputs/make_odb.tcl" > "$J/inputs/make_odb.log" 2>&1
[[ -s "$J/inputs/6_final.odb" ]] || { echo "ODB build failed (see $J/inputs/make_odb.log)" >&2; exit 3; }
echo "$J"
