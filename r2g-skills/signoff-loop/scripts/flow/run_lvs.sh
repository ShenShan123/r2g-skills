#!/usr/bin/env bash
set -euo pipefail

# usage: run_lvs.sh <project-dir> [platform] [flow_variant]
# Runs KLayout LVS on a completed ORFS backend run.
# Compares GDS layout against the CDL netlist.
# Results are collected into <project-dir>/lvs/

PROJECT_DIR="${1:-}"
PLATFORM="${2:-asap7}"
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
# Bounded process-group checker supervisor (RMD2-P0-01)
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_bounded_run.sh"
# Cancellation must never orphan the checker: reap the whole checker session on
# any exit path (same contract as run_drc.sh).
trap 'r2g_bounded_cleanup' EXIT
trap 'r2g_bounded_cleanup; exit 130' INT
trap 'r2g_bounded_cleanup; exit 143' TERM

if [[ -z "${ORFS_ROOT:-}" || ! -d "$FLOW_DIR" ]]; then
  echo "ERROR: ORFS not found. Set ORFS_ROOT to your OpenROAD-flow-scripts checkout." >&2
  exit 1
fi

if [[ -z "$PROJECT_DIR" ]]; then
  echo "usage: run_lvs.sh <project-dir> [platform]" >&2
  exit 1
fi

PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
KNOWLEDGE_DIR_J="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../knowledge" && pwd)"
CONFIG_MK="$PROJECT_DIR/constraints/config.mk"

if [[ ! -f "$CONFIG_MK" ]]; then
  echo "ERROR: config.mk not found at $CONFIG_MK" >&2
  exit 1
fi

DESIGN_NAME=$(grep 'DESIGN_NAME' "$CONFIG_MK" | head -1 | sed 's/.*=\s*//' | tr -d ' ')

# Re-stage project artifacts into ORFS workspace if missing. This makes the
# script idempotent across re-runs even if the ORFS scratch dirs were cleaned.
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_restage_for_signoff.sh"
RESULTS_DIR="$ORFS_RESULTS_DIR"

GDS_FILE="$RESULTS_DIR/6_final.gds"
if [[ ! -f "$GDS_FILE" ]]; then
  echo "ERROR: No 6_final.gds found at $GDS_FILE after restage" >&2
  echo "Re-run the ORFS backend first: run_orfs.sh <project-dir>" >&2
  exit 1
fi

# Check if LVS rule file exists for this platform.
# `|| true`: on a platform whose config.mk has NO KLAYOUT_LVS_FILE (e.g. asap7), grep exits
# 1; under `set -euo pipefail` (line 2) the failed pipeline would ABORT run_lvs.sh BEFORE the
# graceful no-deck skip path below (line ~81) -- so asap7 LVS never wrote
# lvs/lvs_result.json=skipped, and _ensure_baseline (which swallows the abort with its own
# `|| true`) then let extract_lvs parse a STALE prior-platform 6_lvs.lvsdb into reports/lvs.json
# as a false 'clean' (the LVS leg of the 2026-06-30 fabricated-clean bug, surfaced once the
# stale-report gate fix routed asap7 through run_lvs.sh). Tolerate the no-match so the skip
# path is reachable. See references/failure-patterns.md "Stale prior-platform signoff report".
PLATFORM_DIR="$FLOW_DIR/platforms/$PLATFORM"
KLAYOUT_LVS_FILE=$(grep 'KLAYOUT_LVS_FILE' "$PLATFORM_DIR/config.mk" 2>/dev/null | head -1 | sed 's/.*=\s*//' | sed "s|\$(PLATFORM_DIR)|$PLATFORM_DIR|g" | tr -d ' ' || true)

# Resolve the actual file path
KLAYOUT_LVS_RESOLVED=""

# Try the path parsed from config.mk
if [[ -n "$KLAYOUT_LVS_FILE" && -f "$KLAYOUT_LVS_FILE" ]]; then
  KLAYOUT_LVS_RESOLVED="$KLAYOUT_LVS_FILE"
fi

