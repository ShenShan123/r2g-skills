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
  local label="$1" value="$2" required="$3" kind="${4:-}" why=""
  if [[ -n "$value" ]]; then
    # A SET value is verified, not just echoed: E6 got "ok" for an unreadable
    # PDK_ROOT and a python3 that cannot run ORFS (CORRECTIONS #6). Set but
    # unusable is an error even on an optional row.
    case "$kind" in
      dir) [[ -d "$value" && -r "$value" && -x "$value" ]] || why="not a readable directory" ;;
      exe) [[ -f "$value" && -x "$value" ]] || why="not an executable file" ;;
      orfs_python) "$value" -c "import yaml" >/dev/null 2>&1 \
             || why="cannot import yaml, which ORFS flow/scripts/defaults.py needs" ;;
    esac
    if [[ -n "$why" ]]; then
      printf 'BAD  %-14s %s (%s)\n' "$label" "$value" "$why"
      STATUS=1
      return
    fi
    printf 'ok   %-14s %s\n' "$label" "$value"
  elif [[ "$required" == "required" ]]; then
    printf 'MISS %-14s (required)\n' "$label"
    STATUS=1
  else
    printf 'skip %-14s (optional, not found)\n' "$label"
  fi
}

echo "[ORFS]"
print_row ORFS_ROOT "${ORFS_ROOT:-}" required dir
print_row FLOW_DIR  "${FLOW_DIR:-}" required dir
print_row PDK_ROOT  "${PDK_ROOT:-}" optional dir
print_row SKY130A_DIR "${SKY130A_DIR:-}" optional dir

# ORFS execs its python helpers directly (0 of 96 were executable in the E6 checkout,
# so `make synth` died with Error 126), and flow/settings.mk `=` assignments override
# the *_EXE the env exports (E6 F3: a dead YOSYS_EXE there, while this table said ok).
if [[ -n "${FLOW_DIR:-}" && -f "$FLOW_DIR/scripts/defaults.py" && ! -x "$FLOW_DIR/scripts/defaults.py" ]]; then
  printf 'BAD  %-14s %s (not executable; ORFS execs it directly)\n' "ORFS helpers" "$FLOW_DIR/scripts/defaults.py"
  STATUS=1
fi
if [[ -n "${FLOW_DIR:-}" && -f "$FLOW_DIR/settings.mk" ]]; then
  while read -r _var _path; do
    case "$_path" in          # resolved the way make will: relative to FLOW_DIR, or PATH
      *'$('*) continue ;;
      /*)  _abs="$_path" ;;
      */*) _abs="$FLOW_DIR/$_path" ;;
      *)   _abs="$(command -v "$_path" 2>/dev/null || true)" ;;
    esac
    [[ -n "$_abs" && -f "$_abs" && -x "$_abs" ]] && continue
    printf 'BAD  %-14s %s (flow/settings.mk overrides the env with a missing binary)\n' "$_var" "$_path"
    STATUS=1
  done < <(sed -n 's/^[[:space:]]*\(override[[:space:]]\+\)\?\(export[[:space:]]\+\)\?\([A-Z_]*_EXE\)[[:space:]]*:\{0,2\}=[[:space:]]*\([^[:space:]#]\+\).*/\3 \4/p' \
             "$FLOW_DIR/settings.mk")
fi

echo
echo "[required tools]"
print_row OPENROAD_EXE "${OPENROAD_EXE:-}" required exe
print_row YOSYS_EXE    "${YOSYS_EXE:-}"    required exe
print_row IVERILOG_EXE "${IVERILOG_EXE:-}" required exe
print_row VVP_EXE      "${VVP_EXE:-}"      required exe
print_row python3      "$(command -v python3 || true)" required orfs_python

echo
echo "[optional tools]"
print_row VERILATOR_EXE "${VERILATOR_EXE:-}" optional exe
print_row KLAYOUT_CMD   "${KLAYOUT_CMD:-}"   optional exe
print_row MAGIC_EXE     "${MAGIC_EXE:-}"     optional exe
print_row NETGEN_EXE    "${NETGEN_EXE:-}"    optional exe
print_row STA_EXE       "${STA_EXE:-}"       optional exe
print_row gtkwave       "$(command -v gtkwave || true)" optional

