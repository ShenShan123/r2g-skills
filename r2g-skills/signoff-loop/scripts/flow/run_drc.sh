#!/usr/bin/env bash
set -euo pipefail

# usage: run_drc.sh <project-dir> [platform] [flow_variant]
# Runs KLayout DRC on a completed ORFS backend run.
# Expects a successful backend run with GDS output.
# Results are collected into <project-dir>/drc/

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

# DRC_BEOL_ONLY=1 → skip FEOL checks (std cells are pre-verified library; only
# BEOL metal/via/antenna routing varies per design).  See
# references/failure-patterns.md §"KLayout DRC Stuck on `or`".
#
# DRC_BEOL_STRICT=1 (deeper fallback, implies BEOL-only) → physically comment out
# EVERY check (`.output(`) inside the deck's `if FEOL … end # FEOL` block.  Empirically
# the FEOL=false toggle gates the Well/Poly/Active booleans but NOT the IMPLANT and
# CONTACT groups — those still execute in BEOL-only mode and HANG on large designs
# (≥~465K inst — eth_mac_1g_fifo, koios — froze at implant.width/cont.space over millions
# of MOL polygons).  All FEOL-block geometry is library-internal (P&R adds only
# metal/vias), so stripping the whole block body is as defensible as the FEOL toggle and
# guarantees none of it runs.  Leaves BEOL metal/via + OFFGRID (the real P&R geometry).
# (DRC_SKIP_CONTACT is a back-compat alias — CONTACT alone is insufficient; both map to
# the strict whole-FEOL-body strip.)
DRC_BEOL_ONLY="${DRC_BEOL_ONLY:-0}"
DRC_BEOL_STRICT="${DRC_BEOL_STRICT:-${DRC_SKIP_CONTACT:-0}}"
DRC_MODE="full"
if [[ "$DRC_BEOL_STRICT" == "1" ]]; then
  DRC_BEOL_ONLY=1                     # strict implies BEOL-only
  DRC_MODE="beol_only_strict"
elif [[ "$DRC_BEOL_ONLY" == "1" ]]; then
  DRC_MODE="beol_only"
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
KNOWLEDGE_DIR_J="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../knowledge" && pwd)"
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

# ── Resolve the platform DRC deck (RMD-P0-01: the checker-only path needs it
# explicitly — there is no `make drc` left to resolve it for us). Order:
# KLAYOUT_DRC_FILE from the platform config.mk, then platform drc/*.lydrc, then
# the DELIBERATE sky130hs<-sky130hd sibling borrow (failure-patterns #32: same
# sky130A tech, deck is pure process-layer geometry; never a generic borrow). ──
PLATFORM_DIR_DRC="$FLOW_DIR/platforms/$PLATFORM"
DRC_DECK=$(grep 'KLAYOUT_DRC_FILE' "$PLATFORM_DIR_DRC/config.mk" 2>/dev/null \
        | head -1 | sed 's/.*=\s*//' \
        | sed "s|\$(PLATFORM_DIR)|$PLATFORM_DIR_DRC|g" | tr -d ' ' || true)
if [[ -z "$DRC_DECK" || ! -f "$DRC_DECK" ]]; then
  DRC_DECK=$(ls "$PLATFORM_DIR_DRC/drc/"*.lydrc 2>/dev/null | head -1 || true)
fi
if [[ -z "$DRC_DECK" && "$PLATFORM" == "sky130hs" ]]; then
  _SIBLING_DECK="$FLOW_DIR/platforms/sky130hd/drc/sky130hd.lydrc"
  if [[ -f "$_SIBLING_DECK" ]]; then
    echo "NOTE: no ORFS DRC deck for $PLATFORM — using the sibling sky130A tech deck: $_SIBLING_DECK (failure-patterns #32)"
    DRC_DECK="$_SIBLING_DECK"
  fi
fi

DRC_DIR="$PROJECT_DIR/drc"
mkdir -p "$DRC_DIR"

if [[ -z "$DRC_DECK" || ! -f "$DRC_DECK" ]]; then
  # Honest explicit skip (never the old phantom fail, failure-patterns #32):
  # the gate treats a skipped DRC as NOT signed off, which is the truth here.
  echo "WARNING: no KLayout DRC deck found for platform $PLATFORM — DRC not supported" >&2
  printf '{"status": "skipped", "reason": "no_drc_deck_for_platform", "drc_mode": "%s"}\n' \
    "$DRC_MODE" > "$DRC_DIR/drc_result.json"
  echo "Results: $DRC_DIR"
  exit 0