# Fallback: scan common locations (even if config.mk had no KLAYOUT_LVS_FILE entry)
if [[ -z "$KLAYOUT_LVS_RESOLVED" ]]; then
  for candidate in \
    "$PLATFORM_DIR/lvs/"*.lylvs \
    "$PLATFORM_DIR/"*.lylvs; do
    if [[ -f "$candidate" ]]; then
      KLAYOUT_LVS_RESOLVED="$candidate"
      break
    fi
  done
fi

if [[ -z "$KLAYOUT_LVS_RESOLVED" ]]; then
  echo "WARNING: No KLayout LVS rule file found for platform $PLATFORM" >&2
  echo "LVS is not supported on this platform." >&2
  # Create a stub result
  LVS_DIR="$PROJECT_DIR/lvs"
  mkdir -p "$LVS_DIR"
  echo '{"status": "skipped", "reason": "No LVS rules available for platform '"$PLATFORM"'"}' > "$LVS_DIR/lvs_result.json"
  echo "LVS skipped: no rules for $PLATFORM"
  echo "Results: $LVS_DIR"
  exit 0
fi

echo "Running LVS for design: $DESIGN_NAME"
echo "Platform: $PLATFORM"
echo "GDS: $GDS_FILE"
echo "LVS rules: $KLAYOUT_LVS_RESOLVED"

# ORFS_DESIGN_DIR / ORFS_CONFIG were set up by _restage_for_signoff.sh above.
ORFS_CONFIG="$ORFS_DESIGN_DIR/config.mk"
if [[ ! -f "$ORFS_CONFIG" ]]; then
  echo "ERROR: failed to stage ORFS config at $ORFS_CONFIG" >&2
  exit 1
fi

cd "$FLOW_DIR"

# Prevent env collision: ORFS Makefile uses SCRIPTS_DIR internally
unset SCRIPTS_DIR 2>/dev/null || true

# Detect design cell count from 6_report.json — drives BOTH timeout auto-scaling
# and crash-retry gating, so compute it unconditionally (init to 0 for set -u safety).
_LVS_LOGS_DIR="$FLOW_DIR/logs/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
if [[ ! -d "$_LVS_LOGS_DIR" ]]; then
  _LVS_LOGS_DIR="$FLOW_DIR/logs/$PLATFORM/$DESIGN_NAME"
fi
_REPORT_JSON=$(find "$_LVS_LOGS_DIR" -name "6_report.json" 2>/dev/null | head -1)
# Fallback: check the project backend directory
if [[ -z "$_REPORT_JSON" ]]; then
  _REPORT_JSON=$(find "$PROJECT_DIR/backend" -name "6_report.json" 2>/dev/null | sort | tail -1)
fi
_CELL_COUNT=0
if [[ -n "$_REPORT_JSON" ]]; then
  _CELL_COUNT=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(int(d.get('finish__design__instance__count',0)))" "$_REPORT_JSON" 2>/dev/null || echo 0)
fi

# Auto-scale timeout unless the user explicitly set LVS_TIMEOUT. KLayout's layout
# netlist EXTRACTION (not just the compare) is super-linear: ~2700s @51K and
# ~10200s @62K cells (verified 2026-06-03), so the old 3600s default SIGTERM'd every
# >=50K design *mid-extraction* (mis-reported as "incomplete"). Tiers now clear the
# extraction wall. See references/failure-patterns.md "LVS incomplete is mostly a
# comparer bug, not honest slowness".
if [[ -z "${LVS_TIMEOUT:-}" ]]; then
  if   [[ "$_CELL_COUNT" -gt 250000 ]] 2>/dev/null; then LVS_TIMEOUT=28800
  elif [[ "$_CELL_COUNT" -gt 100000 ]] 2>/dev/null; then LVS_TIMEOUT=21600
  elif [[ "$_CELL_COUNT" -gt  50000 ]] 2>/dev/null; then LVS_TIMEOUT=14400
  else                                                   LVS_TIMEOUT=5400
  fi
  echo "Auto-scaled LVS timeout to ${LVS_TIMEOUT}s (cell count: $_CELL_COUNT)"
fi
echo "Timeout: ${LVS_TIMEOUT}s"

