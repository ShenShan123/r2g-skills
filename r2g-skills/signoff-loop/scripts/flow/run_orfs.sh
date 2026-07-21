#!/usr/bin/env bash
set -euo pipefail

# usage: run_orfs.sh <project-dir> [platform] [flow_variant]
# Runs OpenROAD-flow-scripts backend for the given project.
# Expects <project-dir>/constraints/config.mk and constraint.sdc to exist.
# Results are collected back into <project-dir>/backend/
# Optional flow_variant (default: derived from project dir) isolates ORFS work directories.
# Set ORFS_TIMEOUT (seconds) to limit runtime (default: 7200 = 2 hours).
# Set ORFS_MAX_CPUS to limit CPU cores (default: all available).

PROJECT_DIR="${1:-}"
PLATFORM="${2:-asap7}"
# Derive FLOW_VARIANT from project directory basename to isolate ORFS work dirs
# per project config (e.g., swerv_cfg1 vs swerv_cfg2 get separate directories).
# This prevents directory collisions when multiple configs share the same DESIGN_NAME.
if [[ -n "${3:-}" ]]; then
  FLOW_VARIANT="$3"
elif [[ -n "$PROJECT_DIR" && -d "$PROJECT_DIR" ]]; then
  FLOW_VARIANT="$(basename "$(cd "$PROJECT_DIR" && pwd)")"
else
  FLOW_VARIANT="base"
fi
FROM_STAGE="${FROM_STAGE:-}"

# --- Self-heal stale ORFS stage-hook paths (failure-patterns.md #39) ---
# The skill tree moved r2g-rtl2gds/ -> r2g-skills/signoff-loop/ (2026-07-07 split),
# orphaning absolute POST_*_TCL hook paths baked into config.mk — especially old
# A/B-arm copies generated PRE-split and never regenerated (primaries were). A dead
# hook path makes ORFS `source` abort the stage (global_place.tcl: "couldn't read
# file ... no such file or directory"), which the loop then mislabels 'unseen_crash'
# — an arm that dies on a dead hook never diverges, starving the A/B evidence.
# Repoint any *_TCL hook whose file is MISSING to the same-basename file under THIS
# script's canonical orfs_hooks/ sibling. Conservative: only ever rewrites a BROKEN
# path; a valid path (even outside orfs_hooks/) is left untouched. Atomic in-place so
# a concurrently-spawned reader never sees a truncated config.mk.
HOOKS_DIR="${R2G_ORFS_HOOKS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/orfs_hooks" 2>/dev/null && pwd || true)}"
_heal_hook_paths() {  # $1 = config.mk to heal in place
  local cfg="$1" changed=0 tmp line prefix path base cand
  [[ -f "$cfg" ]] || return 0
  tmp="$(mktemp "${cfg}.heal.XXXXXX" 2>/dev/null)" || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" =~ ^([[:space:]]*export[[:space:]]+[A-Z0-9_]*_TCL[[:space:]]*=[[:space:]]*)(.+\.tcl)[[:space:]]*$ ]]; then
      prefix="${BASH_REMATCH[1]}"; path="${BASH_REMATCH[2]}"
      if [[ ! -f "$path" ]]; then
        base="$(basename "$path")"; cand="$HOOKS_DIR/$base"
        if [[ -n "$HOOKS_DIR" && -f "$cand" ]]; then
          line="${prefix}${cand}"; changed=1
          echo "run_orfs: healed stale hook path -> $cand (was: $path)" >&2
        else
          echo "run_orfs: WARNING stale hook path '$path' and no '$base' in '$HOOKS_DIR' — stage may abort" >&2
        fi
      fi
    fi
    printf '%s\n' "$line" >>"$tmp"
  done <"$cfg"
  if [[ "$changed" == 1 ]]; then mv -f "$tmp" "$cfg"; else rm -f "$tmp"; fi
}
# Self-test hook: heal a given config.mk and exit (lets the pytest suite exercise the
# migration without a full ORFS project). Mirrors campaign_resume_waves.sh's
# R2G_GUARD_SELFTEST (#37).
if [[ -n "${R2G_SELFTEST_HEAL_HOOKS:-}" ]]; then
  _heal_hook_paths "$R2G_SELFTEST_HEAL_HOOKS"; exit 0
fi

# --- Tier-0 journal hooks (engineer-loop spec §5.2) — never break the flow ---
KNOWLEDGE_DIR_J="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../knowledge" && pwd)"
JOURNAL="${R2G_JOURNAL_CLI:-$KNOWLEDGE_DIR_J/journal_db.py}"

_journal_stage() {  # stage status elapsed_s log_file — never breaks the flow
  local stage="$1" status="$2" elapsed="$3" log="$4"
  python3 "$JOURNAL" action --project "$PROJECT_DIR" --actor loop \
    --type tool_invoke --platform "${PLATFORM:-}" \
    --payload "{\"stage\":\"$stage\",\"status\":\"$status\",\"elapsed_s\":$elapsed,\"log\":\"$log\",\"cmd\":\"make $stage\"}" \
    ${R2G_JOURNAL_DB:+--db "$R2G_JOURNAL_DB"} 2>/dev/null || true
  [[ -f "$log" ]] && python3 "$JOURNAL" summarize --project "$PROJECT_DIR" \
    --stage "$stage" --tool openroad --log "$log" --status "$status" \
    ${R2G_JOURNAL_DB:+--db "$R2G_JOURNAL_DB"} 2>/dev/null || true
}

