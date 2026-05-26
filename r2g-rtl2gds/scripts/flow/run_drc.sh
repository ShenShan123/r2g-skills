#!/usr/bin/env bash
set -euo pipefail

# usage: run_drc.sh <project-dir> [platform] [flow_variant]
# Runs KLayout DRC on a completed ORFS backend run.
# Expects a successful backend run with GDS output.
# Results are collected into <project-dir>/drc/

PROJECT_DIR="${1:-}"
PLATFORM="${2:-nangate45}"
# Derive FLOW_VARIANT from project directory basename (matching run_orfs.sh logic)
if [[ -n "${3:-}" ]]; then
  FLOW_VARIANT="$3"
elif [[ -n "$PROJECT_DIR" && -d "$PROJECT_DIR" ]]; then
  FLOW_VARIANT="$(basename "$(cd "$PROJECT_DIR" && pwd)")"
else
  FLOW_VARIANT="base"
fi
# Auto-detect ORFS + tools (honors ORFS_ROOT / *_EXE env overrides)
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_env.sh"

if [[ -z "${ORFS_ROOT:-}" || ! -d "$FLOW_DIR" ]]; then
  echo "ERROR: ORFS not found. Set ORFS_ROOT to your OpenROAD-flow-scripts checkout." >&2
  exit 1
fi

if [[ -z "$PROJECT_DIR" ]]; then
  echo "usage: run_drc.sh <project-dir> [platform]" >&2
  exit 1
fi

PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
CONFIG_MK="$PROJECT_DIR/constraints/config.mk"

if [[ ! -f "$CONFIG_MK" ]]; then
  echo "ERROR: config.mk not found at $CONFIG_MK" >&2
  exit 1
fi

DESIGN_NAME=$(grep 'DESIGN_NAME' "$CONFIG_MK" | head -1 | sed 's/.*=\s*//' | tr -d ' ')

# Re-stage project artifacts into ORFS workspace if missing. This makes the
# script idempotent across re-runs: even if the ORFS scratch dirs were cleaned
# we recover from <project>/backend/RUN_*/final/*.
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_restage_for_signoff.sh"

ORFS_CONFIG="$ORFS_DESIGN_DIR/config.mk"
if [[ ! -f "$ORFS_CONFIG" ]]; then
  echo "ERROR: failed to stage ORFS config at $ORFS_CONFIG" >&2
  exit 1
fi

# Verify GDS exists from a prior ORFS run
GDS_FILE="$ORFS_RESULTS_DIR/6_final.gds"
if [[ ! -f "$GDS_FILE" ]]; then
  echo "ERROR: No 6_final.gds found at $GDS_FILE after restage" >&2
  echo "Re-run the ORFS backend first: run_orfs.sh <project-dir>" >&2
  exit 1
fi

echo "Running DRC for design: $DESIGN_NAME (variant: $FLOW_VARIANT)"
echo "Platform: $PLATFORM"
echo "GDS: $GDS_FILE"

cd "$FLOW_DIR"

# Prevent env collision: ORFS Makefile uses SCRIPTS_DIR internally
unset SCRIPTS_DIR 2>/dev/null || true

DRC_TIMEOUT="${DRC_TIMEOUT:-7200}"
echo "Timeout: ${DRC_TIMEOUT}s"

DRC_STATUS=0
set +e +o pipefail
setsid timeout --signal=TERM --kill-after=60 "$DRC_TIMEOUT" \
  make DESIGN_CONFIG="$ORFS_CONFIG" FLOW_VARIANT="$FLOW_VARIANT" drc 2>&1 | tee /tmp/drc_run_$$.log
DRC_STATUS=${PIPESTATUS[0]}
set -e -o pipefail
if [[ $DRC_STATUS -eq 124 ]]; then
  echo "ERROR: DRC timed out after ${DRC_TIMEOUT}s" >&2
fi

# Collect results
DRC_DIR="$PROJECT_DIR/drc"
mkdir -p "$DRC_DIR"
cp /tmp/drc_run_$$.log "$DRC_DIR/drc_run.log" 2>/dev/null || true
rm -f /tmp/drc_run_$$.log

REPORTS_DIR="$FLOW_DIR/reports/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
if [[ ! -d "$REPORTS_DIR" ]]; then
  REPORTS_DIR="$FLOW_DIR/reports/$PLATFORM/$DESIGN_NAME"
fi

LOGS_DIR="$FLOW_DIR/logs/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
if [[ ! -d "$LOGS_DIR" ]]; then
  LOGS_DIR="$FLOW_DIR/logs/$PLATFORM/$DESIGN_NAME"
fi

# Copy DRC artifacts
if [[ -f "$REPORTS_DIR/6_drc.lyrdb" ]]; then
  cp "$REPORTS_DIR/6_drc.lyrdb" "$DRC_DIR/" 2>/dev/null || true
fi
if [[ -f "$REPORTS_DIR/6_drc_count.rpt" ]]; then
  cp "$REPORTS_DIR/6_drc_count.rpt" "$DRC_DIR/" 2>/dev/null || true
fi
if [[ -f "$LOGS_DIR/6_drc.log" ]]; then
  cp "$LOGS_DIR/6_drc.log" "$DRC_DIR/" 2>/dev/null || true
fi

