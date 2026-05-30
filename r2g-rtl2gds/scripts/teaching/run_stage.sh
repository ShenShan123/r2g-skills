#!/usr/bin/env bash
# run_stage.sh — unified teaching stage runner (TEACHING_POLICY §4)
#
#   bash run_stage.sh <stage> <design> [rtl_path]
#       <stage>    = 1 | 2 | 3 | 4
#       <design>   = design name (used for cases/<design> and design_cases/<design>)
#       [rtl_path] = original RTL dir (Stage 1 first run only)
#
# Env:
#   TEACHING_ROOT   dir containing TEACHING_POLICY.md (default: auto-detected upward)
#   REPO_ROOT       agent-r2g repo root (default: 3 levels up from this script)
#   AGENT_BACKEND   e.g. "codex/gpt-5.5" (recorded into the ledger)
#   DRY_RUN=1       print the flow commands instead of running EDA tools
#                   (ledger writes still happen so you can inspect them)
#
# What this script guarantees, regardless of tool outcome:
#   * every flow step it runs gets ONE ledger record (via append_ledger.py)
#   * the ledger is written by THIS script, never by the agent (policy §2.9)
#   * paths recorded are normalized by append_ledger.py
#
# WHERE TO PLUG IN: lines marked  # >>> FLOW  call the real SKILL flow scripts.
# They already match SKILL.md's documented signatures; adjust only if your
# repo's script names differ.

set -uo pipefail

# ─── resolve roots ───────────────────────────────────────────────────────────
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "$SELF/../../.." && pwd)}"     # scripts/teaching -> repo
SKILL_DIR="$REPO_ROOT/r2g-rtl2gds"
LEDGER="$SELF/../ledger/append_ledger.py"

detect_teaching_root() {
  local d="${TEACHING_ROOT:-$PWD}"
  while [ "$d" != "/" ]; do
    [ -f "$d/TEACHING_POLICY.md" ] && { echo "$d"; return 0; }
    d="$(dirname "$d")"
  done
  echo "${TEACHING_ROOT:-$PWD}"
}
TEACHING_ROOT="$(detect_teaching_root)"

# Hard guard: if detect_teaching_root fell back to $PWD (no TEACHING_POLICY.md found
# upward), CASE_DIR would become "$PWD/cases/<design>" and stage4 would mkdir empty
# cases/ dirs in the wrong place. Fail loudly instead of polluting the filesystem.
if [ ! -f "$TEACHING_ROOT/TEACHING_POLICY.md" ]; then
  printf '[run_stage][ERROR] TEACHING_POLICY.md not found upward from PWD=%s.\n' "$PWD" >&2
  printf '[run_stage][ERROR] Run from inside the teaching workspace, or: export TEACHING_ROOT=/path/to/r2g_teaching\n' >&2
  exit 1
fi

STAGE="${1:?usage: run_stage.sh <stage> <design> [rtl_path]}"
DESIGN="${2:?usage: run_stage.sh <stage> <design> [rtl_path]}"
RTL_PATH="${3:-}"

PROJECT_DIR="$SKILL_DIR/design_cases/$DESIGN"          # SKILL.md工程目录
CASE_DIR="$TEACHING_ROOT/cases/$DESIGN"                # 教学产物目录
mkdir -p "$CASE_DIR"

DRY_RUN="${DRY_RUN:-0}"

log()  { printf '[run_stage] %s\n' "$*" >&2; }
die()  { printf '[run_stage][ERROR] %s\n' "$*" >&2; exit 1; }

# ─── ledger helper: record one flow step ─────────────────────────────────────
# usage: ledger_record <stage_str> <step> <cmd> <inputs_glob> <outputs_glob> <start> <end> <rc>
ledger_record() {
  local stage_str="$1" step="$2" cmd="$3" in_glob="$4" out_glob="$5" start="$6" end="$7" rc="$8"
  python3 "$LEDGER" \
    --teaching-root "$TEACHING_ROOT" \
    --repo-root     "$REPO_ROOT" \
    --design        "$DESIGN" \
    --stage         "$stage_str" \
    --step          "$step" \
    --command       "$cmd" \
    --inputs-glob   "$in_glob" \
    --outputs-glob  "$out_glob" \
    --start-ts      "$start" \
    --end-ts        "$end" \
    --exit-code     "$rc" \
    --triggered-by  "flow_script" \
    ${AGENT_BACKEND:+--agent-backend "$AGENT_BACKEND"} \
    >/dev/null || log "WARNING: ledger append failed for step=$step"
}

now() { date -u +%FT%TZ; }