# --- Collision-resistant run identity + per-workspace lock (full-pipeline Issue 9) ---
# Two same-second invocations used to share one backend/RUN_<ts> dir (1s timestamp, no
# PID) and interleave stage_log.jsonl rows; and nothing serialized the shared ORFS
# workspace, so same-variant runs raced clean_all-vs-build. Defined before the
# source-only return so the pytest suite can exercise them in isolation.
_r2g_new_backend_dir() {  # base -> echoes "<dir>\t<RUN_TAG>" for a freshly-CREATED unique dir
  # Keep the sortable RUN_<ts> prefix (consumers glob RUN_* and sort lexically/by-mtime;
  # none parse the timestamp back out) and append PID + randomness. mkdir (no -p on the
  # leaf) fails on an existing dir, so a collision regenerates the suffix (bounded).
  local base="$1" tag dir i
  mkdir -p "$base" || return 1
  for i in 1 2 3 4 5 6 7 8; do
    tag="RUN_$(date +%Y-%m-%d_%H-%M-%S)_$$_$(printf '%04x' "$RANDOM")"
    dir="$base/$tag"
    if mkdir "$dir" 2>/dev/null; then
      printf '%s\t%s' "$dir" "$tag"
      return 0
    fi
  done
  return 1
}
_r2g_workspace_lockfile() {  # platform design variant -> echoes the lockfile path
  # Keyed on the SHARED ORFS workspace identity ($FLOW_DIR/.../<platform>/<design>/<variant>).
  local key h
  key="$1/$2/$3"
  h="$(printf '%s' "$key" | md5sum 2>/dev/null | awk '{print $1}')"
  [[ -z "$h" ]] && h="$(printf '%s' "$key" | tr -c 'A-Za-z0-9' '_')"
  printf '%s/r2g_ws_%s.lock' "${R2G_LOCK_DIR:-/tmp}" "$h"
}
_r2g_acquire_workspace_lock() {  # platform design variant -> holds an fd-scoped lock; 1 on contention
  command -v flock >/dev/null 2>&1 || { echo "run_orfs: flock unavailable — skipping workspace lock" >&2; return 0; }
  local lf; lf="$(_r2g_workspace_lockfile "$1" "$2" "$3")"
  exec {R2G_WS_LOCK_FD}>"$lf" || { echo "run_orfs: ERROR cannot open workspace lockfile $lf" >&2; return 1; }
  if ! flock -n "$R2G_WS_LOCK_FD"; then
    echo "run_orfs: ERROR another run holds the ORFS workspace (platform=$1 design=$2 variant=$3)." >&2
    echo "  Never run two configs with the same DESIGN_NAME+FLOW_VARIANT concurrently" >&2
    echo "  (CLAUDE.md Hard Rules) — they race clean_all vs build. Lockfile: $lf" >&2
    return 1
  fi
  return 0
}

# Test seam: allow sourcing helpers without executing the flow.
[[ "${R2G_SOURCE_ONLY:-0}" == "1" ]] && return 0 2>/dev/null
# --- end Tier-0 journal hooks ---

# Auto-detect ORFS + tools (honors ORFS_ROOT / *_EXE env overrides)
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_env.sh"

if [[ -z "${ORFS_ROOT:-}" || ! -d "$FLOW_DIR" ]]; then
  echo "ERROR: ORFS not found. Set ORFS_ROOT to your OpenROAD-flow-scripts checkout." >&2
  exit 1
fi

if [[ -z "$PROJECT_DIR" ]]; then
  echo "usage: run_orfs.sh <project-dir> [platform]" >&2
  exit 1
fi

PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
CONFIG_MK="$PROJECT_DIR/constraints/config.mk"
SDC_FILE="$PROJECT_DIR/constraints/constraint.sdc"

if [[ ! -f "$CONFIG_MK" ]]; then
  echo "ERROR: config.mk not found at $CONFIG_MK" >&2
  exit 1
fi

if [[ ! -f "$SDC_FILE" ]]; then
  echo "ERROR: constraint.sdc not found at $SDC_FILE" >&2
  exit 1
fi

# Create a design directory inside ORFS for this project.
# Key fix: include FLOW_VARIANT in the path so concurrent runs that share
# DESIGN_NAME (e.g. all ICCAD benchmarks use DESIGN_NAME=top) do not overwrite
# each other's config.mk at the shared $FLOW_DIR/designs/<platform>/<name>/ path.
DESIGN_NAME=$(grep 'DESIGN_NAME' "$CONFIG_MK" | head -1 | sed 's/.*=\s*//' | tr -d ' ')

# Serialize the shared ORFS workspace BEFORE any write/EDA (config copy, clean_all,
# stage builds). Contention = the DESIGN_NAME+FLOW_VARIANT hard-rule violation: fail
# fast with a clear message rather than corrupt both runs (full-pipeline Issue 9).
# The lock is fd-scoped — released automatically when this script exits.
if [[ "${R2G_SKIP_WORKSPACE_LOCK:-0}" != "1" ]]; then
  _r2g_acquire_workspace_lock "$PLATFORM" "$DESIGN_NAME" "$FLOW_VARIANT" || exit 1
fi

ORFS_DESIGN_DIR="$FLOW_DIR/designs/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
mkdir -p "$ORFS_DESIGN_DIR"

# Repoint any dead stage-hook path (skill-relocation staleness; #39) before copy so
# the durable source AND this run's working copy are both corrected.
_heal_hook_paths "$CONFIG_MK"

# Copy config.mk and constraint.sdc
cp "$CONFIG_MK" "$ORFS_DESIGN_DIR/config.mk"
cp "$SDC_FILE" "$ORFS_DESIGN_DIR/constraint.sdc"

# Ensure RTL path in config.mk is absolute
# (The config.mk should already use absolute paths, but let's verify)
if grep -q 'VERILOG_FILES' "$ORFS_DESIGN_DIR/config.mk"; then
  echo "config.mk has VERILOG_FILES entry"
else
  echo "WARNING: config.mk missing VERILOG_FILES" >&2
fi

