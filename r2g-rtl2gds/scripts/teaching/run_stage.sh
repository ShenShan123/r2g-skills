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
#   DEF_PATH / ODB_PATH / SPEF_PATH
#                   stage4 only: real artifact paths. If unset, they are read
#                   from CASE_STATE.md and placeholder-expanded (see stage4()).
#                   Pass these to decouple "what the tools open" (real path) from
#                   "what CASE_STATE records" (policy §3 placeholders).
#   TECH_LEF        stage4 only: nangate tech LEF path (optional)
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

log()  { printf '[run_stage] %s\n' "$*" >&2; }
die()  { printf '[run_stage][ERROR] %s\n' "$*" >&2; exit 1; }

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

# ─── validate design name (single source of truth, NO path-based fallback) ───
# The design name is whatever the caller passes as $2 — there is deliberately no
# basename()/dirname() inference here. These checks reject the names that pollute
# cases/<design>/ dirs (slashes, spaces, illegal chars) and warn on names that
# look like a flattened RTL path (e.g. "..._rtl_omsp_sfr") rather than a top
# module name. A flattened name (slashes already collapsed to '_' upstream) is a
# legal identifier and cannot be detected for sure — hence a WARNING, not a hard
# stop. The real fix is to pass the top-level design name in the stage prompt.
case "$DESIGN" in
  */*|*\\*)      die "design name 不能含路径分隔符: '$DESIGN'（应传顶层设计名，而不是 RTL 目录路径）" ;;
  *[[:space:]]*) die "design name 不能含空白字符: '$DESIGN'" ;;
  ""|.|..)       die "design name 非法: '$DESIGN'" ;;
esac
if printf '%s' "$DESIGN" | LC_ALL=C grep -q '[^A-Za-z0-9._-]'; then
  die "design name 含非法字符（仅允许 A-Za-z0-9._-）: '$DESIGN'"
fi
if [ "${#DESIGN}" -gt 40 ] || printf '%s' "$DESIGN" | grep -qiE '_rtl_|_rtl$'; then
  log "WARNING: design name 看着像从路径拍平来的（'$DESIGN'）。"
  log "WARNING: 确认这是顶层设计名（如 omsp_sfr），而不是 RTL 目录名。可在 stage prompt 里显式给短名。"
fi

PROJECT_DIR="$SKILL_DIR/design_cases/$DESIGN"          # SKILL.md工程目录
CASE_DIR="$TEACHING_ROOT/cases/$DESIGN"                # 教学产物目录
mkdir -p "$CASE_DIR"

DRY_RUN="${DRY_RUN:-0}"

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

# Expand teaching placeholders + strip surrounding whitespace, so a policy-compliant
# CASE_STATE (which should store <repo>/<case_root>/<teaching_root> placeholders, NOT
# machine-absolute paths per §3) still yields a real path the EDA tools can open.
# Also fixes the OpenMSP430-class bug where a trailing space on the SPEF path made
# the file check fail and C_total collapsed to 0.
expand_path() {
  local p="$1"
  # strip leading/trailing whitespace
  p="${p#"${p%%[![:space:]]*}"}"
  p="${p%"${p##*[![:space:]]}"}"
  [ -z "$p" ] && { printf ''; return 0; }
  p="${p//<repo>/$REPO_ROOT}"
  p="${p//<case_root>/$CASE_DIR}"
  p="${p//<teaching_root>/$TEACHING_ROOT}"
  printf '%s' "$p"
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
    "$CASE_DIR/stage2_orfs/6_final.*,$PROJECT_DIR/backend/RUN_*/results/*" -- \
    env TEACHING_ROOT="$TEACHING_ROOT" TEACHING_CASE_NAME="$DESIGN" \
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
    "$PROJECT_DIR/backend/RUN_*/final/*.odb" "$CASE_DIR/stage3_drc_lvs_rcx/6_final.spef" -- \
    env TEACHING_ROOT="$TEACHING_ROOT" TEACHING_CASE_NAME="$DESIGN" \
    bash "$SKILL_DIR/scripts/flow/run_rcx.sh" "$PROJECT_DIR" nangate45  # >>> FLOW
  log "Stage 3 flow done. Write STAGE3 report with DRC/LVS/RCX sub-statuses."
}

stage4() {
  log "Stage 4: labels (Part A) + graph features (Part B) ($DESIGN)"
  mkdir -p "$CASE_DIR/stage4_labels" "$CASE_DIR/stage4_features"

  LABEL_ROOT="$SKILL_DIR/scripts/extract/labels"
  FEATURE_ROOT="$SKILL_DIR/scripts/extract/features"
  LABELS_OUT="$CASE_DIR/stage4_labels"

  # Resolve real artifact paths. Priority: env override -> CASE_STATE.md value.
  # Either way the value is run through expand_path() so:
  #   * policy-compliant placeholders (<repo>/<case_root>/<teaching_root>) become
  #     real paths the tools can open — decoupling "tool input" from "what §3
  #     allows CASE_STATE to record";
  #   * a stray trailing space (the OpenMSP430 SPEF / C_total=0 bug) is stripped.
  cs="$CASE_DIR/CASE_STATE.md"
  get_cs() { [ -f "$cs" ] && sed -n "s/^$1:[[:space:]]*//p" "$cs" | tail -1; }
  DEF_PATH="$(expand_path "${DEF_PATH:-$(get_cs def_path)}")"
  ODB_PATH="$(expand_path "${ODB_PATH:-$(get_cs odb_path)}")"
  SPEF_PATH="$(expand_path "${SPEF_PATH:-$(get_cs spef_path)}")"
  TECH_LEF="${TECH_LEF:-}"   # set if you have the nangate tech LEF path handy

  # Guard the resolved paths before handing them to EDA tools. This converts the
  # silent "fed a literal <repo>/... placeholder to openroad -> empty output"
  # failure into a loud, actionable error. Skipped under DRY_RUN.
  if [ "$DRY_RUN" != "1" ]; then
    for pair in "DEF_PATH:$DEF_PATH" "ODB_PATH:$ODB_PATH"; do
      name="${pair%%:*}"; val="${pair#*:}"
      [ -n "$val" ] || die "stage4: $name 为空。请在 CASE_STATE.md 写明（占位符即可，如 <case_root>/...），或运行时用 env $name=/abs/path 传入。"
      case "$val" in
        *'<'*'>'*) die "stage4: $name 仍含未展开占位符: '$val'。已知占位符仅 <repo>/<case_root>/<teaching_root>；其余请用 env $name=/abs/path 传入真实路径。" ;;
      esac
      [ -e "$val" ] || die "stage4: $name 指向的文件不存在: '$val'。确认 stage2/3 产物在位，或用 env $name=/abs/path 覆盖。"
    done
    # SPEF is optional (RCX may be absent); never hard-fail, only warn.
    if [ -n "$SPEF_PATH" ]; then
      case "$SPEF_PATH" in
        *'<'*'>'*) log "WARNING: SPEF_PATH 仍含未展开占位符: '$SPEF_PATH'（将原样传给 run_features.sh）" ;;
      esac
      [ -e "$SPEF_PATH" ] || log "WARNING: SPEF_PATH 不存在: '$SPEF_PATH'（feature 提取将在无 SPEF 下进行，C_total 可能为 0）"
    fi
  fi

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
  # DEF/SPEF are pinned to the same artifacts Part A used (resolved above) so
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