# Also copy to latest backend run
BACKEND_DIR="$PROJECT_DIR/backend"
if [[ -d "$BACKEND_DIR" ]]; then
  LATEST_RUN=$(ls -d "$BACKEND_DIR"/RUN_* 2>/dev/null | sort | tail -1)
  if [[ -n "$LATEST_RUN" ]]; then
    mkdir -p "$LATEST_RUN/drc"
    cp "$DRC_DIR"/* "$LATEST_RUN/drc/" 2>/dev/null || true
  fi
fi

# Report results
if [[ -f "$DRC_DIR/6_drc_count.rpt" ]]; then
  COUNT=$(cat "$DRC_DIR/6_drc_count.rpt" 2>/dev/null | tr -d '[:space:]')
  echo ""
  echo "DRC completed: $COUNT violations found"
  if [[ "$COUNT" == "0" ]]; then
    echo "DRC CLEAN"
    printf '{"status": "clean", "violations": 0}\n' > "$DRC_DIR/drc_result.json"
  else
    echo "DRC FAILED — review $DRC_DIR/6_drc.lyrdb for details"
    printf '{"status": "violations", "violations": %s}\n' "${COUNT:-unknown}" > "$DRC_DIR/drc_result.json"
  fi
else
  echo ""
  # No count report → either timed out, crashed, or stuck on a polygon-op rule.
  # Detect the FreePDK45 stuck-on-`or` pattern documented in
  # references/failure-patterns.md ("KLayout DRC Stuck on `or`"). When that
  # happens KLayout pegs CPU on a single rule for hours without making
  # progress; rather than retrying with a longer timeout (zombies have run
  # 4+ days unproductively), record status=stuck so the dashboard surfaces
  # a yellow badge and downstream tooling can skip retry.
  STUCK_RULE=""
  KILLED_KEYWORD=0
  if [[ -f "$DRC_DIR/6_drc.log" ]]; then
    # Grab the last `*.lydrc:NN` reference, if any
    STUCK_RULE=$(grep -oE '[A-Za-z0-9_]+\.lydrc:[0-9]+' "$DRC_DIR/6_drc.log" 2>/dev/null | tail -1 || true)
  fi
  # The klayout.sh wrapper prints "Killed" when klayout receives SIGKILL from
  # any external source (cgroups OOM, session limit, manual pkill). When that
  # happens make exits 2 (target failed), not 124/137 — so we look for the
  # keyword in the combined run log too. Without this check the stuck pattern
  # gets misclassified as a generic "failed" and downstream tooling retries it.
  if [[ -f "$DRC_DIR/drc_run.log" ]]; then
    if grep -qE 'Killed[[:space:]]+\$KLAYOUT_CMD|Killed[[:space:]]+klayout|Error 137' "$DRC_DIR/drc_run.log" 2>/dev/null; then
      KILLED_KEYWORD=1
    fi
  fi
  REASON="no_count_report"
  STATUS="failed"
  if [[ $DRC_STATUS -eq 124 || $DRC_STATUS -eq 137 || $KILLED_KEYWORD -eq 1 ]]; then
    if [[ -n "$STUCK_RULE" ]]; then
      STATUS="stuck"
      REASON="klayout_polygon_op_no_progress"
      if [[ $DRC_STATUS -eq 124 || $DRC_STATUS -eq 137 ]]; then
        echo "DRC STUCK on $STUCK_RULE after ${DRC_TIMEOUT}s — see references/failure-patterns.md"
      else
        echo "DRC STUCK on $STUCK_RULE (klayout killed externally, exit=$DRC_STATUS) — see references/failure-patterns.md"
      fi
      # Best-effort cleanup of any orphaned klayout DRC procs from this run.
      pkill -9 -f "klayout.*${FLOW_VARIANT}.*6_drc" 2>/dev/null || true
    elif [[ $DRC_STATUS -eq 124 || $DRC_STATUS -eq 137 ]]; then
      STATUS="timeout"
      REASON="drc_timeout"
      echo "DRC timed out after ${DRC_TIMEOUT}s with no log progress recorded"
    else
      echo "DRC killed externally (exit=$DRC_STATUS) but no lydrc rule recorded"
    fi
  else
    echo "DRC completed but no count report found (exit=$DRC_STATUS)"
  fi
  python3 - "$DRC_DIR/drc_result.json" "$STATUS" "$REASON" "$STUCK_RULE" "$DRC_TIMEOUT" "$DRC_STATUS" <<'PYEOF'
import json, sys
out, status, reason, rule, timeout, exit_code = sys.argv[1:7]
result = {
    "status": status,
    "reason": reason,
    "timeout_s": int(timeout),
    "exit_code": int(exit_code),
}
if rule:
    result["stuck_at_rule"] = rule
with open(out, "w") as f:
    json.dump(result, f, indent=2)
    f.write("\n")
PYEOF
fi

# Mirror drc_result.json into the latest backend run, if present
if [[ -f "$DRC_DIR/drc_result.json" && -d "$BACKEND_DIR" ]]; then
  LATEST_RUN=$(ls -d "$BACKEND_DIR"/RUN_* 2>/dev/null | sort | tail -1)
  if [[ -n "$LATEST_RUN" ]]; then
    mkdir -p "$LATEST_RUN/drc"
    cp "$DRC_DIR/drc_result.json" "$LATEST_RUN/drc/" 2>/dev/null || true
  fi
fi

echo "Results: $DRC_DIR"
exit $DRC_STATUS