# run a flow command, time it, and record it to the ledger
# usage: run_step <stage_str> <step> <inputs_glob> <outputs_glob> -- <command...>
run_step() {
  local stage_str="$1" step="$2" in_glob="$3" out_glob="$4"; shift 4
  [ "$1" = "--" ] && shift
  local start end rc; start="$(now)"
  if [ "$DRY_RUN" = "1" ]; then
    log "DRY_RUN step=$step: $*"
    rc=0
  else
    "$@"; rc=$?
  fi
  end="$(now)"
  ledger_record "$stage_str" "$step" "$*" "$in_glob" "$out_glob" "$start" "$end" "$rc"
  return $rc
}

# ─── stages ──────────────────────────────────────────────────────────────────
stage1() {
  log "Stage 1: RTL -> lint -> sim -> synth ($DESIGN)"
  [ -n "$RTL_PATH" ] || log "no rtl_path given; assuming project already initialized"
  # >>> FLOW: init + prepare inputs (init_project copies layout; you populate rtl/tb)
  if [ -n "$RTL_PATH" ] && [ "$DRY_RUN" != "1" ]; then
    python3 "$SKILL_DIR/scripts/project/init_project.py" "$DESIGN" || true
  fi
  run_step stage1 lint \
    "$PROJECT_DIR/rtl/*.v" "$PROJECT_DIR/lint/lint.log" -- \
    bash "$SKILL_DIR/scripts/flow/run_lint.sh" "$PROJECT_DIR"            # >>> FLOW
  run_step stage1 simulation \
    "$PROJECT_DIR/tb/*.v" "$PROJECT_DIR/sim/sim.log" -- \
    bash "$SKILL_DIR/scripts/flow/run_sim.sh" "$PROJECT_DIR"             # >>> FLOW
  run_step stage1 synthesis \
    "$PROJECT_DIR/rtl/*.v,$PROJECT_DIR/constraints/config.mk" \
    "$PROJECT_DIR/synth/synth_output.v,$PROJECT_DIR/synth/synth.log" -- \
    bash "$SKILL_DIR/scripts/flow/run_synth.sh" "$PROJECT_DIR"           # >>> FLOW
  log "Stage 1 flow done. Agent: write STAGE1 report + CASE_STATE per policy §5/§7."
}

stage2() {
  log "Stage 2: synth -> ORFS -> GDS/DEF/ODB ($DESIGN)"
  run_step stage2 orfs_backend \
    "$PROJECT_DIR/constraints/config.mk,$PROJECT_DIR/constraints/constraint.sdc" \
    "$PROJECT_DIR/backend/RUN_*/final/*,$PROJECT_DIR/backend/RUN_*/results/*" -- \
    bash "$SKILL_DIR/scripts/flow/run_orfs.sh" "$PROJECT_DIR"            # >>> FLOW
  run_step stage2 timing_check \
    "$PROJECT_DIR/reports/ppa.json" "$PROJECT_DIR/reports/timing_check.json" -- \
    python3 "$SKILL_DIR/scripts/reports/check_timing.py" "$PROJECT_DIR" # >>> FLOW
  log "Stage 2 flow done. Handle timing tier per SKILL.md 5b; write STAGE2 report."
}

stage3() {
  log "Stage 3: post-GDS DRC/LVS/RCX ($DESIGN)"
  run_step stage3 drc_klayout \
    "$PROJECT_DIR/backend/RUN_*/final/*.gds" "$PROJECT_DIR/drc/*" -- \
    bash "$SKILL_DIR/scripts/flow/run_drc.sh" "$PROJECT_DIR" nangate45  # >>> FLOW
  run_step stage3 lvs_klayout \
    "$PROJECT_DIR/backend/RUN_*/final/*.gds" "$PROJECT_DIR/lvs/*" -- \
    bash "$SKILL_DIR/scripts/flow/run_lvs.sh" "$PROJECT_DIR" nangate45  # >>> FLOW
  run_step stage3 rcx_openrcx \
    "$PROJECT_DIR/backend/RUN_*/final/*.odb" "$PROJECT_DIR/rcx/6_final.spef" -- \
    bash "$SKILL_DIR/scripts/flow/run_rcx.sh" "$PROJECT_DIR" nangate45  # >>> FLOW
  log "Stage 3 flow done. Write STAGE3 report with DRC/LVS/RCX sub-statuses."
}

