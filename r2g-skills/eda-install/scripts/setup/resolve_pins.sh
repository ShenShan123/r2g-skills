#!/usr/bin/env bash
# Resolve the ONE canonical toolchain a repeat bootstrap must act on (RMD4-P1-01).
#
# Layer 0 of the bootstrap, BEFORE detect → plan → install → pin → verify.
#
# The defect this closes (three-platform fixed pilot 2026-07-27): a plain
# `bootstrap.sh` with no explicit $R2G_ENV_FILE selected /opt/OpenROAD-flow-scripts
# and an ambient PDK even though BOTH deployed consumer skills already pinned a
# different ORFS and a conda PDK — the ones every production flow actually uses.
# It then tried to install strict platform rules into that wrong, read-only
# checkout and failed. Root cause: eda-install has no references/env.local.sh of
# its own, so its copy of _env.sh finds no pin at resolution step 3 and falls
# through to the hardcoded candidate list (where /opt sorts before /proj);
# write_env_local.sh recalls the consumer pins only at the PIN step, far too late
# to make the plan or the install idempotent.
#
# Precedence (documented contract, highest first):
#   1. an explicit operator selection — $R2G_ENV_FILE, or $ORFS_ROOT/$PDK_ROOT
#      exported into the environment. Reported, validated, never silently ignored.
#   2. AGREEING pins already deployed to the consumer skills.
#   3. CONFLICTING deployed pins -> FAIL CLOSED (exit 4). Repeat bootstrap must
#      not pick a winner between two live installations by itself.
#   4. no deployed pins at all -> fresh-machine autodetection (exit 3, advisory).
#
# CONTRACT: stdout is CLEAN KEY=VALUE lines only, same style as detect_env.sh;
# every key is always emitted (empty value == absent). Diagnostics to stderr.
# Exit: 0 selection resolved | 3 no pins (autodetect) | 4 conflicting pins.
set -uo pipefail

_SETUP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
_SKILLS_ROOT="$(cd -- "$_SETUP_DIR/../../.." && pwd)"      # …/r2g-skills

# Consumer skills that carry a deployed pin file. eda-install is NOT one: it is
# the producer, and giving it its own pin would create a second source of truth.
CONSUMERS=(signoff-loop def-graph rtl-acquire)

emit() { printf '%s=%s\n' "$1" "${2:-}"; }

# Parse `export KEY="value"` out of a pin file WITHOUT sourcing it — a pin file is
# operator-editable, and resolving a toolchain must not depend on executing it.
pin_value() {  # file key
  sed -n "s/^[[:space:]]*export[[:space:]]\+$2=\"\{0,1\}\([^\"]*\)\"\{0,1\}[[:space:]]*$/\1/p" \
      "$1" 2>/dev/null | tail -1
}

valid_orfs()  { [[ -n "${1:-}" && -f "$1/flow/Makefile" ]]; }
valid_pdk()   { [[ -n "${1:-}" && -d "$1" ]]; }

# --- gather deployed pins ------------------------------------------------------
declare -a PIN_FILES=() PIN_ORFS=() PIN_PDK=() PIN_SKILLS=()
for _c in "${CONSUMERS[@]}"; do
  _f="$_SKILLS_ROOT/$_c/references/env.local.sh"
  [[ -f "$_f" ]] || continue
  _o="$(pin_value "$_f" ORFS_ROOT)"
  _p="$(pin_value "$_f" PDK_ROOT)"
  # Validate BEFORE admitting as a candidate: a stale pin to a deleted tree must
  # not win over autodetection, and must not count as a "conflict" either.
  valid_orfs "$_o" || { echo "[resolve_pins] $_c: ORFS_ROOT pin invalid/absent (${_o:-<none>})" >&2; _o=""; }
  valid_pdk  "$_p" || { echo "[resolve_pins] $_c: PDK_ROOT pin invalid/absent (${_p:-<none>})"  >&2; _p=""; }
  [[ -z "$_o" && -z "$_p" ]] && continue
  PIN_FILES+=("$_f"); PIN_ORFS+=("$_o"); PIN_PDK+=("$_p"); PIN_SKILLS+=("$_c")
done

# --- explicit operator selection (precedence 1) --------------------------------
EXPLICIT_SOURCE=""
EXPLICIT_ORFS="${ORFS_ROOT:-}"
EXPLICIT_PDK="${PDK_ROOT:-}"
if [[ -n "${R2G_ENV_FILE:-}" && -f "${R2G_ENV_FILE:-}" ]]; then
  EXPLICIT_SOURCE="env_file:$R2G_ENV_FILE"
  [[ -z "$EXPLICIT_ORFS" ]] && EXPLICIT_ORFS="$(pin_value "$R2G_ENV_FILE" ORFS_ROOT)"
  [[ -z "$EXPLICIT_PDK"  ]] && EXPLICIT_PDK="$(pin_value "$R2G_ENV_FILE" PDK_ROOT)"
