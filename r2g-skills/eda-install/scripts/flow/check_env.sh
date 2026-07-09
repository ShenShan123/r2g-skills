#!/usr/bin/env bash
set -uo pipefail

# Report the tool environment the skill has discovered. Exits 0 if all required
# tools + ORFS are available, 1 otherwise. Honors user overrides via:
#   - Explicit env vars: ORFS_ROOT, PDK_ROOT, OPENROAD_EXE, YOSYS_EXE,
#     KLAYOUT_CMD, MAGIC_EXE, NETGEN_EXE, STA_EXE, IVERILOG_EXE, VVP_EXE,
#     VERILATOR_EXE
#   - A user env file pointed to by $R2G_ENV_FILE
#   - references/env.local.sh inside the skill

# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_env.sh"

STATUS=0

print_row() {
  # print_row <label> <value-or-empty> <required?>
  local label="$1" value="$2" required="$3"
  if [[ -n "$value" ]]; then
    printf 'ok   %-14s %s\n' "$label" "$value"
  elif [[ "$required" == "required" ]]; then
    printf 'MISS %-14s (required)\n' "$label"
    STATUS=1
  else
    printf 'skip %-14s (optional, not found)\n' "$label"
  fi
}

echo "[ORFS]"
print_row ORFS_ROOT "${ORFS_ROOT:-}" required
print_row FLOW_DIR  "${FLOW_DIR:-}" required
print_row PDK_ROOT  "${PDK_ROOT:-}" optional
print_row SKY130A_DIR "${SKY130A_DIR:-}" optional

echo
echo "[required tools]"
print_row OPENROAD_EXE "${OPENROAD_EXE:-}" required
print_row YOSYS_EXE    "${YOSYS_EXE:-}"    required
print_row IVERILOG_EXE "${IVERILOG_EXE:-}" required
print_row VVP_EXE      "${VVP_EXE:-}"      required
print_row python3      "$(command -v python3 || true)" required

echo
echo "[optional tools]"
print_row VERILATOR_EXE "${VERILATOR_EXE:-}" optional
print_row KLAYOUT_CMD   "${KLAYOUT_CMD:-}"   optional
print_row MAGIC_EXE     "${MAGIC_EXE:-}"     optional
print_row NETGEN_EXE    "${NETGEN_EXE:-}"    optional
print_row STA_EXE       "${STA_EXE:-}"       optional
print_row gtkwave       "$(command -v gtkwave || true)" optional

echo
echo "[graph dataset stage (def-graph)]"
# The PyG graph-assembly stage (run_graphs.sh) needs a python with
# torch+torch_geometric+pandas, pinned via R2G_GRAPH_PYTHON. Optional: absence
# only SKIPs the graph stage, so it never fails the required-tools gate.
_gp_found=""
for _c in "${R2G_GRAPH_PYTHON:-}" python3; do
  [[ -z "$_c" ]] && continue
  if "$_c" -c "import torch, torch_geometric, pandas" >/dev/null 2>&1; then
    _gp_found="$(command -v "$_c" 2>/dev/null || echo "$_c")"; break
  fi
done
print_row R2G_GRAPH_PYTHON "$_gp_found" optional
if [[ -n "$_gp_found" ]]; then
  _tv="$("$_gp_found" -c 'import torch, torch_geometric as g; print("torch", torch.__version__, "· pyg", g.__version__)' 2>/dev/null || true)"
  [[ -n "$_tv" ]] && printf '     %s\n' "$_tv"
elif [[ -n "${R2G_GRAPH_PYTHON:-}" ]]; then
  printf '     (R2G_GRAPH_PYTHON=%s set but torch/torch_geometric/pandas not importable)\n' "${R2G_GRAPH_PYTHON}"
fi

echo
echo "[corpus expansion (rtl-acquire)]"
# rtl-acquire borrows the sibling sub-skills (scoped-reuse contract): synth via
# signoff-loop run_orfs.sh, graphs via def-graph netlist_graph.py, learning via
# the knowledge DB. Optional: absence only disables the rtl-acquire skill.
_skills_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
print_row "run_orfs.sh (signoff-loop)" \
  "$([[ -f "$_skills_root/signoff-loop/scripts/flow/run_orfs.sh" ]] && echo "$_skills_root/signoff-loop/scripts/flow/run_orfs.sh")" optional
print_row "netlist_graph.py (def-graph)" \
  "$([[ -f "$_skills_root/def-graph/scripts/extract/graph/netlist_graph.py" ]] && echo "$_skills_root/def-graph/scripts/extract/graph/netlist_graph.py")" optional
print_row "ingest_run.py (knowledge)" \
  "$([[ -f "$_skills_root/signoff-loop/knowledge/ingest_run.py" ]] && echo "$_skills_root/signoff-loop/knowledge/ingest_run.py")" optional
if [[ -z "$_gp_found" ]]; then
  echo "     (graph conversion will SKIP: designs record graph_skipped until R2G_GRAPH_PYTHON is provisioned)"
fi

echo
echo "[platforms]"
if [[ -n "${FLOW_DIR:-}" && -d "$FLOW_DIR/platforms" ]]; then
  for p in "$FLOW_DIR"/platforms/*/; do
    printf 'ok    %s\n' "$(basename "$p")"
  done
else
  echo "--    platforms directory not found"
fi

echo
echo "[how to override]"
echo "  bash ../../bootstrap.sh --dry-run   # auto-detect + plan the toolchain (then drop --dry-run)"
echo "  ORFS_ROOT=/your/path OPENROAD_EXE=/your/openroad bash check_env.sh"
echo "  export R2G_ENV_FILE=~/my-r2g-env.sh   # shell snippet with exports"
echo "  or write to  $(dirname "${BASH_SOURCE[0]}")/../../references/env.local.sh"

exit "$STATUS"
