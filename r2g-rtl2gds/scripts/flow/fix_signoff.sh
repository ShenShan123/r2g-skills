#!/usr/bin/env bash
set -euo pipefail
# usage: fix_signoff.sh <project-dir> [platform] [--check drc|lvs|both] [--max-iters N] [--resume]
#
# Iteratively applies REAL layout fixes for DRC/LVS violations: diagnose →
# apply (config.mk marked block) → re-run flow → re-check → compare, up to
# --max-iters, with early-exit when an iteration does not reduce the count.
# Real-fixes-only: never relaxes the DRC rule deck. See references/signoff-fixing.md.
#
# Progress is flushed to <project>/reports/fix_log.jsonl per iteration (long
# DRC/LVS runtimes mean we never batch logging to the end); a human-readable
# <project>/reports/fix_summary.md is written at the end.
#
# Command seams are overridable for testing:
#   R2G_RUN_ORFS R2G_RUN_DRC R2G_RUN_LVS R2G_EXTRACT_DRC R2G_EXTRACT_LVS R2G_DIAGNOSE

PROJECT_DIR=""; PLATFORM="nangate45"; CHECK="both"; MAX_ITERS=3; RESUME=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) CHECK="$2"; shift 2;;
    --max-iters) MAX_ITERS="$2"; shift 2;;
    --resume) RESUME=1; shift;;
    -*) echo "unknown flag: $1" >&2; exit 1;;
    *) if [[ -z "$PROJECT_DIR" ]]; then PROJECT_DIR="$1"; else PLATFORM="$1"; fi; shift;;
  esac
done
[[ -z "$PROJECT_DIR" ]] && { echo "usage: fix_signoff.sh <project-dir> [platform] [--check drc|lvs|both] [--max-iters N] [--resume]" >&2; exit 1; }
[[ "$CHECK" =~ ^(drc|lvs|both)$ ]] || { echo "ERROR: --check must be drc|lvs|both" >&2; exit 1; }
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXTRACT_DIR="$(cd "$SCRIPT_DIR/../extract" && pwd)"
REPORTS_DIR_SCRIPTS="$(cd "$SCRIPT_DIR/../reports" && pwd)"
RUN_ORFS="${R2G_RUN_ORFS:-$SCRIPT_DIR/run_orfs.sh}"
RUN_DRC="${R2G_RUN_DRC:-$SCRIPT_DIR/run_drc.sh}"
RUN_LVS="${R2G_RUN_LVS:-$SCRIPT_DIR/run_lvs.sh}"
EXTRACT_DRC="${R2G_EXTRACT_DRC:-$EXTRACT_DIR/extract_drc.py}"
EXTRACT_LVS="${R2G_EXTRACT_LVS:-$EXTRACT_DIR/extract_lvs.py}"
DIAGNOSE="${R2G_DIAGNOSE:-$REPORTS_DIR_SCRIPTS/diagnose_signoff_fix.py}"

REPORTS="$PROJECT_DIR/reports"
mkdir -p "$REPORTS"
LOG="$REPORTS/fix_log.jsonl"

_count() {  # read current violation count from a report json
  python3 -c 'import json,sys
d=json.load(open(sys.argv[1]))
v=d.get("total_violations"); v=d.get("mismatch_count") if v is None else v
print("" if v is None else v)' "$1" 2>/dev/null || echo ""
}

_run_extract() {  # $1 = drc|lvs
  local script
  if [[ "$1" == "drc" ]]; then script="$EXTRACT_DRC"; else script="$EXTRACT_LVS"; fi
  python3 "$script" "$PROJECT_DIR" "$REPORTS/$1.json"
}

_log_iter() {  # check iter strategy before after verdict
  python3 -c 'import json,sys
o=dict(check=sys.argv[1],iter=int(sys.argv[2]),strategy=sys.argv[3],
       before=(sys.argv[4] or None),after=(sys.argv[5] or None),
       verdict=sys.argv[6],ts=sys.argv[7])
open(sys.argv[8],"a").write(json.dumps(o)+"\n")' \
    "$1" "$2" "$3" "$4" "$5" "$6" "$(date -u +%FT%TZ)" "$LOG"
}