# Create this run's unique backend dir + collision-resistant RUN_TAG (full-pipeline
# Issue 9). The helper mkdir's a fresh dir, regenerating the PID/random suffix on the
# (vanishingly rare) collision, so two same-second runs never share one dir.
_bd_pair="$(_r2g_new_backend_dir "$PROJECT_DIR/backend")" \
  || { echo "ERROR: could not create a unique backend RUN dir under $PROJECT_DIR/backend" >&2; exit 1; }
BACKEND_DIR="${_bd_pair%%$'\t'*}"; RUN_TAG="${_bd_pair#*$'\t'}"; unset _bd_pair
echo "Starting ORFS run: $RUN_TAG"
echo "Design: $DESIGN_NAME"
echo "Platform: $PLATFORM"
echo "Flow variant: $FLOW_VARIANT"
echo "Config: $ORFS_DESIGN_DIR/config.mk"

# Run the ORFS flow
cd "$FLOW_DIR"

# Prevent env collision: ORFS Makefile uses SCRIPTS_DIR internally
unset SCRIPTS_DIR 2>/dev/null || true

if [[ -z "$FROM_STAGE" ]]; then
  echo "Cleaning previous ORFS state for variant=$FLOW_VARIANT ..."
  make DESIGN_CONFIG="$ORFS_DESIGN_DIR/config.mk" FLOW_VARIANT="$FLOW_VARIANT" clean_all 2>&1 | tail -5 || echo "WARNING: clean_all returned non-zero (may be first run)" >&2
else
  echo "Skipping clean_all (resuming from stage: $FROM_STAGE)"
fi

# BACKEND_DIR was created up-front with the unique RUN_TAG (see _r2g_new_backend_dir).

# Timeout and CPU limit support
ORFS_TIMEOUT="${ORFS_TIMEOUT:-7200}"
MAKE_CMD="make DESIGN_CONFIG=\"$ORFS_DESIGN_DIR/config.mk\" FLOW_VARIANT=\"$FLOW_VARIANT\""

# Allow config.mk to opt into PLACE_FAST / ROUTE_FAST without requiring the
# caller to set the env var. A line like `export ROUTE_FAST = 1` in
# config.mk gets respected here. Env var still wins if already set.
# IMPORTANT: temporarily disable -e/pipefail around the grep|head|sed pipeline
# because a missing knob (most common case) makes grep exit 1, which would
# otherwise abort the entire script under `set -eo pipefail`.
set +e +o pipefail
for _knob in PLACE_FAST ROUTE_FAST ROUTE_FAST_SKIP_DRT ROUTE_FAST_DRT_ITERS; do
  if [[ -z "${!_knob:-}" ]]; then
    _val=$(grep -E "^[[:space:]]*export[[:space:]]+${_knob}[[:space:]]*=" "$CONFIG_MK" 2>/dev/null | head -1 | sed -E "s/^[[:space:]]*export[[:space:]]+${_knob}[[:space:]]*=[[:space:]]*//" | tr -d ' "')
    if [[ -n "$_val" ]]; then
      export "$_knob=$_val"
      echo "config.mk supplied $_knob=$_val"
    fi
  fi
done
set -e -o pipefail
unset _knob _val 2>/dev/null || true

# PLACE_FAST escape hatch: disable timing-driven + routability-driven global
# placement. Required for very-large netlists (>1M nets) where the timing
# repair loop in `gpl` would otherwise spin for hours after the placement
# overflow target is already met. Applies to BOOM-class CPUs and similar.
# Set PLACE_FAST=1 in the env OR add `export PLACE_FAST = 1` to config.mk.
if [[ "${PLACE_FAST:-0}" == "1" ]]; then
  MAKE_CMD="$MAKE_CMD GPL_TIMING_DRIVEN=0 GPL_ROUTABILITY_DRIVEN=0"
  echo "PLACE_FAST=1 → disabling GPL_TIMING_DRIVEN and GPL_ROUTABILITY_DRIVEN"
fi

# ROUTE_FAST escape hatch: cap GRT/DRT iterations and skip the optional
# repair/antenna passes that dominate runtime on >M-net netlists. Required
# for BOOM ChipTop class where each GRT extra-iteration phase has 30
# iterations × 2 phases × ~2.4M nets and never converges in <24h.
# Set ROUTE_FAST=1 in the env to enable.
#
# Knobs applied (read by ORFS Makefile from env):
#   GLOBAL_ROUTE_ARGS=-congestion_iterations 5 -allow_congestion -verbose
#     -congestion_report_iter_step 1
#       — cap initial GRT extra-iteration phase at 5 (vs default 30) and
#         accept the result even with congestion violations.
#   SKIP_INCREMENTAL_REPAIR=1
#       — skip repair_design_helper + incremental GRT + repair_timing_helper
#         block inside global_route.tcl. Dominates GRT stage runtime.
#   SKIP_ANTENNA_REPAIR=1
#       — skip antenna repair iterations (each rebuilds affected nets).
#   DETAILED_ROUTE_END_ITERATION=10  (default 64)
#       — cap detailed-routing iterations.
#
# Optional further fallback: ROUTE_FAST_SKIP_DRT=1 also enables
# SKIP_DETAILED_ROUTE=1 — produces DEF + global routes but no GDS.
if [[ "${ROUTE_FAST:-0}" == "1" ]]; then
  # GLOBAL_ROUTE_ARGS is passed as a quoted make cmdline arg so it survives
  # ORFS's per-step variable scrub (see references/orfs-playbook.md).
  GRT_FAST_ARGS='-congestion_iterations 5 -allow_congestion -verbose -congestion_report_iter_step 1'
  # GRT_INCREMENTAL_ALLOW_CONGESTION enables a SKILL-LOCAL patch in
  # OpenROAD-flow-scripts/flow/scripts/global_route.tcl that adds
  # -allow_congestion to the post-recover_power -end_incremental GRT call.
  # Without this patch, the initial GRT call may pass with congestion
  # (allowed via GLOBAL_ROUTE_ARGS) but the recover_power_helper's
  # incremental GRT then aborts with ERROR GRT-0116 on the same residual
  # congestion. ChipTop-class designs cannot reach 0 overflow on this
  # OpenROAD/nangate45, so this is required for any ROUTE_FAST run.
  # DRT iteration cap: default 10. Override with ROUTE_FAST_DRT_ITERS for an
  # even faster (dirtier) detailed-route pass — e.g. =1 produces a GDS quickly
  # for congestion-bound designs that would never converge.
  DRT_ITERS="${ROUTE_FAST_DRT_ITERS:-10}"
  MAKE_CMD="$MAKE_CMD SKIP_INCREMENTAL_REPAIR=1 SKIP_ANTENNA_REPAIR=1 DETAILED_ROUTE_END_ITERATION=$DRT_ITERS GLOBAL_ROUTE_ARGS='$GRT_FAST_ARGS' GRT_INCREMENTAL_ALLOW_CONGESTION=1"
  echo "ROUTE_FAST=1 → SKIP_INCREMENTAL_REPAIR + SKIP_ANTENNA_REPAIR + DRT_END_ITER=$DRT_ITERS"
  echo "             → GLOBAL_ROUTE_ARGS='$GRT_FAST_ARGS'"
  echo "             → GRT_INCREMENTAL_ALLOW_CONGESTION=1 (requires patched global_route.tcl)"
  if [[ "${ROUTE_FAST_SKIP_DRT:-0}" == "1" ]]; then
    MAKE_CMD="$MAKE_CMD SKIP_DETAILED_ROUTE=1"
    echo "ROUTE_FAST_SKIP_DRT=1 → SKIP_DETAILED_ROUTE=1 (no GDS, DEF only)"
  fi
