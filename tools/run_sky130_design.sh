#!/usr/bin/env bash
# Per-design sky130hd PD-flow driver for the sky130 campaign.
#
# Runs the full r2g flow (materialize -> ORFS -> timing gate -> DRC/LVS/RCX ->
# ranked fix-loop on failures -> ingest) for ONE source project, honest-final on
# anything that cannot reach clean signoff, and prints exactly ONE line:
#     RESULT_JSON: {...}
# Verbose stage output goes to <dest>/sky130_driver.log so subagent context stays tiny.
#
# usage: run_sky130_design.sh <source_project_dir>
set -uo pipefail

REPO="/proj/workarea/user5/agent-r2g"
SKILL="$REPO/r2g-skills/signoff-loop"
SRC="${1:-}"
[[ -z "$SRC" ]] && { echo 'RESULT_JSON: {"error":"no source dir"}'; exit 1; }
SRC="$(cd "$SRC" && pwd)"
BASE="$(basename "$SRC")"

# Source the skill's env discovery so PDK_ROOT/MAGIC_EXE/NETGEN_EXE (defined in
# references/env.local.sh) are exported in THIS shell. Without it, the LVS-tool
# gate below sees them unset and silently falls back to KLayout — which cannot
# reconcile sky130 flat-transistor extraction, yielding a bogus lvs_fail on every
# design (root-caused on the 2026-06-11 smoke test). _env.sh is sourced (not run)
# by every flow script and restores the caller's shell flags on exit.
# shellcheck source=/dev/null
source "$SKILL/scripts/flow/_env.sh" >/dev/null 2>&1 || true
DEST="$REPO/design_cases/${BASE}__sky130hd"
RESULTS_DIR="$REPO/design_cases/_batch/sky130hd_results"
mkdir -p "$RESULTS_DIR"
LOG="$DEST/sky130_driver.log"
mkdir -p "$DEST"
: > "$LOG"
START=$(date +%s)

# emit <stage> <orfs_status> <timing_tier> <drc> <drc_viol> <lvs> <lvs_class> <rcx> <pass> <residual> <fixes_csv> <note>
emit() {
  local elapsed=$(( $(date +%s) - START ))
  python3 - "$BASE" "$1" "$2" "$3" "$4" "$5" "$6" "$7" "$8" "$9" "${10}" "${11}" "${12}" "$elapsed" <<'PY'
import json, sys
k=["design","stage","orfs_status","timing_tier","drc_status","drc_violations",
   "lvs_status","lvs_mismatch_class","rcx_status","signoff_pass","residual_class",
   "fixes","note","elapsed_s"]
v=sys.argv[1:]
d=dict(zip(k,v))
d["drc_violations"]=int(v[5]) if v[5].lstrip("-").isdigit() else None
d["signoff_pass"]= v[9]=="true"
d["fixes"]=[s for s in v[11].split(",") if s]
d["elapsed_s"]=int(v[13])
line=json.dumps(d)
print("RESULT_JSON: "+line)
import os
rf=os.environ.get("R2G_RESULT_FILE")
if rf:
    open(rf,"w").write(line+"\n")
PY
}