elif [[ -n "$EXPLICIT_ORFS" || -n "$EXPLICIT_PDK" ]]; then
  EXPLICIT_SOURCE="environment"
fi

# --- conflict detection among the deployed pins --------------------------------
CONFLICT=0
CONFLICT_DETAIL=""
AGREED_ORFS=""; AGREED_PDK=""; AGREED_FILE=""
if (( ${#PIN_FILES[@]} > 0 )); then
  AGREED_ORFS="${PIN_ORFS[0]}"; AGREED_PDK="${PIN_PDK[0]}"; AGREED_FILE="${PIN_FILES[0]}"
  for _i in "${!PIN_FILES[@]}"; do
    # An EMPTY value is "this consumer does not pin it", not a disagreement.
    if [[ -n "${PIN_ORFS[$_i]}" && -n "$AGREED_ORFS" && "${PIN_ORFS[$_i]}" != "$AGREED_ORFS" ]]; then
      CONFLICT=1
      CONFLICT_DETAIL+="ORFS_ROOT: ${PIN_SKILLS[0]}=$AGREED_ORFS vs ${PIN_SKILLS[$_i]}=${PIN_ORFS[$_i]}; "
    fi
    if [[ -n "${PIN_PDK[$_i]}" && -n "$AGREED_PDK" && "${PIN_PDK[$_i]}" != "$AGREED_PDK" ]]; then
      CONFLICT=1
      CONFLICT_DETAIL+="PDK_ROOT: ${PIN_SKILLS[0]}=$AGREED_PDK vs ${PIN_SKILLS[$_i]}=${PIN_PDK[$_i]}; "
    fi
    [[ -z "$AGREED_ORFS" ]] && AGREED_ORFS="${PIN_ORFS[$_i]}"
    [[ -z "$AGREED_PDK"  ]] && AGREED_PDK="${PIN_PDK[$_i]}"
  done
fi

# --- decide --------------------------------------------------------------------
SEL_SOURCE=""; SEL_ORFS=""; SEL_PDK=""; SEL_ENV_FILE=""; OVERRIDES_PINS=0; RC=0
if [[ -n "$EXPLICIT_SOURCE" ]]; then
  SEL_SOURCE="explicit:$EXPLICIT_SOURCE"
  SEL_ORFS="$EXPLICIT_ORFS"; SEL_PDK="$EXPLICIT_PDK"
  SEL_ENV_FILE="${R2G_ENV_FILE:-}"
  # An explicit selection wins — but it is REPORTED when it disagrees with an
  # agreeing deployed pin, never applied silently.
  if (( CONFLICT == 0 )) && [[ -n "$AGREED_ORFS" && -n "$SEL_ORFS" && "$SEL_ORFS" != "$AGREED_ORFS" ]]; then
    OVERRIDES_PINS=1
    echo "[resolve_pins] NOTE: explicit ORFS_ROOT=$SEL_ORFS overrides the agreeing deployed pin $AGREED_ORFS" >&2
  fi
elif (( CONFLICT == 1 )); then
  SEL_SOURCE="conflict"
  RC=4
  echo "[resolve_pins] FAIL CLOSED: deployed skill pins disagree — $CONFLICT_DETAIL" >&2
  echo "[resolve_pins] Choose one explicitly: export R2G_ENV_FILE=<pin file>, or export ORFS_ROOT/PDK_ROOT." >&2
elif (( ${#PIN_FILES[@]} > 0 )); then
  SEL_SOURCE="deployed_pins"
  SEL_ORFS="$AGREED_ORFS"; SEL_PDK="$AGREED_PDK"; SEL_ENV_FILE="$AGREED_FILE"
else
  SEL_SOURCE="autodetect"
  RC=3
  echo "[resolve_pins] no valid deployed pins — falling back to fresh-machine autodetection" >&2
fi

# Digest of the selected pin file: recorded in install_manifest.json so a later
# run can prove it acted on the same immutable selection.
SEL_DIGEST=""
if [[ -n "$SEL_ENV_FILE" && -f "$SEL_ENV_FILE" ]]; then
  SEL_DIGEST="$(sha256sum "$SEL_ENV_FILE" 2>/dev/null | cut -d' ' -f1)"
fi

emit SELECTION_SOURCE   "$SEL_SOURCE"
emit SELECTED_ORFS_ROOT "$SEL_ORFS"
emit SELECTED_PDK_ROOT  "$SEL_PDK"
emit SELECTED_ENV_FILE  "$SEL_ENV_FILE"
emit SELECTED_ENV_SHA256 "$SEL_DIGEST"
emit PIN_CONFLICT       "$CONFLICT"
emit PIN_CONFLICT_DETAIL "$CONFLICT_DETAIL"
emit PIN_FILES          "${PIN_FILES[*]:-}"
emit OVERRIDES_PINS     "$OVERRIDES_PINS"
exit "$RC"