# Where ORFS writes 6_lvs.log — needed below for crash-retry detection AND for the
# result-collection copy further down. Computed once, here, before the run loop.
LOGS_DIR="$FLOW_DIR/logs/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
if [[ ! -d "$LOGS_DIR" ]]; then
  LOGS_DIR="$FLOW_DIR/logs/$PLATFORM/$DESIGN_NAME"
fi

# KLayout 0.30.7's netlist comparer has a NON-DETERMINISTIC SIGSEGV heisenbug in
# db::NetlistCrossReference::sort_circuit()/gen_log_entry() (it reads a corrupted Net
# pointer while sorting the cross-reference, AFTER extraction succeeds). A surviving
# run yields the TRUE verdict — clean OR fail — so retry past the crash. Validated
# 2026-06-03: fifo_basic/verilog_axi_axi_fifo_wr -> clean; aximwr2wbsp/core_usb_host_top
# -> (symmetric) fail. Single-thread, verbose(false), tcmalloc do NOT fix it; `flat`
# mode dodges the crash but yields garbage mismatches. See references/failure-patterns.md
# "LVS KLayout sort_circuit/gen_log_entry SIGSEGV (non-deterministic)".
# Gated low for very large designs — each retry re-runs `make lvs`, which re-extracts.
if [[ -z "${LVS_CRASH_RETRIES:-}" ]]; then
  if [[ "$_CELL_COUNT" -gt 150000 ]] 2>/dev/null; then LVS_CRASH_RETRIES=1; else LVS_CRASH_RETRIES=4; fi
fi

# sky130 CDL slash-fix (PD issue: sky130hd/sky130hs LVS pin-count mismatch).
# KLayout's CDL reader counts the ' / ' node/model separator in the platform CDL's
# *_macro_sparecell subckt instance lines as an extra pin -> "6 expected, got 7" and
# LVS aborts on EVERY sky130 design. Fix: feed `make lvs` a slash-normalized copy of
# the platform CDL (separator removed; netlist semantics unchanged). Validated against
# cordic (LVS clean). Command-line CDL_FILE= overrides the platform config.mk's plain
# `export CDL_FILE`; we SKIP injection when the project config.mk already defines its
# own (override) CDL_FILE, so an explicit operator choice always wins.
# See references/failure-patterns.md "sky130 LVS macro_sparecell slash pin-count".
_CDL_MAKE_ARGS=()
if [[ "$PLATFORM" == "sky130hd" || "$PLATFORM" == "sky130hs" ]]; then
  _PLAT_CDL="$FLOW_DIR/platforms/$PLATFORM/cdl/$PLATFORM.cdl"
  if grep -qE '^[[:space:]]*(override[[:space:]]+)?export[[:space:]]+CDL_FILE' "$CONFIG_MK"; then
    echo "sky130 LVS: project config.mk sets its own CDL_FILE -> not injecting slash-fix"
  elif [[ -f "$_PLAT_CDL" ]]; then
    _CDL_FIX="$PROJECT_DIR/lvs/${PLATFORM}_cdl_fix.cdl"
    mkdir -p "$PROJECT_DIR/lvs"
    # Two KLayout CDL-reader fixes for the sky130 platform netlist:
    #  1) ' / ' node/model separator in *_macro_sparecell -> drop (pin-count "6->7").
    #  2) zero-ohm 'short' resistors in tie/power cells (conb_1 etc.) -> '0'. KLayout's
    #     default SPICE reader rejects the non-numeric 'short' value ("Can't find a value
    #     for a R, C or L device"); a numeric 0 parses and KLayout's device simplification
    #     reduces a 0-ohm resistor to a net short (the layout rule extracts no resistors).
    sed -E -e 's| / | |g' -e 's|^([rclRCL][A-Za-z0-9_]* [^ ]+ [^ ]+) short$|\1 0|' \
        "$_PLAT_CDL" > "$_CDL_FIX"
    _CDL_MAKE_ARGS=("CDL_FILE=$_CDL_FIX")
    echo "sky130 LVS: injected fixed CDL (slash + short-resistor) -> $_CDL_FIX"
  fi
  # Use the r2g-corrected LVS rule (adds a SPICE-reader delegate that shorts the
  # now-0-ohm tie/power resistors so they are not unmatched schematic-only devices;
  # the stock rule extracts MOS only). Override KLAYOUT_LVS_FILE on the make line.
  _R2G_LVS_RULE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/assets/platforms/$PLATFORM/lvs/${PLATFORM}_r2g.lylvs"
  if [[ -f "$_R2G_LVS_RULE" ]]; then
    _CDL_MAKE_ARGS+=("KLAYOUT_LVS_FILE=$_R2G_LVS_RULE")
    echo "sky130 LVS: using r2g-corrected rule -> $_R2G_LVS_RULE"
  fi
