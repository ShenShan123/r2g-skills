#!/bin/bash
# Experiment 1 checklist verifier: runs INSIDE the container as tester after the agent finished. It decides each
# item on its own; the agent's report counts for nothing. Prints one JSON object.
cd ~/repo 2>/dev/null || { echo '{"error":"no repo"}'; exit 1; }
source r2g-skills/signoff-loop/scripts/flow/_env.sh >/dev/null 2>&1
ok(){ "$@" >/dev/null 2>&1 && echo true || echo false; }
item(){ printf '"%s": {"ok": %s, "evidence": %s},' "$1" "$2" "$(printf '%s' "$3" | head -c 160 | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')"; }
printf '{'
v=$("$OPENROAD_EXE" -version 2>&1 | head -1); item openroad "$( [ -n "$OPENROAD_EXE" ] && ok "$OPENROAD_EXE" -version )" "$v"
v=$("$YOSYS_EXE" -V 2>&1 | head -1); item yosys "$( [ -n "$YOSYS_EXE" ] && ok "$YOSYS_EXE" -V )" "$v"
v=$("${KLAYOUT_CMD:-klayout}" -v 2>&1 | head -1); item klayout "$(ok "${KLAYOUT_CMD:-klayout}" -v)" "$v"
v=$("${MAGIC_EXE:-magic}" --version 2>&1 | head -1); item magic "$(ok "${MAGIC_EXE:-magic}" --version)" "$v"
v=$(echo quit | "${NETGEN_EXE:-netgen}" -batch 2>&1 | head -2 | tr '\n' ' '); item netgen "$( [ -x "$(command -v "${NETGEN_EXE:-netgen}")" ] && echo true || echo false )" "$v"
v=$("${IVERILOG_EXE:-iverilog}" -V 2>&1 | head -1); item iverilog "$(ok "${IVERILOG_EXE:-iverilog}" -V)" "$v"
v=$("${VERILATOR_EXE:-verilator}" --version 2>&1 | head -1); item verilator "$(ok "${VERILATOR_EXE:-verilator}" --version)" "$v"
item orfs "$( [ -f "${ORFS_ROOT:-/nonexistent}/flow/Makefile" ] && [ -d "$ORFS_ROOT/flow/platforms/nangate45" ] && [ -d "$ORFS_ROOT/flow/platforms/sky130hd" ] && [ -d "$ORFS_ROOT/flow/platforms/gf180" ] && echo true || echo false )" "${ORFS_ROOT:-unset}"
item sky130A_pdk "$( [ -d "${PDK_ROOT:-/x}/sky130A/libs.tech" ] && [ -d "$PDK_ROOT/sky130A/libs.ref" ] && echo true || echo false )" "${PDK_ROOT:-unset}"
v=$("${R2G_GRAPH_PYTHON:-python3}" -c 'import torch,torch_geometric,pandas;print(torch.__version__,torch_geometric.__version__,pandas.__version__)' 2>&1 | tail -1); item graph_env "$(ok "${R2G_GRAPH_PYTHON:-/nonexistent}" -c 'import torch,torch_geometric,pandas')" "$v"
for p in nangate45 sky130hd gf180; do
  out=$(R2G_STRICT_PLATFORMS=$p bash r2g-skills/signoff-loop/scripts/flow/check_env.sh 2>&1); rc=$?
  item "strict_ready_$p" "$( [ $rc = 0 ] && echo true || echo false )" "$(echo "$out" | grep -E 'MISS|STRICT|READY' | head -3 | tr '\n' ' ')"
done
printf '"_installed_kib": %s, "_home_kib": %s}\n' "$(du -sk ~/toolchain 2>/dev/null | cut -f1 || echo 0)" "$(du -sk ~ 2>/dev/null | cut -f1)"