export R2G_RESULT_FILE="$RESULTS_DIR/${BASE}.json"
jval() { python3 -c "import json,sys;
try: print(json.load(open(sys.argv[1])).get(sys.argv[2],''))
except Exception: print('')" "$1" "$2" 2>/dev/null; }

cd "$REPO"
echo "=== sky130 driver: $BASE  $(date) ===" >>"$LOG"

# 1) materialize sky130hd project ----------------------------------------------
python3 tools/mk_sky130_project.py "$SRC" "$DEST" >>"$LOG" 2>&1
mk=$?
if [[ $mk -eq 2 ]]; then emit setup partial - - - - - - false macro_unportable "" "needs sky130 SRAM"; exit 0; fi
if [[ $mk -ne 0 ]]; then emit setup partial - - - - - - false setup_error "" "mk_sky130_project failed"; exit 0; fi

# 2) validate (non-fatal) -------------------------------------------------------
python3 "$SKILL/scripts/project/validate_config.py" "$DEST" >>"$LOG" 2>&1 || echo "validate warnings" >>"$LOG"

# 3) ORFS backend ---------------------------------------------------------------
echo "--- run_orfs ---" >>"$LOG"
ORFS_TIMEOUT="${ORFS_TIMEOUT:-5400}" bash "$SKILL/scripts/flow/run_orfs.sh" "$DEST" sky130hd >>"$LOG" 2>&1
# success == a final ODB/GDS exists somewhere we can extract from
python3 "$SKILL/scripts/extract/extract_ppa.py" "$DEST" "$DEST/reports/ppa.json" >>"$LOG" 2>&1
GDS=$(find "$DEST/backend" -name '6_final.gds' 2>/dev/null | head -1)
ODB=$(find "$DEST/backend" -name '6_final.odb' 2>/dev/null | head -1)
if [[ -z "$GDS" && -z "$ODB" ]]; then
  fs=$(jval "$DEST/reports/ppa.json" orfs_fail_stage)
  python3 "$SKILL/knowledge/ingest_run.py" "$DEST" >>"$LOG" 2>&1 || true
  emit orfs fail - - - - - - false "orfs_${fs:-incomplete}" "" "no final GDS/ODB"; exit 0
fi

# 4) timing gate ----------------------------------------------------------------
echo "--- check_timing ---" >>"$LOG"
python3 "$SKILL/scripts/reports/check_timing.py" "$DEST" --journal >>"$LOG" 2>&1 || true
TIER=$(jval "$DEST/reports/timing_check.json" tier); TIER="${TIER:-clean}"
echo "timing tier=$TIER" >>"$LOG"
if [[ "$TIER" == "minor" ]]; then
  # auto-bump clk_period to suggested, re-run backend once
  SUG=$(jval "$DEST/reports/timing_check.json" suggested_clock_period)
  if [[ -n "$SUG" && "$SUG" != "None" ]]; then
    echo "minor timing -> bump clk_period to $SUG, re-run" >>"$LOG"
    sed -i -E "s/(set[[:space:]]+clk_period[[:space:]]+)[0-9.]+/\1${SUG}/" "$DEST/constraints/constraint.sdc" 2>>"$LOG" || true
    ORFS_TIMEOUT="${ORFS_TIMEOUT:-5400}" FROM_STAGE=floorplan bash "$SKILL/scripts/flow/run_orfs.sh" "$DEST" sky130hd >>"$LOG" 2>&1
    python3 "$SKILL/scripts/extract/extract_ppa.py" "$DEST" "$DEST/reports/ppa.json" >>"$LOG" 2>&1
    python3 "$SKILL/scripts/reports/check_timing.py" "$DEST" --journal >>"$LOG" 2>&1 || true
    TIER=$(jval "$DEST/reports/timing_check.json" tier); TIER="${TIER:-clean}"
  fi
fi
if [[ "$TIER" == "moderate" || "$TIER" == "severe" || "$TIER" == "unconstrained" ]]; then
  python3 "$SKILL/knowledge/ingest_run.py" "$DEST" >>"$LOG" 2>&1 || true
  emit timing partial "$TIER" - - - - - false "timing_${TIER}" "" "stopped at timing gate (no silent relax)"; exit 0
fi

# 5) signoff: DRC / LVS / RCX ---------------------------------------------------
echo "--- DRC ---" >>"$LOG"
bash "$SKILL/scripts/flow/run_drc.sh" "$DEST" sky130hd >>"$LOG" 2>&1 || true
python3 "$SKILL/scripts/extract/extract_drc.py" "$DEST" "$DEST/reports/drc.json" >>"$LOG" 2>&1 || true
echo "--- LVS ---" >>"$LOG"
# sky130 genuine LVS needs Netgen+Magic+sky130A PDK (KLayout's bundled sky130 rule
# cannot reconcile the flat-transistor extraction; see failure-patterns "sky130 LVS").
# Prefer Netgen when the tooling is installed; otherwise fall back to KLayout and let
# the verdict be recorded honestly (lvs_env_blocked-class on resume environments).
LVS_TOOL=klayout
if [[ -n "${PDK_ROOT:-}" && -d "${PDK_ROOT}/sky130A" ]] \
   && { [[ -n "${MAGIC_EXE:-}" ]] || command -v magic >/dev/null 2>&1; } \
   && { [[ -n "${NETGEN_EXE:-}" ]] || command -v netgen >/dev/null 2>&1 || command -v netgen-lvs >/dev/null 2>&1; }; then
  LVS_TOOL=netgen
  echo "LVS via Netgen (sky130A PDK + magic + netgen present)" >>"$LOG"
  bash "$SKILL/scripts/flow/run_netgen_lvs.sh" "$DEST" sky130hd >>"$LOG" 2>&1 || true
  if [[ -f "$DEST/lvs/netgen_lvs_result.json" ]]; then
    cp "$DEST/lvs/netgen_lvs_result.json" "$DEST/reports/lvs.json"
  fi
else
  echo "LVS via KLayout (Netgen/PDK absent)" >>"$LOG"
  bash "$SKILL/scripts/flow/run_lvs.sh" "$DEST" sky130hd >>"$LOG" 2>&1 || true
  python3 "$SKILL/scripts/extract/extract_lvs.py" "$DEST" "$DEST/reports/lvs.json" >>"$LOG" 2>&1 || true
fi
echo "--- RCX ---" >>"$LOG"
bash "$SKILL/scripts/flow/run_rcx.sh" "$DEST" sky130hd >>"$LOG" 2>&1 || true
python3 "$SKILL/scripts/extract/extract_rcx.py" "$DEST" "$DEST/reports/rcx.json" >>"$LOG" 2>&1 || true

DRC=$(jval "$DEST/reports/drc.json" status)
LVS=$(jval "$DEST/reports/lvs.json" status)

# 6) ranked fix-loop on DRC/LVS failure (the "different strategy per PD issue") --
if [[ "$DRC" == "fail" || "$LVS" == "fail" ]]; then
  echo "--- ranked candidate strategies (before fix) ---" >>"$LOG"
  [[ "$DRC" == "fail" ]] && python3 "$SKILL/scripts/reports/diagnose_signoff_fix.py" "$DEST" --check drc --list >>"$LOG" 2>&1 || true
  [[ "$LVS" == "fail" ]] && python3 "$SKILL/scripts/reports/diagnose_signoff_fix.py" "$DEST" --check lvs --list >>"$LOG" 2>&1 || true
  echo "--- fix_signoff ---" >>"$LOG"
  bash "$SKILL/scripts/flow/fix_signoff.sh" "$DEST" sky130hd --check both >>"$LOG" 2>&1 || true
  python3 "$SKILL/scripts/extract/extract_drc.py" "$DEST" "$DEST/reports/drc.json" >>"$LOG" 2>&1 || true
  python3 "$SKILL/scripts/extract/extract_lvs.py" "$DEST" "$DEST/reports/lvs.json" >>"$LOG" 2>&1 || true
  DRC=$(jval "$DEST/reports/drc.json" status)
  LVS=$(jval "$DEST/reports/lvs.json" status)
fi

DRCV=$(jval "$DEST/reports/drc.json" total_violations); DRCV="${DRCV:-}"
LVSC=$(jval "$DEST/reports/lvs.json" mismatch_class); LVSC="${LVSC:-none}"
RCX=$(jval "$DEST/reports/rcx.json" status)
# strategies actually attempted (from fix_log.jsonl)
FIXES=$(python3 - "$DEST/reports/fix_log.jsonl" <<'PY'
import json,sys,os
p=sys.argv[1]; s=[]
if os.path.isfile(p):
    for ln in open(p):
        try: r=json.loads(ln); s.append(r.get("strategy",""))
        except Exception: pass
print(",".join(sorted({x for x in s if x})))
PY
)

# 7) verdict --------------------------------------------------------------------
drc_ok=false; lvs_ok=false
[[ "$DRC" == "clean" || "$DRC" == "clean_beol" ]] && drc_ok=true
[[ "$LVS" == "clean" ]] && lvs_ok=true
[[ "$LVS" == "fail" && "$LVSC" == "symmetric_matcher" ]] && lvs_ok=true
PASS=false; RES=""
if [[ "$drc_ok" == true && "$lvs_ok" == true ]]; then
  PASS=true
else
  [[ "$drc_ok" != true ]] && RES="drc_${DRC:-none}"
  [[ "$lvs_ok" != true ]] && RES="${RES:+$RES,}lvs_${LVS:-none}"
fi

# 8) ingest into knowledge store (trajectories/actions already in fix_log) ------
python3 "$SKILL/knowledge/ingest_run.py" "$DEST" >>"$LOG" 2>&1 || echo "ingest warn" >>"$LOG"

emit signoff partial "$TIER" "${DRC:-none}" "${DRCV:-}" "${LVS:-none}" "$LVSC" "${RCX:-none}" "$PASS" "${RES:-}" "$FIXES" "lvs_tool=${LVS_TOOL:-klayout}"
exit 0
