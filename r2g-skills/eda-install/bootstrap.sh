#!/usr/bin/env bash
# One-command EDA-toolchain bootstrap for the r2g-skills collection.
#
#   detect → plan → install (missing tiers) → pin env.local.sh → verify
#
# The companion to install.sh: install.sh deploys the two *skills* into
# .claude/skills/; this provisions the *toolchain* they drive (ORFS + openroad/
# yosys, iverilog, klayout, magic/netgen, the sky130A PDK, the torch venv).
#
# Design + rationale: docs/superpowers/plans/r2g-skills-bootstrap-2026-07-08.md.
#
# The channel is auto-selected by whether you have root:
#   HAVE_SUDO=1 → may build ORFS from source / use the system package manager
#   HAVE_SUDO=0 → the DEFAULT here: every tier comes from pre-built conda
#                 (litex-hub) + a venv, all under a big volume ($HOME is never
#                 filled). No privilege escalation, nothing built.
#
# This first slice ships detection + the plan (--dry-run) + the env.local.sh pin
# generator + the verify hand-off. The heavy per-tier installers are invoked when
# present (scripts/setup/install_<tier>.sh); until they land, a missing tier prints
# the exact command it WOULD run and points at the plan doc — honest, never silent.
set -uo pipefail

PLUGIN_NAME="r2g-skills"
SKILL_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"   # …/r2g-skills/eda-install
SETUP_DIR="$SKILL_DIR/scripts/setup"
FLOW_DIR_SH="$SKILL_DIR/scripts/flow"                             # check_env.sh lives here
COLLECTION_DIR="$(cd -- "$SKILL_DIR/.." && pwd)"                  # …/r2g-skills (install.sh, sibling skills)

CONDA_ENV="eda"
CONDA_CH="--override-channels -c litex-hub -c conda-forge"   # ToS-gate workaround
GRAPH_VENV_SUBPATH="pyenvs/r2g-graph"

# ---- args --------------------------------------------------------------------
do_dry=0; do_yes=0; prefix=""; graph_python=""; plan_from=""; tiers_arg=""
do_deploy=0; deploy_link=0; min_free=""

print_help() {
  cat <<EOF
Provision the EDA toolchain for ${PLUGIN_NAME} (detect → plan → install → pin → verify).

Usage:
  $(basename "$0") [--dry-run] [--yes] [--prefix DIR] [--tiers a,b,c]
                   [--graph-python PATH] [--min-free-gb N]
                   [--deploy [--link]]
  $(basename "$0") --plan-from FILE          # print the plan for a saved detect dump (implies --dry-run)

Options:
  --dry-run          Detect + print the plan table, install nothing.
  --yes, -y          Non-interactive: accept the plan (incl. heavy --yes-gated tiers).
  --prefix DIR       Big-volume root for the conda install, PDK, and torch venv
                     (default: first writable dir with >= min-free-gb, preferring /proj).
  --tiers LIST       Comma-separated subset to act on (core,frontend,sky130,klayout,pdk,graph).
  --graph-python P   A python that already has torch+torch_geometric+pandas (pins R2G_GRAPH_PYTHON).
  --min-free-gb N    Free-space threshold for the big-volume picker (default 15).
  --plan-from FILE   Use a saved 'detect_env.sh' KEY=VALUE dump instead of probing (for review/tests).
  --deploy [--link]  After provisioning, run install.sh to deploy the skills (--link recommended).
  -h, --help         Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)      do_dry=1; shift ;;
    --yes|-y)       do_yes=1; shift ;;
    --prefix)       prefix="${2:-}"; shift 2 ;;
    --tiers)        tiers_arg="${2:-}"; shift 2 ;;
    --graph-python) graph_python="${2:-}"; shift 2 ;;
    --min-free-gb)  min_free="${2:-}"; shift 2 ;;
    --plan-from)    plan_from="${2:-}"; do_dry=1; shift 2 ;;
    --deploy)       do_deploy=1; shift ;;
    --link)         deploy_link=1; shift ;;
    -h|--help)      print_help; exit 0 ;;
    *) echo "unknown arg: $1" >&2; print_help >&2; exit 2 ;;
  esac