fi

# ── Frozen-layout preflight (RMD-P0-01, three-platform pilot 2026-07-22).
# `make lvs` is a Make-based path: its dependency cascade reaches back through
# finish→route→…→synthesis, so a stale-looking chain silently REBUILDS the
# physical implementation before the checker — the verdict then grades a
# foreign layout. `make --question` builds nothing (rc 0 = everything up to
# date, rc 1 = a rebuild would run, rc 2 = evaluation error): fail CLOSED with
# physical_rebuild_required instead of silently regenerating the layout.
_PREFLIGHT_TARGETS=("$RESULTS_DIR/5_route.odb" "$RESULTS_DIR/6_final.def"
                    "$RESULTS_DIR/6_final.v" "$RESULTS_DIR/6_final.sdc")
set +e
make --question DESIGN_CONFIG="$ORFS_CONFIG" FLOW_VARIANT="$FLOW_VARIANT" \
  "${_CDL_MAKE_ARGS[@]}" "${_PREFLIGHT_TARGETS[@]}" >/dev/null 2>&1
_PREFLIGHT_RC=$?
set -e
if [[ $_PREFLIGHT_RC -ne 0 ]]; then
  echo "ERROR: 'make lvs' would rebuild physical stages (make --question rc=$_PREFLIGHT_RC)" >&2
  echo "  A signoff checker must never regenerate the layout it grades (RMD-P0-01)." >&2
  echo "  Restage the preserved backend (or re-run run_orfs.sh) and retry." >&2
  LVS_DIR="$PROJECT_DIR/lvs"
  mkdir -p "$LVS_DIR"
  printf '{"status": "failed", "reason": "physical_rebuild_required", "make_question_rc": %s}\n' \
    "$_PREFLIGHT_RC" > "$LVS_DIR/lvs_result.json"
  exit 1
fi

# Expanded frozen-artifact digest set (RMD-P0-01): every physical artifact must
# be byte-identical before and after the checker run.
_r2g_lvs_digest_set() {
  local f
  for f in 5_route.odb 6_final.def 6_final.odb 6_final.gds 6_final.v 6_final.sdc 6_final.spef; do
    if [[ -f "$RESULTS_DIR/$f" ]]; then
      sha256sum "$RESULTS_DIR/$f" 2>/dev/null || true
    fi
  done
}
LVS_GDS_SHA_PRE="$(sha256sum "$GDS_FILE" 2>/dev/null | cut -d' ' -f1 || true)"
LVS_DIGEST_SET_PRE="$(_r2g_lvs_digest_set)"
LVS_STARTED_AT="$(date -Iseconds)"