echo
echo "[graph dataset stage (def-graph)]"
# The PyG graph-assembly stage (run_graphs.sh) needs a python with
# torch+torch_geometric+pandas, pinned via R2G_GRAPH_PYTHON. Optional: absence
# only SKIPs the graph stage, so it never fails the required-tools gate.
_gp_found=""
# Probe as the launchers run it: without the caller's PYTHONHOME (see run_graphs.sh).
_gp_env=(env -u PYTHONHOME -u PYTHONEXECUTABLE -u PYTHONNOUSERSITE)
for _c in "${R2G_GRAPH_PYTHON:-}" python3; do
  [[ -z "$_c" ]] && continue
  if "${_gp_env[@]}" "$_c" -c "import torch, torch_geometric, pandas" >/dev/null 2>&1; then
    _gp_found="$(command -v "$_c" 2>/dev/null || echo "$_c")"; break
  fi
done
# Set but unable to start is BAD, not an optional miss: run_graphs.sh fails loud on it.
if [[ -n "${R2G_GRAPH_PYTHON:-}" ]] && ! "${_gp_env[@]}" "$R2G_GRAPH_PYTHON" -c pass >/dev/null 2>&1; then
  printf 'BAD  %-14s %s (configured but cannot start; run_graphs.sh fails on it)\n' R2G_GRAPH_PYTHON "$R2G_GRAPH_PYTHON"
  STATUS=1
else
  print_row R2G_GRAPH_PYTHON "$_gp_found" optional
  if [[ -n "$_gp_found" ]]; then
    _tv="$("${_gp_env[@]}" "$_gp_found" -c 'import torch, torch_geometric as g; print("torch", torch.__version__, "· pyg", g.__version__)' 2>/dev/null || true)"
    [[ -n "$_tv" ]] && printf '     %s\n' "$_tv"
  elif [[ -n "${R2G_GRAPH_PYTHON:-}" ]]; then
    printf '     (R2G_GRAPH_PYTHON=%s set but torch/torch_geometric/pandas not importable)\n' "${R2G_GRAPH_PYTHON}"
  fi
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
echo "[platform strict-signoff capability]"
# Tool presence alone does not prove a platform can satisfy a STRICT signoff
# (2026-07-21 pilot P0-3: green ENV, but nangate45 had no LVS rule and a 0-area
# antenna diode). Probe borrowed from the sibling signoff-loop skill; advisory
# here — export R2G_STRICT_PLATFORMS="nangate45" to make readiness REQUIRED.
_cap_probe="$_skills_root/signoff-loop/scripts/flow/platform_capability.py"
if [[ -f "$_cap_probe" && -n "${FLOW_DIR:-}" && -d "$FLOW_DIR/platforms" ]]; then
  python3 "$_cap_probe" --flow-dir "$FLOW_DIR" --summary 2>/dev/null \
    || echo "--    (capability probe failed)"
  if [[ -n "${R2G_STRICT_PLATFORMS:-}" ]]; then
    for _p in ${R2G_STRICT_PLATFORMS}; do
      if ! python3 "$_cap_probe" --flow-dir "$FLOW_DIR" --platform "$_p" --strict >/dev/null 2>&1; then
        printf 'MISS strict capability: %s (required via R2G_STRICT_PLATFORMS)\n' "$_p"
        STATUS=1
      fi
    done
  fi
else
  echo "--    (probe or ORFS platforms dir unavailable — capability not probed)"
fi

echo
echo "[how to override]"
echo "  bash ../../bootstrap.sh --dry-run   # auto-detect + plan the toolchain (then drop --dry-run)"
echo "  ORFS_ROOT=/your/path OPENROAD_EXE=/your/openroad bash check_env.sh"
echo "  export R2G_ENV_FILE=~/my-r2g-env.sh   # shell snippet with exports"
echo "  or write to  $(dirname "${BASH_SOURCE[0]}")/../../references/env.local.sh"

exit "$STATUS"
