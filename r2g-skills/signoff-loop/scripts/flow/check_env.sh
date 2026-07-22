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

# Target-platform mode (RMD-P0-03, three-platform pilot 2026-07-22): when the
# campaign's platform is NAMED — `check_env.sh --platform sky130hs` or
# R2G_TARGET_PLATFORM — its strict-signoff capability is a REQUIRED, fail-closed
# postcondition, not advisory. The pilot awarded ENV credit and spent hours in
# ORFS on platforms that could never satisfy the signoff policy (no LVS rule,
# legacy sky130hs .lyt) because readiness was only enforced when the operator
# remembered to export R2G_STRICT_PLATFORMS. Entry points must call THIS script
# (which loads the resolved env first) rather than probing from a bare shell —
# an empty environment falsely reports installed tools as missing.
TARGET_PLATFORMS="${R2G_TARGET_PLATFORM:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform)
      TARGET_PLATFORMS="${TARGET_PLATFORMS:+$TARGET_PLATFORMS }${2:?--platform needs a value}"
      shift 2 ;;
    *) shift ;;
  esac
done

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
# A green tool table above does NOT mean a platform can satisfy a STRICT signoff
# (2026-07-21 pilot P0-3: ENV passed while nangate45 had no LVS rule and an
# unusable 0-area antenna diode; the gap surfaced only after multi-hour flows).
# platform_capability.py probes DRC deck / LVS path / antenna model / RCX rules /
# timing libs per platform (incl. the sky130hs modern-.lyt postcondition,
# RMD-P0-04). Advisory for unnamed platforms; REQUIRED and fail-closed for a
# NAMED target platform (`--platform X` / R2G_TARGET_PLATFORM, RMD-P0-03) and
# for R2G_STRICT_PLATFORMS.
if [[ -n "${FLOW_DIR:-}" && -d "$FLOW_DIR/platforms" ]]; then
  python3 "$(dirname "${BASH_SOURCE[0]}")/platform_capability.py" \
    --flow-dir "$FLOW_DIR" --summary 2>/dev/null || echo "--    (capability probe failed)"
  for _p in ${TARGET_PLATFORMS} ${R2G_STRICT_PLATFORMS:-}; do
    if ! python3 "$(dirname "${BASH_SOURCE[0]}")/platform_capability.py" \
           --flow-dir "$FLOW_DIR" --platform "$_p" --strict >/dev/null 2>&1; then
      printf 'MISS strict capability: %s (target platform must be strict_signoff_ready — RMD-P0-03)\n' "$_p"
      STATUS=1
    fi
  done
else
  if [[ -n "$TARGET_PLATFORMS" ]]; then
    printf 'MISS strict capability: %s (no ORFS platforms dir — cannot verify, failing closed)\n' "$TARGET_PLATFORMS"
    STATUS=1
  else
    echo "--    (no ORFS platforms dir — capability not probed)"
  fi
fi

echo
echo "[how to override]"
echo "  bash ../../../bootstrap.sh --dry-run   # auto-detect + plan the toolchain (then drop --dry-run)"
echo "  ORFS_ROOT=/your/path OPENROAD_EXE=/your/openroad bash check_env.sh"
echo "  export R2G_ENV_FILE=~/my-r2g-env.sh   # shell snippet with exports"
echo "  or write to  $(dirname "${BASH_SOURCE[0]}")/../../references/env.local.sh"

exit "$STATUS"