fi

# Apply CPU core limit if specified
if [[ -n "${ORFS_MAX_CPUS:-}" ]]; then
  # Build a CPU list 0-(N-1)
  CPU_LIST="0-$((ORFS_MAX_CPUS - 1))"
  MAKE_CMD="taskset -c $CPU_LIST $MAKE_CMD"
  echo "Limiting to $ORFS_MAX_CPUS CPU cores ($CPU_LIST)"
fi

echo "Timeout: ${ORFS_TIMEOUT}s"

# Stage-by-stage execution support
ORFS_STAGES_LIST="${ORFS_STAGES:-synth floorplan place cts route finish}"

# Guard: if FROM_STAGE is set but doesn't match any known stage, abort loudly.
# Without this, the stage loop silently skips every stage and exits 0, which has
# caused ghost "passes" in batch runners that accidentally passed a timeout value
# (e.g. "14400") to FROM_STAGE.
if [[ -n "$FROM_STAGE" ]]; then
  stage_known=false
  for _s in $ORFS_STAGES_LIST; do
    if [[ "$_s" == "$FROM_STAGE" ]]; then
      stage_known=true
      break
    fi
  done
  if [[ "$stage_known" != "true" ]]; then
    echo "ERROR: FROM_STAGE='$FROM_STAGE' does not match any stage in ORFS_STAGES_LIST='$ORFS_STAGES_LIST'" >&2
    echo "       Valid stages: $ORFS_STAGES_LIST" >&2
    exit 2
  fi
fi