done

# ---- detect ------------------------------------------------------------------
[[ -n "$prefix" ]]       && export R2G_PREFIX="$prefix"
[[ -n "$graph_python" ]] && export R2G_GRAPH_PYTHON="$graph_python"
[[ -n "$min_free" ]]     && export R2G_MIN_FREE_GB="$min_free"

if [[ -n "$plan_from" ]]; then
  [[ -f "$plan_from" ]] || { echo "error: --plan-from file not found: $plan_from" >&2; exit 2; }
  DETECT_OUT="$(cat -- "$plan_from")"
else
  DETECT_OUT="$(bash "$SETUP_DIR/detect_env.sh" 2>/dev/null)"
fi

declare -A DET
while IFS='=' read -r _k _v; do
  [[ -z "$_k" ]] && continue
  DET["$_k"]="$_v"
done <<< "$DETECT_OUT"
d() { echo "${DET[$1]:-}"; }

SUDO="$(d HAVE_SUDO)"; SUDO="${SUDO:-0}"
BIGV="$(d BIG_VOLUME)"
CONDA="$(d HAVE_CONDA)"
PKG="$(d PKG_MGR)"

# ---- tier evaluation ---------------------------------------------------------
# For each tier -> STATUS (OK|MISS|OPT) + ACTION (what install would do).
#   OK  = satisfied      MISS = required & unsatisfied      OPT = optional & installable
CONDA_ROOT="${BIGV:-\$BIG_VOLUME}/miniconda3"
_conda_bin="${CONDA:-$CONDA_ROOT/bin/conda}"
_graph_venv="${BIGV:-\$BIG_VOLUME}/$GRAPH_VENV_SUBPATH"