fix_one() {  # $1 = drc|lvs
  local check="$1" report="$REPORTS/$1.json" tried="" before after it sid rerun recheck line verdict
  [[ -f "$report" ]] || _run_extract "$check"
  before="$(_count "$report")"
  for ((it=1; it<=MAX_ITERS; it++)); do
    line="$(python3 "$DIAGNOSE" "$PROJECT_DIR" --check "$check" --exclude "$tried" --next)"
    # Split on tab WITHOUT collapsing empty middle fields. `read` with a
    # whitespace IFS (tab) would merge consecutive tabs, dropping an empty
    # rerun_from column and shifting recheck into rerun; map tabs to a
    # non-whitespace unit-separator first so empty fields are preserved.
    IFS=$'\x1f' read -r sid rerun recheck <<<"${line//$'\t'/$'\x1f'}"
    [[ -n "$sid" ]] || { echo "[$check] ERROR: diagnose returned empty output; aborting" >&2; return 1; }
    if [[ "$sid" == "STOP" ]]; then
      _log_iter "$check" "$it" "none" "$before" "$before" "stop_${rerun}"
      echo "[$check] stop: $rerun ${recheck:-}"; return 0
    fi
    echo "[$check] iter $it: applying $sid (rerun_from=${rerun:-none})"
    if ! python3 "$DIAGNOSE" "$PROJECT_DIR" --check "$check" --apply "$sid" >/dev/null; then
      echo "[$check] apply '$sid' failed; aborting" >&2
      _log_iter "$check" "$it" "$sid" "$before" "$before" "apply_failed"; return 1
    fi
    tried="${tried:+$tried,}$sid"
    if [[ -n "$rerun" ]]; then
      local rc=0
      if [[ "$RESUME" == "1" ]]; then FROM_STAGE="$rerun" "$RUN_ORFS" "$PROJECT_DIR" "$PLATFORM" || rc=$?
      else "$RUN_ORFS" "$PROJECT_DIR" "$PLATFORM" || rc=$?; fi
      if [[ $rc -ne 0 ]]; then
        echo "[$check] run_orfs failed (rc=$rc); aborting this check" >&2
        _log_iter "$check" "$it" "$sid" "$before" "$before" "rerun_failed_rc$rc"
        return 1
      fi
    fi
    if [[ "$check" == "drc" ]]; then "$RUN_DRC" "$PROJECT_DIR" "$PLATFORM" || true; else "$RUN_LVS" "$PROJECT_DIR" "$PLATFORM" || true; fi
    _run_extract "$check"
    after="$(_count "$report")"
    verdict="applied"
    if [[ -n "$before" && -n "$after" ]] && python3 -c "import sys;sys.exit(0 if float('$after')>=float('$before') else 1)" 2>/dev/null; then
      verdict="no_improvement"
    fi
    _log_iter "$check" "$it" "$sid" "$before" "$after" "$verdict"
    echo "[$check] iter $it: $before -> $after ($verdict)"
    if [[ "$after" == "0" ]]; then echo "[$check] CLEAN"; return 0; fi
    if [[ "$verdict" == "no_improvement" ]]; then echo "[$check] no improvement; trying next strategy"; fi
    before="$after"
  done
  echo "[$check] reached max-iters=$MAX_ITERS"
}

: > "$LOG"
[[ "$CHECK" == "drc" || "$CHECK" == "both" ]] && fix_one drc || true
[[ "$CHECK" == "lvs" || "$CHECK" == "both" ]] && fix_one lvs || true

# Markdown summary from the JSONL log
python3 -c 'import json,sys
log=sys.argv[1]; out=sys.argv[2]
rows=[json.loads(l) for l in open(log) if l.strip()]
lines=["# Signoff fix summary","","| check | iter | strategy | before | after | verdict |","|---|---|---|---|---|---|"]
def c(v): return "" if v is None else v
for r in rows:
    lines.append("| {} | {} | {} | {} | {} | {} |".format(
        c(r.get("check")), c(r.get("iter")), c(r.get("strategy")),
        c(r.get("before")), c(r.get("after")), c(r.get("verdict"))))
open(out,"w").write("\n".join(lines)+"\n")' "$LOG" "$REPORTS/fix_summary.md"
echo "Summary: $REPORTS/fix_summary.md"

# exit 0 if final state clean, else 2 if residual remains
python3 -c 'import json,sys,os
proj=sys.argv[1]; rc=0
for c in ("drc","lvs"):
    p=os.path.join(proj,"reports",c+".json")
    if os.path.exists(p):
        d=json.load(open(p))
        if d.get("status") in ("fail","failed","residual"): rc=2
sys.exit(rc)' "$PROJECT_DIR" || exit 2