# Stage-scoped invalidation on resume (failure-patterns.md #35): config.mk is
# NOT a make prerequisite in ORFS, so resuming FROM_STAGE over intact artifacts
# makes `make <stage>` a NO-OP — a just-applied config edit silently never takes
# effect. Cleaning exactly the resumed stage forces it (and, via the odb
# dependency chain, everything downstream) to rebuild while every stage BEFORE
# it is REUSED — this is what makes FROM_STAGE both correct for config edits
# and cheaper than the clean_all full rebuild. Opt out with
# R2G_RESUME_NO_CLEAN=1 (pure crash-resume of an interrupted flow, unchanged
# config — e.g. the finish-stage GDS resume).
# Resume provenance (failure-patterns.md #38 / codex #3): make the rerun decision
# auditable. The reused/rerun choice used to be a bare stdout echo (invisible in
# the persisted backend/RUN_*/flow.log). Now the decision is tee'd to flow.log
# with its concrete REASON (R2G_RERUN_REASON, supplied by fix_signoff.sh), and a
# structured resume_meta.json records which stages were REUSED and why.
_write_resume_meta() {  # reason no_clean
  local reason="$1" no_clean="$2" reused="" s
  for s in $ORFS_STAGES_LIST; do
    [[ "$s" == "$FROM_STAGE" ]] && break
    reused="${reused:+$reused }$s"
  done
  # Content-addressed parent chain for the REUSED stages (pilot P0-4, 2026-07-21):
  # a resumed run's own stage_log.jsonl only records the stages it reran, so its
  # RUN dir looked like a complete flow to signoff_gate.py while synth..cts were
  # actually consumed from an EARLIER run. Record, per reused stage, the sha256 of
  # the canonical artifact being consumed from the ORFS results dir RIGHT NOW plus
  # the newest sibling RUN whose ledger shows a clean row for that stage — so
  # completion can later be judged as a reconstructable six-stage lineage instead
  # of merely a successful finish row.
  local rdir="$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
  [[ -d "$rdir" ]] || rdir="$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME"
  python3 - "$BACKEND_DIR/resume_meta.json" "$FROM_STAGE" "$reason" "$no_clean" "$reused" \
            "$rdir" "$PROJECT_DIR/backend" "$(basename "$BACKEND_DIR")" <<'PY' 2>/dev/null || true
import hashlib, json, os, sys, time
path, from_stage, reason, no_clean, reused, rdir, backend, self_run = sys.argv[1:9]
STAGE_ARTIFACT = {"synth": "1_synth.v", "floorplan": "2_floorplan.odb",
                  "place": "3_place.odb", "cts": "4_cts.odb",
                  "route": "5_route.odb", "finish": "6_final.odb"}

def _sha256(p):
    try:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None

def _clean_stage_runs(stage):
    """Sibling RUN dirs (newest first, excluding this run) whose stage_log has a
    clean row for `stage`."""
    try:
        runs = sorted((d for d in os.listdir(backend)
                       if d.startswith("RUN_") and d != self_run),
                      key=lambda d: os.path.getmtime(os.path.join(backend, d)),
                      reverse=True)
    except OSError:
        return []
    out = []
    for d in runs:
        slog = os.path.join(backend, d, "stage_log.jsonl")
        try:
            with open(slog, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if rec.get("stage") == stage and rec.get("status") in (0, "0"):
                        out.append(d)
                        break
        except OSError:
            continue
    return out

lineage = {}
for stage in (reused.split() if reused else []):
    art = STAGE_ARTIFACT.get(stage)
    apath = os.path.join(rdir, art) if art else None
    parents = _clean_stage_runs(stage)
    lineage[stage] = {
        "artifact": art,
        "sha256": _sha256(apath) if apath and os.path.isfile(apath) else None,
        "parent_run": parents[0] if parents else None,
    }
json.dump({"from_stage": from_stage, "reason": reason, "no_clean": no_clean == "1",
           "reused_stages": reused.split() if reused else [],
           "parent_lineage": lineage, "ts": int(time.time())},
          open(path, "w"), indent=1)
PY
}
if [[ -n "$FROM_STAGE" ]]; then
  RERUN_REASON="${R2G_RERUN_REASON:-stage-scoped resume: config edit forces clean_$FROM_STAGE; earlier stages reused}"
  if [[ "${R2G_RESUME_NO_CLEAN:-0}" != "1" ]]; then
    echo "Invalidating resumed stage: make clean_$FROM_STAGE — reason: $RERUN_REASON (earlier stages reused)" \
      | tee -a "$BACKEND_DIR/flow.log"
    make DESIGN_CONFIG="$ORFS_DESIGN_DIR/config.mk" FLOW_VARIANT="$FLOW_VARIANT" "clean_$FROM_STAGE" 2>&1 | tail -3 \
      || echo "WARNING: clean_$FROM_STAGE returned non-zero (stage may have no artifacts yet)" >&2
    _write_resume_meta "$RERUN_REASON" 0
  else
    echo "Resuming FROM_STAGE=$FROM_STAGE (R2G_RESUME_NO_CLEAN=1: pure crash-resume, no clean) — reason: $RERUN_REASON" \
      | tee -a "$BACKEND_DIR/flow.log"
    _write_resume_meta "$RERUN_REASON" 1
  fi
fi

run_stage() {
  local stage="$1"
  echo ""
  echo "=== Running ORFS stage: $stage ==="
  local stage_start
  stage_start=$(date +%s)

  local STAGE_STATUS=0
  set +e +o pipefail
  # Reap the ENTIRE stage tree on timeout (failure-patterns.md #40). GNU `timeout` forks the
  # command into a NEW process group and signals that whole group (SIGTERM, then SIGKILL after
  # --kill-after) — BUT ONLY when timeout is not itself a process-group leader. The old
  # `setsid timeout ...` made timeout a session/group leader, so `setpgid(0,0)` failed and
  # timeout fell back to signaling only its direct `bash -c` child. On a stage that actually
  # hit ORFS_TIMEOUT (e.g. a 143K-cell design's ~2h KLayout DRC), the deep tool grandchild
  # (klayout/openroad) was orphaned (reparented to init) and KEPT RUNNING — holding the stdout
  # pipe open so `tee` never saw EOF, hanging run_orfs.sh and freezing the whole campaign for
  # hours behind one design. Dropping `setsid` lets timeout become the new group's leader and
  # group-kill the whole tree. (Empirically: `setsid timeout` leaves orphans; plain `timeout`
  # reaps them — test_run_orfs_timeout_reaping.py.)
  timeout --signal=TERM --kill-after=60 "$ORFS_TIMEOUT" \
    bash -c "$MAKE_CMD $stage" 2>&1 | tee -a "$BACKEND_DIR/flow.log"
  STAGE_STATUS=${PIPESTATUS[0]}
  set -e -o pipefail

  local stage_end
  stage_end=$(date +%s)
  local stage_elapsed=$((stage_end - stage_start))
  # Per-stage provenance (failure-patterns.md #38 / codex #3): absolute timestamps
  # + the newest output ODB the stage produced, so a later resume's reuse of an
  # earlier stage is auditable (WHAT it produced, WHEN). Best-effort, fail-soft.
  # The {stage,status,elapsed_s} contract is PRESERVED (many readers key on
  # status per row) — the new keys are purely additive.
  local _rdir="$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
  [[ -d "$_rdir" ]] || _rdir="$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME"
  local _extra="\"ts_start\": $stage_start, \"ts_end\": $stage_end"
  if [[ $STAGE_STATUS -eq 0 && -d "$_rdir" ]]; then
    local _art _artmt
    _art=$(ls -t "$_rdir"/*.odb 2>/dev/null | head -1 || true)
    if [[ -n "$_art" ]]; then
      _artmt=$(date -r "$_art" +%s 2>/dev/null || echo "")
      _extra="$_extra, \"artifact\": \"$(basename "$_art")\""
      [[ -n "$_artmt" ]] && _extra="$_extra, \"artifact_mtime\": $_artmt"
    fi
  fi
  echo "{\"stage\": \"$stage\", \"status\": $STAGE_STATUS, \"elapsed_s\": $stage_elapsed, $_extra}" >> "$BACKEND_DIR/stage_log.jsonl"
  _journal_stage "$stage" "$([[ "$STAGE_STATUS" -eq 0 ]] && echo pass || echo fail)" "$stage_elapsed" "$BACKEND_DIR/flow.log"

  if [[ $STAGE_STATUS -ne 0 ]]; then
    echo "ERROR: Stage '$stage' failed (exit code $STAGE_STATUS) after ${stage_elapsed}s" | tee -a "$BACKEND_DIR/flow.log"
    if [[ $STAGE_STATUS -eq 124 || $STAGE_STATUS -eq 137 ]]; then
      echo "  (timed out after ${ORFS_TIMEOUT}s, exit code $STAGE_STATUS)" | tee -a "$BACKEND_DIR/flow.log"
    fi
    return $STAGE_STATUS
  fi
  echo "Stage '$stage' completed in ${stage_elapsed}s"
  return 0
}

# Run stages
MAKE_STATUS=0
SKIP_STAGES=true
if [[ -z "$FROM_STAGE" ]]; then
  SKIP_STAGES=false
fi

for stage in $ORFS_STAGES_LIST; do
  if [[ "$SKIP_STAGES" == "true" ]]; then
    if [[ "$stage" == "$FROM_STAGE" ]]; then
      SKIP_STAGES=false
    else
      # Persist the reuse decision (codex #3): tee to flow.log, not stdout-only.
      echo "Reusing stage: $stage (artifacts from a prior run; resuming from $FROM_STAGE)" \
        | tee -a "$BACKEND_DIR/flow.log"
      continue
    fi
  fi

  run_stage "$stage" || { MAKE_STATUS=$?; break; }
done

# Detect routing failure and suggest recovery
if [[ $MAKE_STATUS -ne 0 ]]; then
  FAILED_STAGE=$(tail -1 "$BACKEND_DIR/stage_log.jsonl" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('stage','unknown'))" 2>/dev/null || echo "unknown")
  if [[ "$FAILED_STAGE" == "grt" || "$FAILED_STAGE" == "route" ]]; then
    echo "" | tee -a "$BACKEND_DIR/flow.log"
    # Distinguish the two route-stage failure modes — the old HINT called every
    # route abort "congestion", but the common case is a wall-clock TIMEOUT killing
    # detailed routing mid-grind (exit 124/137), NOT a global-route GRT-0116 abort.
    # The learnable, A/B-validated fix for BOTH is route_relief (lower CORE_UTILIZATION
    # so DRT has room to converge within budget); see references/failure-patterns.md
    # "Routing Congestion" and the route-relief note.
    if [[ $MAKE_STATUS -eq 124 || $MAKE_STATUS -eq 137 ]] \
       && ! grep -q "GRT-0116" "$BACKEND_DIR/flow.log" 2>/dev/null; then
      echo "HINT: Detailed routing was KILLED by the ${ORFS_TIMEOUT}s wall-clock (exit $MAKE_STATUS)," | tee -a "$BACKEND_DIR/flow.log"
      echo "      not a global-route congestion abort. Two levers:" | tee -a "$BACKEND_DIR/flow.log"
      echo "  1. route_relief (PREFERRED, learnable): lower CORE_UTILIZATION so DRT converges" | tee -a "$BACKEND_DIR/flow.log"
      echo "       fix_signoff.sh $PROJECT_DIR $PLATFORM --check route" | tee -a "$BACKEND_DIR/flow.log"
      echo "  2. Or just give it more time: ORFS_TIMEOUT=14400 FROM_STAGE=route scripts/flow/run_orfs.sh $PROJECT_DIR $PLATFORM" | tee -a "$BACKEND_DIR/flow.log"
    else
      echo "HINT: Routing congestion detected. Try re-running with:" | tee -a "$BACKEND_DIR/flow.log"
      echo "  1. route_relief (learnable): fix_signoff.sh $PROJECT_DIR $PLATFORM --check route" | tee -a "$BACKEND_DIR/flow.log"
      echo "     (lowers CORE_UTILIZATION to open routing channels; deck never relaxed)" | tee -a "$BACKEND_DIR/flow.log"
      echo "  2. Or: add export ROUTING_LAYER_ADJUSTMENT = 0.10 and resume FROM_STAGE=route" | tee -a "$BACKEND_DIR/flow.log"
    fi
    # Auto-suggest ROUTE_FAST when failure is on a ChipTop/BOOM-scale design
    # (large 4_cts.odb is the cheapest signal; no need to walk the netlist).
    CTS_ODB="$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT/4_cts.odb"
    CTS_SIZE=0
    if [[ -f "$CTS_ODB" ]]; then
      CTS_SIZE=$(stat -c%s "$CTS_ODB" 2>/dev/null || echo 0)
    fi
    # >1 GB CTS database ≈ ChipTop/BOOM scale. Recommend ROUTE_FAST.
    if (( CTS_SIZE > 1073741824 )); then
      echo "  3. ChipTop-scale CTS ODB detected (${CTS_SIZE} bytes). Add ROUTE_FAST=1:" | tee -a "$BACKEND_DIR/flow.log"
      echo "       ROUTE_FAST=1 FROM_STAGE=route scripts/flow/run_orfs.sh $PROJECT_DIR $PLATFORM" | tee -a "$BACKEND_DIR/flow.log"
      echo "       (skips post-GRT incremental repair + antenna; caps DRT to 10 iters)" | tee -a "$BACKEND_DIR/flow.log"
    fi
  elif [[ "$FAILED_STAGE" == "floorplan" ]]; then
    if grep -q "PDN-0179\|Insufficient width to add straps\|Unable to repair all channels" "$BACKEND_DIR/flow.log" 2>/dev/null; then
      echo "" | tee -a "$BACKEND_DIR/flow.log"
      echo "HINT: PDN channel repair failure (PDN-0179) detected during floorplan." | tee -a "$BACKEND_DIR/flow.log"
      echo "  The design has too many cells for the current die area." | tee -a "$BACKEND_DIR/flow.log"
      echo "  Possible fixes:" | tee -a "$BACKEND_DIR/flow.log"
      echo "  1. Increase DIE_AREA/CORE_AREA by 10-20% in config.mk" | tee -a "$BACKEND_DIR/flow.log"
      echo "  2. Reduce PLACE_DENSITY in config.mk" | tee -a "$BACKEND_DIR/flow.log"
      echo "  3. Remove SYNTH_HIERARCHICAL=1 if set (reduces cell count)" | tee -a "$BACKEND_DIR/flow.log"
      echo "  4. Remove ABC_AREA=1 if set (changes cell mix)" | tee -a "$BACKEND_DIR/flow.log"
    fi
  elif [[ "$FAILED_STAGE" == "place" ]]; then
    # Two distinct stalls live inside the place stage. Diagnose which:
    # - 3_3_place_gp stuck in `Timing-driven iteration N/2` (gpl resizer pass)
    #   → PLACE_FAST=1 fixes this (disables GPL_TIMING_DRIVEN/ROUTABILITY_DRIVEN).
    # - 3_4_place_resized stuck in `repair_design -verbose` (resize.tcl) on a
    #   multi-M-net design (Iteration|Area|Resized|Buffers|Nets repaired|Remaining).
    #   PLACE_FAST does NOT help here — repair_design is a separate code path.
    #   Observed on arm_core (2026-05-26, 8h budget exhausted at iter 785K/1.36M).
    GP_STUCK=0
    RESIZED_STUCK=0
    if grep -qE "Timing-driven iteration .*virtual.*false" "$BACKEND_DIR/flow.log" 2>/dev/null; then
      GP_STUCK=1
    fi
    if [[ -n "${FLOW_DIR:-}" ]]; then
      LATEST_PLACE_TMP=$(ls -t "$FLOW_DIR/logs/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT/3_4_place_resized.tmp.log" 2>/dev/null | head -1)
      if [[ -f "$LATEST_PLACE_TMP" ]] && tail -200 "$LATEST_PLACE_TMP" 2>/dev/null | grep -qE "Iteration\s+\|.*Resized.*Buffers.*Nets repaired"; then
        RESIZED_STUCK=1
      fi
    fi
    if [[ $GP_STUCK -eq 1 ]]; then
      echo "" | tee -a "$BACKEND_DIR/flow.log"
      echo "HINT: Place_gp timing-driven repair appears stuck on a very large netlist." | tee -a "$BACKEND_DIR/flow.log"
      echo "  Validated workaround for BOOM-class designs (place_gp only):" | tee -a "$BACKEND_DIR/flow.log"
      echo "  1. PLACE_FAST=1 FROM_STAGE=place scripts/flow/run_orfs.sh $PROJECT_DIR $PLATFORM" | tee -a "$BACKEND_DIR/flow.log"
      echo "  2. Or add to config.mk: export GPL_TIMING_DRIVEN=0; export GPL_ROUTABILITY_DRIVEN=0" | tee -a "$BACKEND_DIR/flow.log"
    fi
    if [[ $RESIZED_STUCK -eq 1 ]]; then
      echo "" | tee -a "$BACKEND_DIR/flow.log"
      echo "HINT: 3_4_place_resized's repair_design appears stuck on buffer insertion." | tee -a "$BACKEND_DIR/flow.log"
      echo "  This is a DIFFERENT hang from place_gp — PLACE_FAST does not fix it." | tee -a "$BACKEND_DIR/flow.log"
      echo "  No ORFS knob currently skips repair_design at place stage." | tee -a "$BACKEND_DIR/flow.log"
      echo "  Reduce design size (smaller CORE_UTILIZATION, less aggressive synth)" | tee -a "$BACKEND_DIR/flow.log"
      echo "  or accept the design is intractable on this OpenROAD version." | tee -a "$BACKEND_DIR/flow.log"
      echo "  Reference: arm_core (Amber a25 + 4 single_port_ram_*) hit this 2026-05-26." | tee -a "$BACKEND_DIR/flow.log"
    fi
  elif [[ "$FAILED_STAGE" == "synth" ]]; then
    # Synth-stage failures fall into three documented shapes, none fixable by a P&R
    # knob (see references/failure-patterns.md). Emit a targeted HINT so the operator
    # does not waste budget re-running with bigger timeouts / lower utilization.
    if grep -qE "Executing AST frontend in derive mode" "$BACKEND_DIR/flow.log" 2>/dev/null \
       && [[ $MAKE_STATUS -eq 124 || $MAKE_STATUS -eq 137 ]]; then
      echo "" | tee -a "$BACKEND_DIR/flow.log"
      echo "HINT: Synth timed out inside Yosys AST 'derive mode' — a const-function" | tee -a "$BACKEND_DIR/flow.log"
      echo "  elaboration blowup (classic parametric LFSR/CRC lfsr_mask), NOT scale." | tee -a "$BACKEND_DIR/flow.log"
      echo "  A longer ORFS_TIMEOUT / lower utilization will NOT help (pre-floorplan)." | tee -a "$BACKEND_DIR/flow.log"
      echo "  Intractable without RTL surgery. See failure-patterns.md:" | tee -a "$BACKEND_DIR/flow.log"
      echo "  'LFSR / CRC parametric function expansion in Yosys AST frontend'." | tee -a "$BACKEND_DIR/flow.log"
    elif grep -qE "GTECH_[A-Z0-9_]+.* referenced .* not part of the design" "$BACKEND_DIR/flow.log" 2>/dev/null; then
      echo "" | tee -a "$BACKEND_DIR/flow.log"
      echo "HINT: Synth failed on a missing Synopsys GTECH/DesignWare primitive —" | tee -a "$BACKEND_DIR/flow.log"
      echo "  the RTL bundle is incomplete (vendor cell library absent). No config" | tee -a "$BACKEND_DIR/flow.log"
      echo "  knob supplies it; do NOT stub sequential/MUX cells (corrupts netlist)." | tee -a "$BACKEND_DIR/flow.log"
      echo "  See failure-patterns.md: 'Missing proprietary primitive library'." | tee -a "$BACKEND_DIR/flow.log"
    elif [[ $MAKE_STATUS -eq 124 || $MAKE_STATUS -eq 137 ]]; then
      echo "" | tee -a "$BACKEND_DIR/flow.log"
      echo "HINT: Synth timed out with no AST-derive or GTECH signature — likely a pure" | tee -a "$BACKEND_DIR/flow.log"
      echo "  SCALE timeout (huge multiplier/array design stuck in OPT/FLATTEN/ABC)." | tee -a "$BACKEND_DIR/flow.log"
      echo "  Triage per failure-patterns.md 'Synth timeout triage: AST pathology vs" | tee -a "$BACKEND_DIR/flow.log"
      echo "  scale timeout'. If genuinely scale-bound (e.g. koios_lenet LeNet CNN), it" | tee -a "$BACKEND_DIR/flow.log"
      echo "  may be intractable on this host; a longer ORFS_TIMEOUT only sometimes helps." | tee -a "$BACKEND_DIR/flow.log"
    fi
  fi
fi

# Collect results (ORFS uses FLOW_VARIANT as subdirectory)
RESULTS_DIR="$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
LOGS_DIR="$FLOW_DIR/logs/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
OBJECTS_DIR="$FLOW_DIR/objects/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
REPORTS_DIR="$FLOW_DIR/reports/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"

# Fallback: if variant dir doesn't exist, try without it
if [[ ! -d "$RESULTS_DIR" ]]; then
  RESULTS_DIR="$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME"
  LOGS_DIR="$FLOW_DIR/logs/$PLATFORM/$DESIGN_NAME"
  OBJECTS_DIR="$FLOW_DIR/objects/$PLATFORM/$DESIGN_NAME"
  REPORTS_DIR="$FLOW_DIR/reports/$PLATFORM/$DESIGN_NAME"
fi

# Copy results to project backend directory
if [[ -d "$RESULTS_DIR" ]]; then
  cp -r "$RESULTS_DIR" "$BACKEND_DIR/results" 2>/dev/null || true
fi

if [[ -d "$LOGS_DIR" ]]; then
  cp -r "$LOGS_DIR" "$BACKEND_DIR/logs" 2>/dev/null || true
fi

if [[ -d "$REPORTS_DIR" ]]; then
  cp -r "$REPORTS_DIR" "$BACKEND_DIR/reports_orfs" 2>/dev/null || true
fi

# Preserve objects/ too (pilot P1-2, 2026-07-21): ORFS stage rules also depend on
# objects/ (merged libs, klayout .lyt, ABC scripts). A later signoff restage that
# recovers results/ but not objects/ leaves `make drc` seeing missing/older
# prerequisites and IMPLICITLY REBUILDING synth→finish before KLayout. Best-effort
# like the copies above; _restage_for_signoff.sh restages it identity-aware.
if [[ -d "$OBJECTS_DIR" ]]; then
  cp -r "$OBJECTS_DIR" "$BACKEND_DIR/objects" 2>/dev/null || true
fi

# Copy key artifacts
GDS_FILES=$(find "$RESULTS_DIR" -name "*.gds" 2>/dev/null || true)
DEF_FILES=$(find "$RESULTS_DIR" -name "*.def" 2>/dev/null || true)
ODB_FILES=$(find "$RESULTS_DIR" -name "*.odb" 2>/dev/null || true)

mkdir -p "$BACKEND_DIR/final"

for f in $GDS_FILES; do
  cp "$f" "$BACKEND_DIR/final/" 2>/dev/null || true
done
for f in $DEF_FILES; do
  cp "$f" "$BACKEND_DIR/final/" 2>/dev/null || true
done
for f in $ODB_FILES; do
  cp "$f" "$BACKEND_DIR/final/" 2>/dev/null || true
done

# HONESTY GUARD (2026-07-04 audit M7): the copies above swallow failures
# (`|| true` keeps a partial resume usable), but a SUCCESSFUL flow whose GDS
# never reached backend/final/ (disk full, permissions) must not report
# success — signoff would later find no GDS and misdiagnose the design.
# Downgrade to failure with an explicit reason; run-meta records the new status.
if [[ $MAKE_STATUS -eq 0 && -n "$GDS_FILES" ]] && ! ls "$BACKEND_DIR"/final/*.gds >/dev/null 2>&1; then
  echo "ERROR: flow succeeded but no GDS reached $BACKEND_DIR/final (result copy failed — disk full?)" | tee -a "$BACKEND_DIR/flow.log"
  MAKE_STATUS=1
fi

# Write run metadata
cat > "$BACKEND_DIR/run-meta.json" <<METAEOF
{
  "run_tag": "$RUN_TAG",
  "design_name": "$DESIGN_NAME",
  "platform": "$PLATFORM",
  "flow_variant": "$FLOW_VARIANT",
  "config_mk": "$CONFIG_MK",
  "sdc_file": "$SDC_FILE",
  "make_status": $MAKE_STATUS,
  "orfs_results": "$RESULTS_DIR",
  "orfs_logs": "$LOGS_DIR"
}
METAEOF

if [[ $MAKE_STATUS -eq 0 ]]; then
  echo ""
  echo "ORFS run completed successfully: $RUN_TAG"
  echo "Results: $BACKEND_DIR"
else
  echo ""
  echo "ORFS run FAILED (exit code $MAKE_STATUS): $RUN_TAG"
  echo "Check logs: $BACKEND_DIR/flow.log"
fi

exit $MAKE_STATUS
