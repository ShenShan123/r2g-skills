#!/usr/bin/env bash
# usage: run_signoff.sh <project-dir>
# Run DRC + LVS + RCX + extract on a single design.
# Skips stages whose reports/<x>.json already exists (idempotent).
# Emits one-line JSONL summary to stdout for batch aggregators.
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
SKILL_DIR="$REPO_ROOT/r2g-skills/signoff-loop"

PROJECT_DIR="${1:-}"
if [[ -z "$PROJECT_DIR" ]]; then
  echo "usage: run_signoff.sh <project-dir>" >&2
  exit 2
fi
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
NAME="$(basename "$PROJECT_DIR")"
LOG_DIR="$REPO_ROOT/design_cases/_batch/logs_signoff"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/${NAME}.log"

# Ensure ORFS env is sourced (idempotent).
if [[ -z "${ORFS_ROOT:-}" || -z "${FLOW_HOME:-}" ]]; then
  if [[ -f "/proj/workarea/user5/OpenROAD-flow-scripts/env.sh" ]]; then
    # shellcheck disable=SC1091
    source /proj/workarea/user5/OpenROAD-flow-scripts/env.sh >/dev/null 2>&1
  fi
fi

now() { date +%Y-%m-%dT%H:%M:%S%:z; }

START="$(date +%s)"
RDRC=skip
RLVS=skip
RRCX=skip

REPORTS="$PROJECT_DIR/reports"
mkdir -p "$REPORTS"

# --- DRC ---
DRC_NEEDED=1
if [[ -f "$REPORTS/drc.json" ]]; then
  STATUS=$(python3 -c "import json; d=json.load(open('$REPORTS/drc.json')); print(d.get('status','unknown'))" 2>/dev/null || echo unknown)
  if [[ "$STATUS" == "clean" || "$STATUS" == "violations" || "$STATUS" == "stuck" ]]; then
    DRC_NEEDED=0
    RDRC="cached:$STATUS"
  fi
fi
if [[ "$DRC_NEEDED" == "1" ]]; then
  echo "[$NAME] $(now) DRC begin" >> "$LOG"
  DRC_TIMEOUT="${DRC_TIMEOUT:-3600}" bash "$SKILL_DIR/scripts/flow/run_drc.sh" "$PROJECT_DIR" >>"$LOG" 2>&1
  DRC_EXIT=$?
  python3 "$SKILL_DIR/scripts/extract/extract_drc.py" "$PROJECT_DIR" "$REPORTS/drc.json" >>"$LOG" 2>&1 || true
  if [[ -f "$REPORTS/drc.json" ]]; then
    STATUS=$(python3 -c "import json; d=json.load(open('$REPORTS/drc.json')); print(d.get('status','unknown'))" 2>/dev/null || echo unknown)
    RDRC="$STATUS:$DRC_EXIT"
  else
    RDRC="missing:$DRC_EXIT"
  fi
  echo "[$NAME] $(now) DRC end status=$RDRC" >> "$LOG"
fi

# --- LVS ---
LVS_NEEDED=1
if [[ -f "$REPORTS/lvs.json" ]]; then
  STATUS=$(python3 -c "import json; d=json.load(open('$REPORTS/lvs.json')); print(d.get('status','unknown'))" 2>/dev/null || echo unknown)
  if [[ "$STATUS" != "unknown" ]]; then
    LVS_NEEDED=0
    RLVS="cached:$STATUS"
  fi
fi
if [[ "$LVS_NEEDED" == "1" ]]; then
  echo "[$NAME] $(now) LVS begin" >> "$LOG"
  bash "$SKILL_DIR/scripts/flow/run_lvs.sh" "$PROJECT_DIR" >>"$LOG" 2>&1
  LVS_EXIT=$?
  python3 "$SKILL_DIR/scripts/extract/extract_lvs.py" "$PROJECT_DIR" "$REPORTS/lvs.json" >>"$LOG" 2>&1 || true
  if [[ -f "$REPORTS/lvs.json" ]]; then
    STATUS=$(python3 -c "import json; d=json.load(open('$REPORTS/lvs.json')); print(d.get('status','unknown'))" 2>/dev/null || echo unknown)
    RLVS="$STATUS:$LVS_EXIT"
  else
    RLVS="missing:$LVS_EXIT"
  fi
  echo "[$NAME] $(now) LVS end status=$RLVS" >> "$LOG"
fi

# --- RCX ---
RCX_NEEDED=1
if [[ -f "$REPORTS/rcx.json" ]]; then
  STATUS=$(python3 -c "import json; d=json.load(open('$REPORTS/rcx.json')); print(d.get('status','unknown'))" 2>/dev/null || echo unknown)
  if [[ "$STATUS" == "complete" ]]; then
    RCX_NEEDED=0
    RRCX="cached:$STATUS"
  fi
fi
if [[ "$RCX_NEEDED" == "1" ]]; then
  echo "[$NAME] $(now) RCX begin" >> "$LOG"
  RCX_TIMEOUT="${RCX_TIMEOUT:-3600}" bash "$SKILL_DIR/scripts/flow/run_rcx.sh" "$PROJECT_DIR" >>"$LOG" 2>&1
  RCX_EXIT=$?
  python3 "$SKILL_DIR/scripts/extract/extract_rcx.py" "$PROJECT_DIR" "$REPORTS/rcx.json" >>"$LOG" 2>&1 || true
  if [[ -f "$REPORTS/rcx.json" ]]; then
    STATUS=$(python3 -c "import json; d=json.load(open('$REPORTS/rcx.json')); print(d.get('status','unknown'))" 2>/dev/null || echo unknown)
    RRCX="$STATUS:$RCX_EXIT"
  else
    RRCX="missing:$RCX_EXIT"
  fi
  echo "[$NAME] $(now) RCX end status=$RRCX" >> "$LOG"
fi

END="$(date +%s)"
ELAPSED=$((END - START))

# JSON one-liner to stdout (for jsonl aggregation)
python3 - "$NAME" "$RDRC" "$RLVS" "$RRCX" "$ELAPSED" <<'PYEOF'
import json, sys
name, drc, lvs, rcx, elapsed = sys.argv[1:6]
print(json.dumps({"case": name, "drc": drc, "lvs": lvs, "rcx": rcx, "elapsed_s": int(elapsed)}))
PYEOF
