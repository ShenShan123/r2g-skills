#!/usr/bin/env bash
# Helper (not a tier): materialize platform DRC/LVS/antenna rule decks into the
# ORFS checkout. Upstream ORFS ships no LVS rule for nangate45 and no antenna
# model in its tech LEF, so `make lvs` silently skips and repair_antennas is inert.
# This dispatches to the repo's idempotent, backup-aware nangate45 rule installers
# when they are reachable (they live in the agent-r2g repo `tools/`, not in the
# installed skill). Best-effort for missing installers (HINT, no failure) — with
# ONE exception: the sky130hs .lyt lefdef repair is a REQUIRED, verified
# postcondition (--check + GDS geometry canary) and a failed repair FAILS setup
# (RMD-P0-04, three-platform pilot 2026-07-22).
set -uo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/../flow/_env.sh" 1>&2
# shellcheck source=/dev/null
source "$HERE/_setup_lib.sh"
setup_parse "$@"

# eda-install/scripts/setup → r2g-skills (../../..) → repo root (../../../..)
_repo="$(cd -- "$HERE/../../../.." 2>/dev/null && pwd || true)"
_found=0
for _base in "${R2G_TOOLS_DIR:-}" "$_repo/tools"; do
  [[ -z "$_base" || ! -d "$_base" ]] && continue
  for _rule in install_nangate45_lvs.sh install_nangate45_drc.sh install_nangate45_antenna.sh; do
    if [[ -f "$_base/$_rule" ]]; then
      log "nangate45 rules: $_rule"
      run bash "$_base/$_rule" || hint "$_rule returned non-zero (deck left unchanged)"
      _found=1
    fi
  done
  # sky130hs klayout lefdef repair (failure-patterns.md #33 / RMD-P0-04): this
  # ORFS ships sky130hs.lyt with LEGACY lefdef reader options, so def2stream
  # maps every DEF-derived shape to unmappable legacy layer numbers (portless
  # magic extraction -> every Netgen LVS a false top-pin mismatch). Idempotent;
  # backs up .orig. Unlike the other rule installers this one is a REQUIRED,
  # verified postcondition when the checkout ships sky130hs: the three-platform
  # pilot proved a best-effort hint lets an unpatched .lyt silently invalidate
  # every sky130hs LVS while ENV stays green.
  if [[ -f "$_base/patch_sky130hs_lyt.py" ]]; then
    log "sky130hs lyt lefdef patch: patch_sky130hs_lyt.py"
    run python3 "$_base/patch_sky130hs_lyt.py" || hint "patch_sky130hs_lyt.py returned non-zero (lyt unchanged)"
    _found=1
    if [[ "$DRY" != "1" && -f "${FLOW_DIR:-}/platforms/sky130hs/sky130hs.lyt" ]]; then
      if ! python3 "$_base/patch_sky130hs_lyt.py" --check; then
        _SKY130HS_POSTCOND_FAIL=1
        hint "sky130hs.lyt POSTCONDITION FAILED: legacy lefdef options still live — sky130hs GDS/LVS unusable until repaired (RMD-P0-04)"
      elif [[ -f "$_base/sky130hs_gds_canary.py" ]]; then
        # Geometry canary: prove the DEF->GDS import path end-to-end (a green
        # --check trusts option NAMES; the canary checks actual layer numbers).
        # klayout absent -> rc 3 -> soft hint (the merge can't run either).
        python3 "$_base/sky130hs_gds_canary.py" --flow-dir "${FLOW_DIR}" 1>&2
        _canary_rc=$?
        if [[ "$_canary_rc" == "2" ]]; then
          _SKY130HS_POSTCOND_FAIL=1
          hint "sky130hs GDS geometry canary FAILED: DEF geometry lands on unmappable layers (RMD-P0-04)"
        elif [[ "$_canary_rc" != "0" ]]; then
          hint "sky130hs GDS geometry canary could not run (rc=$_canary_rc, klayout missing?) — postcondition unverified"
        fi
      fi
    fi
  fi
  [[ "$_found" == "1" ]] && break
done

if [[ "$_found" != "1" ]]; then
  hint "platform rule installers not found (expected in the agent-r2g repo tools/ — set R2G_TOOLS_DIR); nangate45 LVS/antenna decks + sky130hs lyt unchanged"
fi

# Fail-closed exit (RMD-P0-04): a broken sky130hs GDS-import postcondition must
# FAIL setup, not degrade to a hint — every sky130hs LVS verdict downstream of a
# legacy .lyt is invalid, and evidence produced before the repair must be
# regenerated from finish.
if [[ "${_SKY130HS_POSTCOND_FAIL:-0}" == "1" ]]; then
  die "sky130hs .lyt repair postcondition failed — fix the patch (tools/patch_sky130hs_lyt.py), then regenerate all sky130hs GDS/LVS evidence from finish"
fi