# returns via globals TIER_STATUS / TIER_ACTION
eval_tier() {
  local tier="$1"
  TIER_STATUS="OPT"; TIER_ACTION=""
  case "$tier" in
    core)
      if [[ -n "$(d ORFS_ROOT)" && -n "$(d OPENROAD_EXE)" && -n "$(d YOSYS_EXE)" ]]; then
        TIER_STATUS="OK"; TIER_ACTION="present"
      else
        TIER_STATUS="MISS"
        if [[ "$SUDO" == "1" ]]; then
          TIER_ACTION="clone ORFS + build_openroad.sh --local (~30min; needs --yes)"
        else
          TIER_ACTION="clone ORFS (no build) + '$_conda_bin' -n $CONDA_ENV openroad yosys"
        fi
      fi ;;
    frontend)
      if [[ -n "$(d IVERILOG_EXE)" && -n "$(d VVP_EXE)" ]]; then
        TIER_STATUS="OK"; TIER_ACTION="present"
      else
        TIER_STATUS="MISS"
        if [[ "$SUDO" == "1" && "$PKG" != "none" ]]; then
          TIER_ACTION="$PKG install iverilog verilator"
        else
          TIER_ACTION="conda -n $CONDA_ENV iverilog verilator"
        fi
      fi ;;
    sky130)
      if [[ -n "$(d MAGIC_EXE)" && -n "$(d NETGEN_EXE)" ]]; then
        TIER_STATUS="OK"; TIER_ACTION="present"
      else
        TIER_STATUS="OPT"; TIER_ACTION="conda -n $CONDA_ENV magic netgen"
      fi ;;
    klayout)
      if [[ -n "$(d KLAYOUT_CMD)" ]]; then
        TIER_STATUS="OK"; TIER_ACTION="present"
      else
        TIER_STATUS="OPT"; TIER_ACTION="conda -n $CONDA_ENV klayout"
      fi ;;
    pdk)
      if [[ -n "$(d SKY130A_DIR)" ]]; then
        TIER_STATUS="OK"; TIER_ACTION="present ($(d SKY130A_DIR))"
      else
        TIER_STATUS="OPT"; TIER_ACTION="conda -n $CONDA_ENV open_pdks.sky130a -> $CONDA_ROOT/envs/$CONDA_ENV/share/pdk"
      fi ;;
    graph)
      if [[ -n "$(d GRAPH_PYTHON)" ]]; then
        TIER_STATUS="OK"; TIER_ACTION="present ($(d GRAPH_PYTHON))"
      else
        TIER_STATUS="OPT"; TIER_ACTION="venv+pip torch(cpu)+torch_geometric+pandas -> $_graph_venv"
      fi ;;
    platform_rules)
      # Strict-signoff capability for the default full-flow platform (round-2
      # pilot P0-3): a stock nangate45 ORFS checkout ships NO LVS deck and an
      # unusable zero-diff-area antenna diode, so a green tool table coexists
      # with an impossible strict signoff — discovered only after multi-hour
      # flows. Probe via the sibling skill's platform_capability.py; the
      # installer materializes the repo's bundled DRC/LVS/antenna decks.
      local _cap="$COLLECTION_DIR/signoff-loop/scripts/flow/platform_capability.py"
      if [[ -z "$(d ORFS_ROOT)" || ! -f "$_cap" ]]; then
        TIER_STATUS="OPT"; TIER_ACTION="bundled nangate45 DRC/LVS/antenna decks -> ORFS (install ORFS core first)"
      elif python3 "$_cap" --flow-dir "$(d ORFS_ROOT)/flow" --platform nangate45 --strict >/dev/null 2>&1; then
        TIER_STATUS="OK"; TIER_ACTION="nangate45 strict-signoff capable (DRC/LVS decks + usable antenna model)"
      else
        TIER_STATUS="OPT"; TIER_ACTION="install bundled nangate45 DRC/LVS/antenna decks (install_platform_rules.sh) — strict signoff impossible until then"
      fi ;;
    *) TIER_STATUS="?"; TIER_ACTION="unknown tier" ;;
  esac
}

ALL_TIERS=(core frontend sky130 klayout pdk platform_rules graph)
tier_need() { case "$1" in core|frontend) echo req ;; *) echo opt ;; esac; }

# Restrict to --tiers if given.
SELECTED=("${ALL_TIERS[@]}")
if [[ -n "$tiers_arg" ]]; then
  IFS=',' read -r -a SELECTED <<< "$tiers_arg"
fi

# ---- plan table --------------------------------------------------------------
print_preamble() {
  echo "== ${PLUGIN_NAME} toolchain plan =="
  printf '  os=%s  pkg=%s  sudo=%s  conda=%s\n' \
    "$(d OS_FAMILY)" "$PKG" "$([[ "$SUDO" == 1 ]] && echo yes || echo NO)" \
    "${CONDA:-none}"
  printf '  big-volume=%s (%s GB free)  channel=%s\n' \
    "${BIGV:-<none: pass --prefix>}" "$(d BIG_VOLUME_FREE_GB)" \
    "$([[ "$SUDO" == 1 ]] && echo 'sudo/build (or conda)' || echo 'conda litex-hub (no-sudo)')"
  echo
  printf '%-11s %-6s %-4s %s\n' "tier" "status" "need" "action"
  printf '%-11s %-6s %-4s %s\n' "-----------" "------" "----" "----------------------------------------"
}

MISSING_REQUIRED=0
print_plan() {
  print_preamble
  local t
  for t in "${SELECTED[@]}"; do
    eval_tier "$t"
    local need; need="$(tier_need "$t")"
    printf '%-11s %-6s %-4s %s\n' "$t" "$TIER_STATUS" "$need" "$TIER_ACTION"
    [[ "$TIER_STATUS" == "MISS" ]] && MISSING_REQUIRED=$((MISSING_REQUIRED+1))
  done
  echo
}