# RMD2-P0-01 (2026-07-24, extended from run_drc.sh): the old
# `setsid timeout … make lvs | tee` had BOTH known liveness defects — `setsid`
# made timeout a group leader and silently disabled its tree-kill (#40), and
# `tee` held the output pipe open so a surviving deep klayout grandchild hung
# this script forever. r2g_bounded_run runs `make lvs` in its own session,
# writes output DIRECTLY to the run log, TERM→grace→KILLs the WHOLE group on
# expiry, and — crucially for the 2026-06-03 SIGSEGV leak, where make died on
# "Error 11" while a multi-GB klayout child kept spinning — reaps ANY session
# survivor before returning, replacing the fragile pattern-scoped pkill reaper
# (2026-07-04 audit H2 documented those patterns misfiring).
LVS_RUN_LOG="/tmp/lvs_run_$$.log"
LVS_STATUS=0
for _attempt in $(seq 1 "$LVS_CRASH_RETRIES"); do
  set +e +o pipefail
  r2g_bounded_run "$LVS_TIMEOUT" "${LVS_KILL_GRACE:-60}" "$LVS_RUN_LOG" \
    make DESIGN_CONFIG="$ORFS_CONFIG" FLOW_VARIANT="$FLOW_VARIANT" "${_CDL_MAKE_ARGS[@]}" lvs
  LVS_STATUS=$?
  set -e -o pipefail
  # Output goes straight to the log (no pipe reader can outlive the checker) —
  # surface the tail so operators still see this attempt's outcome.
  tail -n 25 "$LVS_RUN_LOG" 2>/dev/null || true

  # A timeout/external-kill will just recur — do not spend retries on it.
  if [[ $LVS_STATUS -eq 124 || $LVS_STATUS -eq 137 ]]; then
    echo "ERROR: LVS timed out after ${LVS_TIMEOUT}s (exit code $LVS_STATUS)" >&2
    break
  fi

  # Crash signature in the ORFS 6_lvs.log or the run log -> retry for a survivor.
  # Besides the sort_circuit SIGSEGV heisenbug ("Signal number"), also retry the KLayout
  # INTERNAL lvsdb-writer crash that fires AFTER a successful compare ('net2id.end ()' /
  # 'Internal error ... Executable::cleanup'); it emits a spurious "Netlists don't match"
  # and was misread as a hard lvs=fail on an actually-matching design (2026-06-28 PicoRV32).
  if grep -qaE "Signal number|net2id\.end|dbLayoutVsSchematicWriter|Internal error.*Executable::cleanup" \
        "$LOGS_DIR/6_lvs.log" "$LVS_RUN_LOG" 2>/dev/null; then
    if [[ $_attempt -lt $LVS_CRASH_RETRIES ]]; then
      echo "LVS crashed (KLayout comparer/lvsdb-writer heisenbug); retry $_attempt/$LVS_CRASH_RETRIES ..." >&2
      continue
    fi
    echo "ERROR: LVS still crashing after $LVS_CRASH_RETRIES attempts (KLayout 0.30.7 comparer bug, no newer build on host)" >&2
    break
  fi

  # No crash this attempt -> the verdict (clean or fail) is trustworthy. Stop.
  break
done

# Frozen-layout postcondition (RMD-P0-01): the preflight said nothing would
# rebuild — verify nothing DID. A changed digest set means the verdict grades
# foreign bytes; force a failed result (extract_lvs.py honors lvs_result.json).
LVS_DIGEST_SET_POST="$(_r2g_lvs_digest_set)"
LVS_IMPLICIT_REBUILD=0
if [[ "$LVS_DIGEST_SET_PRE" != "$LVS_DIGEST_SET_POST" ]]; then
  LVS_IMPLICIT_REBUILD=1
  echo "ERROR: 'make lvs' changed physical artifacts — the layout is not frozen (RMD-P0-01)" >&2
  diff <(echo "$LVS_DIGEST_SET_PRE") <(echo "$LVS_DIGEST_SET_POST") >&2 || true
fi

# Collect results
LVS_DIR="$PROJECT_DIR/lvs"
mkdir -p "$LVS_DIR"
cp "$LVS_RUN_LOG" "$LVS_DIR/lvs_run.log" 2>/dev/null || true
rm -f "$LVS_RUN_LOG"
# Drop any stale skip-marker from a prior `no rules available` run — once we
# have a real lvs_run.log/6_lvs.log the skip marker is no longer authoritative.
rm -f "$LVS_DIR/lvs_result.json"
if [[ "$LVS_IMPLICIT_REBUILD" == "1" ]]; then
  printf '{"status": "failed", "reason": "layout_changed_under_signoff", "note": "physical artifacts changed while make lvs ran; verdict does not describe the frozen backend layout (RMD-P0-01)"}\n' \
    > "$LVS_DIR/lvs_result.json"
  if [[ $LVS_STATUS -eq 0 ]]; then LVS_STATUS=1; fi
fi

# Strong provenance sidecar (RMD-P0-02): which run + exact layout bytes this
# LVS graded. extract_lvs.py lifts these into reports/lvs.json; the def-graph
# gate matches the digest against the layout it publishes. Kept SEPARATE from
# lvs_result.json (that file is the skip/failure marker in extract_lvs.py's
# freshness logic).
python3 - "$LVS_DIR/lvs_provenance.json" "${R2G_BACKEND_RUN:-}" "$GDS_FILE" \
  "$LVS_GDS_SHA_PRE" "$KLAYOUT_LVS_RESOLVED" "$LVS_STARTED_AT" <<'PYEOF' || true
