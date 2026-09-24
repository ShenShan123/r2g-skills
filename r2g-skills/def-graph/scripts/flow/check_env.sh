#!/usr/bin/env bash
set -uo pipefail
#
# Report the tool environment the def-graph skill has discovered. This skill
# CONSUMES a signed-off backend run (DEF/LEF/SPEF) and builds PyG graph datasets;
# it never runs PnR. So its hard requirement is just ORFS (for platform liberty/LEF
# resolution via resolve_platform_paths.sh) + python3; the PyG graph-assembly stage
# additionally wants a torch venv (R2G_GRAPH_PYTHON). Everything else is optional.
#
# Exits 0 if the required pieces are present, 1 otherwise. Honors the same override
# chain as the flow scripts (env vars > $R2G_ENV_FILE > references/env.local.sh).
# Self-contained: a def-graph-only install has no signoff-loop checker to defer to.

# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_env.sh"

STATUS=0
print_row() {
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
      printf 'BAD  %-16s %s (%s)\n' "$label" "$value" "$why"
      STATUS=1
      return
    fi
    printf 'ok   %-16s %s\n' "$label" "$value"
  elif [[ "$required" == "required" ]]; then
    printf 'MISS %-16s (required)\n' "$label"
    STATUS=1
  else
    printf 'skip %-16s (optional, not found)\n' "$label"
  fi
}

echo "[ORFS / platform data]"
print_row ORFS_ROOT "${ORFS_ROOT:-}" required dir
print_row FLOW_DIR  "${FLOW_DIR:-}"  required dir
print_row python3   "$(command -v python3 || true)" required
print_row PDK_ROOT  "${PDK_ROOT:-}"  optional dir

echo
echo "[graph dataset stage (torch venv)]"
# Same import probe run_graphs.sh uses. Absence only SKIPs the PyG assembly stage
# (labels/features CSVs still build), so it is optional — not a required MISS.
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
  printf 'BAD  %-16s %s (configured but cannot start; run_graphs.sh fails on it)\n' R2G_GRAPH_PYTHON "$R2G_GRAPH_PYTHON"
  STATUS=1
else
  print_row R2G_GRAPH_PYTHON "$_gp_found" optional
  if [[ -n "$_gp_found" ]]; then
    _tv="$("${_gp_env[@]}" "$_gp_found" -c 'import torch, torch_geometric as g; print("torch", torch.__version__, "· pyg", g.__version__)' 2>/dev/null || true)"
    [[ -n "$_tv" ]] && printf '     %s\n' "$_tv"
  elif [[ -n "${R2G_GRAPH_PYTHON:-}" ]]; then
    printf '     (R2G_GRAPH_PYTHON=%s set but torch/torch_geometric/pandas not importable)\n' "${R2G_GRAPH_PYTHON}"
  else
    printf '     graph stage SKIPs cleanly without it; install a torch venv on /proj and pin R2G_GRAPH_PYTHON.\n'
  fi
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
echo "  bash ../../../bootstrap.sh --dry-run   # auto-detect + plan the toolchain (then drop --dry-run)"
echo "  export R2G_ENV_FILE=~/my-r2g-env.sh    # shell snippet with exports"
echo "  or write to  $(dirname "${BASH_SOURCE[0]}")/../../references/env.local.sh"

exit "$STATUS"