stage4() {
  log "Stage 4: labels (Part A) + graph features (Part B) ($DESIGN)"
  mkdir -p "$CASE_DIR/stage4_labels" "$CASE_DIR/stage4_features"

  LABEL_ROOT="$SKILL_DIR/scripts/extract/labels"
  FEATURE_ROOT="$SKILL_DIR/scripts/extract/features"
  LABELS_OUT="$CASE_DIR/stage4_labels"

  # Resolve real artifact paths from CASE_STATE.md (def_path / odb_path / spef_path).
  cs="$CASE_DIR/CASE_STATE.md"
  get_cs() { [ -f "$cs" ] && sed -n "s/^$1:[[:space:]]*//p" "$cs" | tail -1; }
  DEF_PATH="${DEF_PATH:-$(get_cs def_path)}"
  ODB_PATH="${ODB_PATH:-$(get_cs odb_path)}"
  SPEF_PATH="${SPEF_PATH:-$(get_cs spef_path)}"
  TECH_LEF="${TECH_LEF:-}"   # set if you have the nangate tech LEF path handy

  # The four label scripts FORCE the canonical basename, so even if these paths
  # were wrong-named the output still lands correctly. We pass canonical names
  # anyway for clarity.  Each call goes through run_step -> ledger.
  run_step stage4 label_wirelength \
    "$DEF_PATH" "$LABELS_OUT/wirelength.csv" -- \
    python3 "$LABEL_ROOT/extract_wirelength.py" "$DEF_PATH" "$LABELS_OUT/wirelength.csv" "$DESIGN"   # >>> FLOW

  run_step stage4 label_congestion \
    "$DEF_PATH" "$LABELS_OUT/cell_congestion.csv" -- \
    env ${TECH_LEF:+TECH_LEF="$TECH_LEF"} \
    python3 "$LABEL_ROOT/extract_congestion.py" "$DEF_PATH" "$LABELS_OUT/cell_congestion.csv" "$DESIGN"  # >>> FLOW

  run_step stage4 label_timing \
    "$ODB_PATH,$DEF_PATH" "$LABELS_OUT/timing_features.csv" -- \
    env OUTPUT_CSV="$LABELS_OUT/timing_features.csv" ODB_FILE="$ODB_PATH" \
        DEF_FILE="$DEF_PATH" DESIGN_NAME="$DESIGN" \
        openroad "$LABEL_ROOT/extract_timing.tcl"                       # >>> FLOW

  run_step stage4 label_irdrop \
    "$ODB_PATH,$DEF_PATH" "$LABELS_OUT/ir_drop.csv" -- \
    env OUTPUT_RPT="$LABELS_OUT/ir_drop.csv" ODB_FILE="$ODB_PATH" \
        DEF_FILE="$DEF_PATH" DESIGN_NAME="$DESIGN" \
        openroad "$LABEL_ROOT/extract_irdrop.tcl"                       # >>> FLOW

  # --- Part B: graph features ---------------------------------------------
  # run_features.sh writes the 8 feature CSVs to $R2G_FEATURES_DIR and the stats
  # JSON to $R2G_REPORTS_DIR. We point both straight at cases/<design>/stage4_features
  # so there is NO output/<case> staging and NO second copy — the workers' out_csv
  # (case_paths.py) lands the CSVs in the canonical location directly.
  #
  # DEF/SPEF are pinned to the same artifacts Part A used (from CASE_STATE.md) so
  # run_features.sh does not auto-discover a different backend RUN_*. Platform is
  # locked to nangate45 (TEACHING_POLICY red line 3).
  FEATURES_OUT="$CASE_DIR/stage4_features"
  run_step stage4 feature_extract \
    "$DEF_PATH" "$FEATURES_OUT/*.csv,$FEATURES_OUT/features_stats.json" -- \
    env R2G_FEATURES_DIR="$FEATURES_OUT" \
        R2G_REPORTS_DIR="$FEATURES_OUT" \
        ${DEF_PATH:+R2G_DEF="$DEF_PATH"} \
        ${SPEF_PATH:+R2G_SPEF="$SPEF_PATH"} \
        bash "$SKILL_DIR/scripts/flow/run_features.sh" "$PROJECT_DIR" nangate45   # >>> FLOW

  log "Stage 4 flow done (Part A labels -> $LABELS_OUT, Part B features -> $FEATURES_OUT)."
  log "Agent: write STAGE4 report + update CASE_STATE per policy §6/§7."
}

case "$STAGE" in
  1) stage1 ;;
  2) stage2 ;;
  3) stage3 ;;
  4) stage4 ;;
  *) die "invalid stage: $STAGE (expected 1|2|3|4)" ;;
esac

log "done. ledger at: $TEACHING_ROOT/run_ledger.jsonl"