fi

# ── BEOL-only mode: generate a FEOL=false copy of the resolved deck ──────────
if [[ "$DRC_BEOL_ONLY" == "1" ]]; then
  BEOL_DECK="$DRC_DIR/$(basename "$DRC_DECK" .lydrc).beol.lydrc"
  _deck="$DRC_DECK"
  # Disable BOTH FEOL and the ANTENNA group. The ANTENNA checks reference the
  # `gate` layer (`gate = poly & active`), which is DERIVED INSIDE the
  # `if FEOL ... end` block — so with FEOL=false the ANTENNA `connect` fails
  # with "First argument must be a layer" and make exits 1. Leave OFFGRID true
  # (it has no FEOL-derived dependency and completes fine).
  sed -E -e 's/^([[:space:]]*FEOL[[:space:]]*=[[:space:]]*)true/\1false/' \
         -e 's/^([[:space:]]*ANTENNA[[:space:]]*=[[:space:]]*)true/\1false/' "$_deck" > "$BEOL_DECK"
  # Verify BOTH toggles flipped (abort if either FEOL or ANTENNA didn't change).
  if ! grep -qE '^[[:space:]]*FEOL[[:space:]]*=[[:space:]]*false' "$BEOL_DECK"; then
    echo "ERROR: BEOL deck transform failed — 'FEOL = false' not found in $BEOL_DECK" >&2
    echo "Check that $PLATFORM deck has a top-level 'FEOL    = true' line." >&2
    rm -f "$BEOL_DECK"
    exit 1
  fi
  if ! grep -qE '^[[:space:]]*ANTENNA[[:space:]]*=[[:space:]]*false' "$BEOL_DECK"; then
    echo "ERROR: BEOL deck transform failed — 'ANTENNA = false' not found in $BEOL_DECK" >&2
    echo "ANTENNA must be disabled too: it depends on FEOL-derived layers." >&2
    echo "Check that $PLATFORM deck has a top-level 'ANTENNA = true' line." >&2
    rm -f "$BEOL_DECK"
    exit 1
  fi
  # Deeper fallback: physically comment EVERY check (`.output(`) between `if FEOL` and
  # `end # FEOL`.  The FEOL=false toggle does NOT reliably gate this block (the IMPLANT
  # and CONTACT groups execute in BEOL-only mode and hang large designs), so we strip the
  # whole body.  Layer-derivation lines (well=, gate=, implant=) carry no `.output(` and
  # are left intact (cheap, harmless); only the marker-emitting check statements — which
  # nothing downstream consumes — are commented.  awk tracks the block by its delimiters.
  if [[ "$DRC_BEOL_STRICT" == "1" ]]; then
    awk '
      /^[[:space:]]*if[[:space:]]+FEOL([^[:alnum:]_]|$)/ { infeol=1 }
      infeol && /^[[:space:]]*end[[:space:]]*#[[:space:]]*FEOL/ { infeol=0 }
      { if (infeol && $0 ~ /\.output\(/ && $0 !~ /^[[:space:]]*#/) print "# r2g-beol-strict: " $0; else print }
    ' "$BEOL_DECK" > "$BEOL_DECK.tmp" && mv "$BEOL_DECK.tmp" "$BEOL_DECK"
    # Guard: no uncommented `.output(` may remain inside the FEOL block.
    if awk '
        /^[[:space:]]*if[[:space:]]+FEOL([^[:alnum:]_]|$)/ { infeol=1 }
        infeol && /^[[:space:]]*end[[:space:]]*#[[:space:]]*FEOL/ { infeol=0 }
        infeol && /\.output\(/ && $0 !~ /^[[:space:]]*#/ { found=1 }
        END { exit(found?0:1) }
      ' "$BEOL_DECK"; then
      echo "ERROR: DRC_BEOL_STRICT=1 but uncommented checks remain inside the FEOL block of $BEOL_DECK" >&2
      rm -f "$BEOL_DECK"; exit 1
    fi
    echo "DRC BEOL-strict mode: entire FEOL block body (Well/Poly/Active/Implant/Contact) stripped + ANTENNA off (all library-internal); only BEOL metal/via + off-grid checks run. NOT full DRC-clean; deck=$BEOL_DECK"
  else
    echo "DRC BEOL-only mode: FEOL and ANTENNA checks skipped (ANTENNA depends on FEOL-derived layers); metal/via routing geometry + off-grid checks run. NOT full DRC-clean, antenna NOT verified; deck=$BEOL_DECK"
  fi
  DRC_DECK="$BEOL_DECK"
fi
# ──────────────────────────────────────────────────────────────────────────────

# HONESTY: purge any STALE local DRC artifacts before the run. If the fresh
# result-copy below is skipped — e.g. a pre-copytree-fix A/B arm dir
# that inherited a June-19 6_drc_count.rpt=0, or an interrupted run — an OLD
# count/lyrdb left in place would be misread by run_drc.sh's count logic AND by
# extract_drc.py as a fresh 0-violation clean, fabricating a clean signoff over a
# run that actually found violations. Clearing them first means a failed copy
# falls through to the "no count report" path -> honest stuck/error, never a
# stale-0 clean. (2026-06-30 asap7 arm fabricated-clean regression; the six
# asap7 arms recorded drc=clean while the real asap7.lydrc run found 25 viols.)
rm -f "$DRC_DIR/6_drc.lyrdb" "$DRC_DIR/6_drc_count.rpt" \
      "$DRC_DIR/6_drc.log" 2>/dev/null || true

DRC_TIMEOUT="${DRC_TIMEOUT:-7200}"
echo "Timeout: ${DRC_TIMEOUT}s"

# ── Frozen-layout, checker-only DRC (RMD-P0-01, three-platform pilot
# 2026-07-22). A DRC entry point must NEVER execute synthesis, floorplan,
# placement, CTS, routing, or finish. The old `make drc` followed ORFS's mtime
# dependency cascade back through the whole flow, and the restage timestamp
# policy handed it a stale-looking chain (clock_period.txt stamped newer than
# the restored Yosys outputs; numbered logs older than their stage results) —
# ALL 12 pilot DRC invocations rebuilt synth→finish before KLayout. KLayout
# needs only the GDS and the deck, so invoke ORFS scripts/klayout.sh DIRECTLY
# with absolute paths: physical-stage dependency evaluation cannot run at all.
# Plain `timeout`, never `setsid timeout` (failure-patterns #40: setsid made
# timeout a group leader and silently disabled its tree-kill).

# Select the frozen layout: the preserved backend run's GDS (the artifact this
# verdict will be attributed to), falling back to the restaged workspace copy.
DRC_GDS="$GDS_FILE"
DRC_RUN_TAG=""
RUN_DRC_DIR=""
if [[ -n "${R2G_BACKEND_RUN:-}" && -d "${R2G_BACKEND_RUN:-}" ]]; then
  DRC_RUN_TAG="$(basename "$R2G_BACKEND_RUN")"
  RUN_DRC_DIR="$R2G_BACKEND_RUN/drc"
  for _sub in results final; do
    if [[ -f "$R2G_BACKEND_RUN/$_sub/6_final.gds" ]]; then
      DRC_GDS="$R2G_BACKEND_RUN/$_sub/6_final.gds"
      break
    fi
  done
fi

# Expanded digest set (was: single GDS): every physical artifact of the frozen
# run must be byte-identical before and after the checker. The checker cannot
# rebuild anything, so a change means something ELSE mutated the run mid-check
# — the verdict then grades foreign bytes and is forced to failed.
DRC_ARTIFACT_DIR="$(dirname "$DRC_GDS")"
_r2g_digest_set() {
  local f
  for f in 5_route.odb 6_final.def 6_final.odb 6_final.gds 6_final.v 6_final.sdc 6_final.spef; do
    if [[ -f "$DRC_ARTIFACT_DIR/$f" ]]; then
      sha256sum "$DRC_ARTIFACT_DIR/$f" 2>/dev/null || true
    fi
  done
}
GDS_SHA_PRE="$(sha256sum "$DRC_GDS" 2>/dev/null | cut -d' ' -f1 || true)"
DIGEST_SET_PRE="$(_r2g_digest_set)"

if [[ -z "${KLAYOUT_CMD:-}" ]]; then
  echo "ERROR: KLAYOUT_CMD not resolved — install KLayout or export KLAYOUT_CMD (see eda-install)" >&2
  printf '{"status": "failed", "reason": "klayout_not_found", "drc_mode": "%s"}\n' \
    "$DRC_MODE" > "$DRC_DIR/drc_result.json"
  exit 1
fi
export KLAYOUT_CMD
KLAYOUT_VERSION="$("$KLAYOUT_CMD" -v 2>/dev/null | head -1 || true)"
DECK_SHA="$(sha256sum "$DRC_DECK" 2>/dev/null | cut -d' ' -f1 || true)"

# Write DRC output under the selected backend run FIRST, then mirror to the
# project-level drc/ dir (RMD-P0-02: the report is born run-local).
OUT_DIR="${RUN_DRC_DIR:-$DRC_DIR}"
mkdir -p "$OUT_DIR"
LYRDB="$OUT_DIR/6_drc.lyrdb"
DRC_LOG="$OUT_DIR/6_drc.log"
rm -f "$LYRDB" "$OUT_DIR/6_drc_count.rpt" 2>/dev/null || true

DRC_STARTED_AT="$(date -Iseconds)"
_KLAYOUT_WRAPPER="$FLOW_DIR/scripts/klayout.sh"
DRC_STATUS=0
set +e +o pipefail
if [[ -f "$_KLAYOUT_WRAPPER" ]]; then
  timeout --signal=TERM --kill-after=60 "$DRC_TIMEOUT" \
    bash "$_KLAYOUT_WRAPPER" -zz -rd in_gds="$DRC_GDS" \
    -rd report_file="$LYRDB" -r "$DRC_DECK" 2>&1 | tee "$DRC_LOG"
else
  timeout --signal=TERM --kill-after=60 "$DRC_TIMEOUT" \
    "$KLAYOUT_CMD" -zz -rd in_gds="$DRC_GDS" \
    -rd report_file="$LYRDB" -r "$DRC_DECK" 2>&1 | tee "$DRC_LOG"
fi
DRC_STATUS=${PIPESTATUS[0]}
set -e -o pipefail
DRC_ENDED_AT="$(date -Iseconds)"
if [[ $DRC_STATUS -eq 124 ]]; then
  echo "ERROR: DRC timed out after ${DRC_TIMEOUT}s" >&2
fi

# Violation count from the lyrdb (same marker count ORFS's drc target derived).
# Never derive a count from a timed-out/killed run — KLayout writes the report
# database at the END, but a kill racing that write could leave partial XML
# that would read as a too-low count (the no-count path classifies honestly).
if [[ -f "$LYRDB" && $DRC_STATUS -ne 124 && $DRC_STATUS -ne 137 ]]; then
  grep -c "<value>" "$LYRDB" > "$OUT_DIR/6_drc_count.rpt" 2>/dev/null || true
fi

GDS_SHA_POST="$(sha256sum "$DRC_GDS" 2>/dev/null | cut -d' ' -f1 || true)"
DIGEST_SET_POST="$(_r2g_digest_set)"
IMPLICIT_REBUILD=0
if [[ "$DIGEST_SET_PRE" != "$DIGEST_SET_POST" ]]; then
  IMPLICIT_REBUILD=1
  echo "ERROR: physical artifacts changed under signoff — the layout is not frozen" >&2
  diff <(echo "$DIGEST_SET_PRE") <(echo "$DIGEST_SET_POST") >&2 || true
  echo "  This DRC verdict grades foreign bytes, not the frozen backend run (RMD-P0-01)." >&2
fi

# Mirror the run-local DRC output to the project-level drc/ dir. drc_run.log is
# the combined run log consumed by extract_drc.py's staleness guard + journal.
cp "$DRC_LOG" "$DRC_DIR/drc_run.log" 2>/dev/null || true
if [[ "$OUT_DIR" != "$DRC_DIR" ]]; then
  cp "$DRC_LOG" "$DRC_DIR/6_drc.log" 2>/dev/null || true
  [[ -f "$LYRDB" ]] && cp "$LYRDB" "$DRC_DIR/" 2>/dev/null || true
  [[ -f "$OUT_DIR/6_drc_count.rpt" ]] && cp "$OUT_DIR/6_drc_count.rpt" "$DRC_DIR/" 2>/dev/null || true
else
  cp "$DRC_LOG" "$DRC_DIR/6_drc.log" 2>/dev/null || true
fi

# Report results
if [[ -f "$DRC_DIR/6_drc_count.rpt" ]]; then
  COUNT=$(cat "$DRC_DIR/6_drc_count.rpt" 2>/dev/null | tr -d '[:space:]')
  echo ""
  echo "DRC completed: $COUNT violations found"
  if [[ "$COUNT" == "0" ]]; then
    echo "DRC CLEAN"
    printf '{"status": "clean", "violations": 0, "drc_mode": "%s"}\n' "$DRC_MODE" > "$DRC_DIR/drc_result.json"
  else
    echo "DRC FAILED — review $DRC_DIR/6_drc.lyrdb for details"
    printf '{"status": "violations", "violations": %s, "drc_mode": "%s"}\n' "${COUNT:-unknown}" "$DRC_MODE" > "$DRC_DIR/drc_result.json"
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
  # If we saw a `*.lydrc:NN` reference, treat as stuck regardless of how the
  # process exited — observed exit codes for this pattern have included 124
  # (timeout), 137 (SIGKILL), 2 (make-target failed), and others when klayout
  # got SIGTERM'd or aborted mid-rule. The stuck_at_rule is the load-bearing
  # signal; exit code is unreliable across kill mechanisms.
  if [[ -n "$STUCK_RULE" ]]; then
    STATUS="stuck"
    REASON="klayout_polygon_op_no_progress"
    if [[ $DRC_STATUS -eq 124 || $DRC_STATUS -eq 137 ]]; then
      echo "DRC STUCK on $STUCK_RULE after ${DRC_TIMEOUT}s — see references/failure-patterns.md"
    elif [[ $KILLED_KEYWORD -eq 1 ]]; then
      echo "DRC STUCK on $STUCK_RULE (klayout killed externally, exit=$DRC_STATUS) — see references/failure-patterns.md"
    else
      echo "DRC STUCK on $STUCK_RULE (no count report, exit=$DRC_STATUS) — see references/failure-patterns.md"
    fi
    echo "HINT: retry with DRC_BEOL_ONLY=1 to skip the FEOL checks (standard cells are library-verified) — see references/failure-patterns.md"
    # Best-effort cleanup of any orphaned klayout DRC procs from this run.
    # Match variant+6_drc in EITHER order (the direct invocation puts the GDS
    # path — which carries the variant — before the 6_drc.lyrdb report arg).
    pkill -9 -f "klayout.*${FLOW_VARIANT}.*6_drc" 2>/dev/null || true
    pkill -9 -f "klayout.*6_drc.*${FLOW_VARIANT}" 2>/dev/null || true
  elif [[ $DRC_STATUS -eq 124 || $DRC_STATUS -eq 137 ]]; then
    STATUS="timeout"
    REASON="drc_timeout"
    echo "DRC timed out after ${DRC_TIMEOUT}s with no log progress recorded"
  elif [[ $KILLED_KEYWORD -eq 1 ]]; then
    echo "DRC killed externally (exit=$DRC_STATUS) but no lydrc rule recorded"
  else
    echo "DRC completed but no count report found (exit=$DRC_STATUS)"
  fi
  python3 - "$DRC_DIR/drc_result.json" "$STATUS" "$REASON" "$STUCK_RULE" "$DRC_TIMEOUT" "$DRC_STATUS" "$DRC_MODE" <<'PYEOF'
import json, sys
out, status, reason, rule, timeout, exit_code, drc_mode = sys.argv[1:8]
result = {
    "status": status,
    "reason": reason,
    "timeout_s": int(timeout),
    "exit_code": int(exit_code),
    "drc_mode": drc_mode,
}
if rule:
    result["stuck_at_rule"] = rule
with open(out, "w") as f:
    json.dump(result, f, indent=2)
    f.write("\n")
PYEOF
fi

# Strong provenance stamp (RMD-P0-02): the verdict names the exact run, layout
# bytes, deck bytes, and toolchain it graded — extract_drc.py carries these into
# reports/drc.json and the def-graph gate matches the digest against the layout
# it publishes.
python3 - "$DRC_DIR/drc_result.json" "$DRC_RUN_TAG" "$DRC_GDS" "$GDS_SHA_PRE" \
  "$DRC_DECK" "$DECK_SHA" "$KLAYOUT_VERSION" "$DRC_STARTED_AT" "$DRC_ENDED_AT" \
  "$DRC_TIMEOUT" "$DRC_STATUS" <<'PYEOF' || true
import json, sys
(path, run_tag, gds, gds_sha, deck, deck_sha,
 klayout_version, started, ended, timeout_s, exit_code) = sys.argv[1:12]
try:
    d = json.load(open(path))
except Exception:
    d = {}
d.update(checker="klayout_direct",
         run_tag=run_tag or None,
         gds_path=gds, gds_sha256=gds_sha or None,
         deck_path=deck, deck_sha256=deck_sha or None,
         klayout_version=klayout_version or None,
         started_at=started, ended_at=ended,
         timeout_s=int(timeout_s), exit_code=int(exit_code))
with open(path, "w") as f:
    json.dump(d, f, indent=2)
    f.write("\n")
PYEOF

# Frozen-layout override (RMD-P0-01): the checker itself cannot rebuild, so a
# changed digest set means the run mutated mid-check — the verdict grades
# foreign bytes. Force failed; extract_drc.py honors an explicit failed status
# over the count, so reports/drc.json inherits it.
if [[ "$IMPLICIT_REBUILD" == "1" && -f "$DRC_DIR/drc_result.json" ]]; then
  python3 - "$DRC_DIR/drc_result.json" "$GDS_SHA_PRE" "$GDS_SHA_POST" <<'PYEOF' || true
import json, sys
path, pre, post = sys.argv[1:4]
try:
    d = json.load(open(path))
except Exception:
    d = {}
d.update(status="failed", reason="layout_changed_under_signoff",
         gds_sha256_pre=pre, gds_sha256_post=post,
         note="the frozen run's physical artifacts changed while the checker ran; "
              "verdict does not describe the preserved backend layout (RMD-P0-01)")
with open(path, "w") as f:
    json.dump(d, f, indent=2)
    f.write("\n")
PYEOF
  if [[ $DRC_STATUS -eq 0 ]]; then DRC_STATUS=1; fi
fi

# Mirror drc_result.json into the run the verdict belongs to (RMD-P0-02: the
# SELECTED run, never `ls | tail -1` — a newer empty RUN dir must not adopt it).
if [[ -f "$DRC_DIR/drc_result.json" && -n "$RUN_DRC_DIR" ]]; then
  mkdir -p "$RUN_DRC_DIR"
  cp "$DRC_DIR/drc_result.json" "$RUN_DRC_DIR/" 2>/dev/null || true
fi

echo "Results: $DRC_DIR"

# --- Optional advisory Magic DRC cross-check (sky130 only; NON-authoritative) -------------
# When R2G_MAGIC_ADVISORY=1, run Magic DRC ALONGSIDE the KLayout signoff for visibility.
# KLayout remains the authoritative gate: extract_drc.py records Magic's count under
# `magic_advisory` and NEVER lets it change drc status/pass-fail/promotion (naive Magic
# over-reports std-cell li/mcon geometry — see failure-patterns.md "Magic DRC Failure").
# Best-effort: a Magic crash/timeout must not fail the flow (|| true). mtime-guarded so
# fix_signoff's DRC re-runs don't re-run Magic on an unchanged layout. Default OFF so the
# running campaign is unaffected unless the knob is explicitly exported.
if [[ "${R2G_MAGIC_ADVISORY:-0}" == "1" && "$PLATFORM" == sky130* ]]; then
  _magic_json="$DRC_DIR/magic_drc_result.json"
  if [[ -f "$_magic_json" && -f "$DRC_DIR/drc_run.log" && "$_magic_json" -nt "$DRC_DIR/drc_run.log" ]]; then
    echo "[advisory] Magic DRC cross-check already fresh for this layout — skipping"
  else
    _adv_to="${R2G_MAGIC_ADVISORY_TIMEOUT:-300}"
    echo "[advisory] running Magic DRC cross-check (non-authoritative, ${_adv_to}s cap)…"
    MAGIC_TIMEOUT="$_adv_to" timeout "$_adv_to" \
      bash "$(dirname "${BASH_SOURCE[0]}")/run_magic_drc.sh" "$PROJECT_DIR" "$PLATFORM" "$FLOW_VARIANT" \
      >"$DRC_DIR/magic_drc_advisory.log" 2>&1 \
      || echo "[advisory] Magic DRC cross-check did not complete (non-fatal) — see magic_drc_advisory.log"
  fi
fi

# Tier-0 journal: digest this check's tool log + extracted report (never breaks the flow).
[[ -f "$DRC_DIR/drc_run.log" ]] && python3 "$KNOWLEDGE_DIR_J/journal_db.py" summarize \
  --project "$PROJECT_DIR" --stage drc --tool klayout --log "$DRC_DIR/drc_run.log" \
  ${R2G_JOURNAL_DB:+--db "$R2G_JOURNAL_DB"} 2>/dev/null || true
[[ -f "$PROJECT_DIR/reports/drc.json" ]] && python3 "$KNOWLEDGE_DIR_J/journal_db.py" report \
  --project "$PROJECT_DIR" --kind drc --file "$PROJECT_DIR/reports/drc.json" \
  ${R2G_JOURNAL_DB:+--db "$R2G_JOURNAL_DB"} 2>/dev/null || true

exit $DRC_STATUS