import hashlib, json, os, sys, time
out, run_dir, gds, gds_sha, rule, started = sys.argv[1:7]
deck_sha = None
try:
    h = hashlib.sha256()
    with open(rule, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    deck_sha = h.hexdigest()
except OSError:
    pass
doc = {"engine": "klayout", "run_tag": os.path.basename(run_dir) if run_dir else None,
       "gds_path": gds, "gds_sha256": gds_sha or None,
       "rule_path": rule, "rule_sha256": deck_sha,
       "started_at": started, "ended_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
with open(out, "w") as f:
    json.dump(doc, f, indent=2)
    f.write("\n")
PYEOF

# LOGS_DIR was computed before the run loop (used for crash-retry detection).

# Copy LVS artifacts
if [[ -f "$RESULTS_DIR/6_lvs.lvsdb" ]]; then
  cp "$RESULTS_DIR/6_lvs.lvsdb" "$LVS_DIR/" 2>/dev/null || true
fi
if [[ -f "$LOGS_DIR/6_lvs.log" ]]; then
  cp "$LOGS_DIR/6_lvs.log" "$LVS_DIR/" 2>/dev/null || true
fi
if [[ -f "$RESULTS_DIR/6_final.cdl" ]]; then
  cp "$RESULTS_DIR/6_final.cdl" "$LVS_DIR/" 2>/dev/null || true
fi

# Copy to the SELECTED backend run (RMD-P0-02: the run the restage picked and
# this verdict describes — never `ls | tail -1`, which can name a newer empty
# RUN dir from a crashed re-attempt).
TARGET_RUN="${R2G_BACKEND_RUN:-}"
if [[ -z "$TARGET_RUN" || ! -d "$TARGET_RUN" ]]; then
  TARGET_RUN=$(ls -d "$PROJECT_DIR/backend"/RUN_* 2>/dev/null | sort | tail -1 || true)
fi
if [[ -n "$TARGET_RUN" && -d "$TARGET_RUN" ]]; then
  mkdir -p "$TARGET_RUN/lvs"
  cp "$LVS_DIR"/* "$TARGET_RUN/lvs/" 2>/dev/null || true
fi

# Parse LVS result
if [[ -f "$LVS_DIR/6_lvs.log" ]]; then
  # Check for failure first (more specific patterns take priority)
  if grep -qi "don't match\|do not match\|mismatch\|ERROR.*Netlists\|NOT match" "$LVS_DIR/6_lvs.log" 2>/dev/null; then
    echo ""
    echo "LVS FAILED — netlist mismatch detected"
    echo "Review $LVS_DIR/6_lvs.lvsdb for details"
  elif grep -qi "Congratulations.*match\|Netlists match\|circuits match" "$LVS_DIR/6_lvs.log" 2>/dev/null; then
    echo ""
    echo "LVS CLEAN — netlists match"
  else
    echo ""
    echo "LVS completed — check logs for detailed results"
  fi
else
  echo ""
  echo "LVS completed but no log found"
fi

echo "Results: $LVS_DIR"

# Tier-0 journal: digest this check's tool log + extracted report (never breaks the flow).
[[ -f "$LVS_DIR/lvs_run.log" ]] && python3 "$KNOWLEDGE_DIR_J/journal_db.py" summarize \
  --project "$PROJECT_DIR" --stage lvs --tool klayout --log "$LVS_DIR/lvs_run.log" \
  ${R2G_JOURNAL_DB:+--db "$R2G_JOURNAL_DB"} 2>/dev/null || true
[[ -f "$PROJECT_DIR/reports/lvs.json" ]] && python3 "$KNOWLEDGE_DIR_J/journal_db.py" report \
  --project "$PROJECT_DIR" --kind lvs --file "$PROJECT_DIR/reports/lvs.json" \
  ${R2G_JOURNAL_DB:+--db "$R2G_JOURNAL_DB"} 2>/dev/null || true

exit $LVS_STATUS
