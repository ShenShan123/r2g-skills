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
# Presence is not readiness: a copied ELF can exist but fail to load its libs.
# Bound probes without needing a healthy Python to test Python itself.
check_required_runtime() {
  local label="$1" executable="$2"; shift 2
  local limiter output rc
  limiter="$(command -v timeout 2>/dev/null || command -v gtimeout 2>/dev/null || true)"
  if [[ -z "$executable" || ! -f "$executable" || ! -x "$executable" || -z "$limiter" ]]; then
    printf 'MISS runtime: %s (executable or timeout/gtimeout unavailable)\n' "$label"
    STATUS=1
    return
  fi
  output="$("$limiter" --kill-after=2s 30s "$executable" "$@" 2>&1)"; rc=$?
  if [[ "$rc" -ne 0 || -z "${output//[[:space:]]/}" ]]; then
    printf 'MISS runtime: %s (probe rc=%s, empty output or runtime failure)\n' "$label" "$rc"
    STATUS=1
  else
    printf 'ok   runtime: %s\n' "$label"
  fi
}

check_required_orfs() {
  if [[ -z "${ORFS_ROOT:-}" || ! -f "$ORFS_ROOT/flow/Makefile" ||
        -z "${FLOW_DIR:-}" || ! -f "$FLOW_DIR/Makefile" ]]; then
    echo 'MISS ORFS runtime data: required flow/Makefile unavailable'
    STATUS=1
  fi
}
print_row() {
  local label="$1" value="$2" required="$3"
  if [[ -n "$value" ]]; then
    printf 'ok   %-16s %s\n' "$label" "$value"
  elif [[ "$required" == "required" ]]; then
    printf 'MISS %-16s (required)\n' "$label"
    STATUS=1
  else
    printf 'skip %-16s (optional, not found)\n' "$label"
  fi
}

echo "[ORFS / platform data]"
print_row ORFS_ROOT "${ORFS_ROOT:-}" required
print_row FLOW_DIR  "${FLOW_DIR:-}"  required
print_row python3   "$(command -v python3 || true)" required
print_row PDK_ROOT  "${PDK_ROOT:-}"  optional

echo
echo "[required runtime health]"
check_required_orfs
check_required_runtime python3 "$(command -v python3 || true)" -c 'import sys; print(sys.version); sys.exit(0 if sys.version_info >= (3, 10) else 1)'

echo
echo "[graph dataset stage (torch venv)]"
# Same import probe run_graphs.sh uses. Absence only SKIPs the PyG assembly stage
# (labels/features CSVs still build), so it is optional — not a required MISS.
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
else
  printf '     graph stage SKIPs cleanly without it; install a torch venv on /proj and pin R2G_GRAPH_PYTHON.\n'
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