print_plan

# ---- dry-run stops here ------------------------------------------------------
if [[ "$do_dry" == "1" ]]; then
  if [[ "$MISSING_REQUIRED" -gt 0 ]]; then
    echo "Plan: ${MISSING_REQUIRED} required tier(s) missing. Re-run without --dry-run to install."
  else
    echo "Plan: all required tiers satisfied. Optional 'OPT' tiers install on request."
  fi
  echo "(dry run — nothing installed)"
  exit 0
fi

# ---- confirm ------------------------------------------------------------------
if [[ "$do_yes" != "1" ]]; then
  if [[ ! -t 0 ]]; then
    echo "Non-interactive and no --yes: refusing to install. Re-run with --yes or --dry-run." >&2
    exit 3
  fi
  printf 'Proceed with the plan above? [y/N]: '
  read -r _ans
  case "${_ans:-N}" in y|Y|yes|YES) : ;; *) echo "aborted."; exit 0 ;; esac
fi

# ---- install missing tiers ----------------------------------------------------
# Dispatch to scripts/setup/install_<tier>.sh when present; otherwise print the
# planned command and a pointer (heavy installers land in a later slice).
# Build optional-flag arrays once (guard against the ${var:+…}-on-"0" pitfall).
YES_FLAG=();  [[ "$do_yes" == "1" ]]      && YES_FLAG=(--yes)
LINK_FLAG=(); [[ "$deploy_link" == "1" ]] && LINK_FLAG=(--link)
GP_FLAG=();   [[ -n "$graph_python" ]]     && GP_FLAG=(--graph-python "$graph_python")

run_tier() {
  local t="$1" script="$SETUP_DIR/install_$1.sh"
  eval_tier "$t"
  [[ "$TIER_STATUS" == "OK" ]] && { echo "[$t] already satisfied — skip"; return 0; }
  if [[ -x "$script" || -f "$script" ]]; then
    echo "[$t] running $(basename "$script") ..."
    R2G_PREFIX="${BIGV}" R2G_CONDA_ENV="$CONDA_ENV" bash "$script" "${YES_FLAG[@]}" || {
      echo "[$t] installer returned non-zero (tier left unsatisfied)" >&2; return 1; }
  else
    echo "[$t] no installer script yet — would run: $TIER_ACTION" >&2
    echo "     (see docs/superpowers/plans/r2g-skills-bootstrap-2026-07-08.md)" >&2
    return 1
  fi
}

install_rc=0
for t in "${SELECTED[@]}"; do
  need="$(tier_need "$t")"
  eval_tier "$t"
  # Only auto-install required-missing tiers; optional tiers install when named via --tiers.
  if [[ "$TIER_STATUS" == "OK" ]]; then continue; fi
  if [[ "$need" == "opt" && -z "$tiers_arg" ]]; then
    echo "[$t] optional and not requested (add to --tiers to install) — skip"
    continue
  fi
  run_tier "$t" || install_rc=1
done

# ---- pin env.local.sh --------------------------------------------------------
if [[ -x "$SETUP_DIR/write_env_local.sh" || -f "$SETUP_DIR/write_env_local.sh" ]]; then
  echo
  echo "== pinning references/env.local.sh (both skills) =="
  bash "$SETUP_DIR/write_env_local.sh" "${GP_FLAG[@]}" || \
    echo "warning: env.local.sh pin step failed" >&2
fi

# ---- verify ------------------------------------------------------------------
echo
echo "== verify =="
bash "$FLOW_DIR_SH/check_env.sh" || true

# ---- optional skill deploy ---------------------------------------------------
if [[ "$do_deploy" == "1" ]]; then
  echo
  echo "== deploying skills =="
  bash "$COLLECTION_DIR/install.sh" --user "${LINK_FLAG[@]}" --force || \
    echo "warning: install.sh (deploy) failed" >&2
fi

exit "$install_rc"
