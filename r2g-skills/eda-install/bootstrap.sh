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
# The preferred channel is a direct user-owned bundle under R2G_PREFIX. A
# legacy conda/venv path remains available for operators who do not request
# --direct; direct mode is fail-closed and never installs or activates conda.
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
do_dry=0; do_yes=0; do_hermetic=0; do_direct=0; prefix=""; graph_python=""; plan_from=""; tiers_arg=""
do_deploy=0; deploy_link=0; min_free=""; strict_platforms="${R2G_STRICT_PLATFORMS:-}"

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
  --hermetic         Require every selected tool/PDK/graph path to be user-owned
                     (never /usr or /opt) and install all tiers by default.
  --direct           Use only the direct user-owned bundle; refuse conda fallback.
  --prefix DIR       Big-volume root for the direct tool bundle, PDK, and torch venv
                     (default: first writable dir with >= min-free-gb, preferring /proj).
  --tiers LIST       Comma-separated subset to act on (core,frontend,sky130,klayout,pdk,graph).
  --strict-platforms LIST
                     Comma-separated platforms that MUST come out strict-signoff
                     capable (e.g. nangate45,sky130hd,sky130hs). Makes the
                     platform_rules tier REQUIRED and FAIL-CLOSED: a missing rule
                     installer, failed postcondition/canary, or failed
                     platform_capability --strict check fails the bootstrap
                     (RMD2-P1-01). Unselected platforms stay best-effort.
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
    --hermetic)     do_hermetic=1; shift ;;
    --direct|--no-conda) do_direct=1; do_hermetic=1; shift ;;
    --prefix)       prefix="${2:-}"; shift 2 ;;
    --tiers)        tiers_arg="${2:-}"; shift 2 ;;
    --strict-platforms) strict_platforms="${2:-}"; shift 2 ;;
    --graph-python) graph_python="${2:-}"; shift 2 ;;
    --min-free-gb)  min_free="${2:-}"; shift 2 ;;
    --plan-from)    plan_from="${2:-}"; do_dry=1; shift 2 ;;
    --deploy)       do_deploy=1; shift ;;
    --link)         deploy_link=1; shift ;;
    -h|--help)      print_help; exit 0 ;;
    *) echo "unknown arg: $1" >&2; print_help >&2; exit 2 ;;
  esac
done

# ---- resolve the canonical toolchain BEFORE detect/plan (RMD4-P1-01) ---------
# A repeat bootstrap must act on the SAME installation production flows use. This
# resolves one canonical selection — explicit operator choice > agreeing deployed
# consumer pins > fresh autodetection — and fails closed when two live pins
# disagree. Without it, detect_env.sh resolves through eda-install's own copy of
# _env.sh, which has no pin file and falls through to the hardcoded candidate list
# (/opt before /proj): the 2026-07-27 pilot planned and installed against a
# different ORFS + PDK than every production run used.
declare -A SEL
# --plan-from deliberately supplies its own machine description (review/tests), so
# it bypasses live pin resolution entirely.
if [[ -z "$plan_from" ]]; then
  PIN_SEL_OUT="$(bash "$SETUP_DIR/resolve_pins.sh" 2>/dev/null)"; PIN_SEL_RC=$?
  while IFS='=' read -r _k _v; do
    [[ -z "$_k" ]] && continue
    SEL["$_k"]="$_v"
  done <<< "$PIN_SEL_OUT"
  # Fail closed in EVERY mode, --dry-run included: a plan computed against an
  # ambiguous toolchain is exactly the misleading output this defect produced.
  if [[ "$PIN_SEL_RC" -eq 4 ]]; then
    echo "error: deployed skill pins disagree — ${SEL[PIN_CONFLICT_DETAIL]:-}" >&2
    echo "       Repeat bootstrap will not choose between two live installations." >&2
    echo "       Select one explicitly and re-run:" >&2
    echo "         export R2G_ENV_FILE=<r2g-skills/<skill>/references/env.local.sh>" >&2
    echo "       or export ORFS_ROOT=… PDK_ROOT=… ." >&2
    exit 4
  fi
  # Bind detection to the selection so plan, install, pin and verify all resolve
  # the SAME toolchain. An explicit R2G_ENV_FILE is never overwritten.
  if [[ -z "${R2G_ENV_FILE:-}" && -n "${SEL[SELECTED_ENV_FILE]:-}" ]]; then
    export R2G_ENV_FILE="${SEL[SELECTED_ENV_FILE]}"
  fi
  echo "== toolchain selection =="
  printf '  source=%s  orfs=%s\n  pdk=%s\n  env-file=%s\n\n' \
    "${SEL[SELECTION_SOURCE]:-autodetect}" "${SEL[SELECTED_ORFS_ROOT]:-<autodetect>}" \
    "${SEL[SELECTED_PDK_ROOT]:-<autodetect>}" "${R2G_ENV_FILE:-<none>}"
  [[ "${SEL[OVERRIDES_PINS]:-0}" == "1" ]] && \
    echo "  NOTE: this explicit selection OVERRIDES the agreeing deployed pins." >&2
fi

# ---- detect ------------------------------------------------------------------
[[ -n "$prefix" ]]       && export R2G_PREFIX="$prefix"
[[ "$do_hermetic" == "1" ]] && export R2G_HERMETIC=1
[[ "$do_direct" == "1" ]] && export R2G_DIRECT=1
[[ -n "$graph_python" ]] && export R2G_GRAPH_PYTHON="$graph_python"
[[ -n "$min_free" ]]     && export R2G_MIN_FREE_GB="$min_free"
# Normalize commas → spaces; exported so install_platform_rules.sh fail-closes
# on exactly the selected strict platforms (RMD2-P1-01).
strict_platforms="${strict_platforms//,/ }"
[[ -n "$strict_platforms" ]] && export R2G_STRICT_PLATFORMS="$strict_platforms"

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
DIRECT_ROOT="${R2G_TOOLCHAIN_ROOT:-${BIGV:-\$BIG_VOLUME}}"

# A host-wide executable is a useful diagnostic fallback, but it is not a
# provisioned user toolchain. Core planning must send a machine that only has
# /usr or /opt OpenROAD/Yosys through the installer so a clone really closes
# over its own binaries.
tool_is_user_owned() {
  local path="${1:-}"
  # detect_env.sh has already established executability on a live machine;
  # --plan-from tests and saved detect dumps intentionally carry synthetic
  # paths, so this planner predicate only classifies ownership by location.
  [[ -n "$path" ]] || return 1
  case "$path" in
    /usr/*|/opt/*|/bin/*|/sbin/*) return 1 ;;
  esac
  return 0
}

# returns via globals TIER_STATUS / TIER_ACTION
eval_tier() {
  local tier="$1"
  TIER_STATUS="OPT"; TIER_ACTION=""
  case "$tier" in
    core)
      if [[ -n "$(d ORFS_ROOT)" && -n "$(d OPENROAD_EXE)" &&
            -n "$(d YOSYS_EXE)" ]] &&
         tool_is_user_owned "$(d OPENROAD_EXE)" &&
         tool_is_user_owned "$(d YOSYS_EXE)"; then
        TIER_STATUS="OK"; TIER_ACTION="present"
      else
        TIER_STATUS="MISS"
        if [[ "$do_direct" == "1" ]]; then
          TIER_ACTION="direct bundle missing under $DIRECT_ROOT (stage openroad + yosys; conda disabled)"
        elif [[ "$SUDO" == "1" ]]; then
          if [[ -n "$(d ORFS_ROOT)" ]]; then
            TIER_ACTION="install/build user-owned OpenROAD + Yosys under R2G_PREFIX"
          else
            TIER_ACTION="clone ORFS + build_openroad.sh --local (~30min; needs --yes)"
          fi
        else
          if [[ -n "$(d ORFS_ROOT)" ]]; then
            TIER_ACTION="'$_conda_bin' -n $CONDA_ENV openroad yosys (user-owned under R2G_PREFIX)"
          else
            TIER_ACTION="clone ORFS (no build) + '$_conda_bin' -n $CONDA_ENV openroad yosys"
          fi
        fi
      fi ;;
    frontend)
      if [[ -n "$(d IVERILOG_EXE)" && -n "$(d VVP_EXE)" ]] &&
         { [[ "$do_hermetic" != "1" ]] ||
           { tool_is_user_owned "$(d IVERILOG_EXE)" &&
             tool_is_user_owned "$(d VVP_EXE)"; }; }; then
        TIER_STATUS="OK"; TIER_ACTION="present"
      else
        TIER_STATUS="MISS"
        if [[ "$do_direct" == "1" ]]; then
          TIER_ACTION="direct bundle missing under $DIRECT_ROOT (stage OSS CAD Suite frontend; conda disabled)"
        elif [[ "$SUDO" == "1" && "$PKG" != "none" ]]; then
          TIER_ACTION="$PKG install iverilog verilator"
        else
          TIER_ACTION="conda -n $CONDA_ENV iverilog verilator"
        fi
      fi ;;
    sky130)
      if [[ -n "$(d MAGIC_EXE)" && -n "$(d NETGEN_EXE)" ]] &&
         { [[ "$do_hermetic" != "1" ]] ||
           { tool_is_user_owned "$(d MAGIC_EXE)" &&
             tool_is_user_owned "$(d NETGEN_EXE)"; }; }; then
        TIER_STATUS="OK"; TIER_ACTION="present"
      else
        TIER_STATUS="$([[ "$do_hermetic" == "1" ]] && echo MISS || echo OPT)"
        TIER_ACTION="$([[ "$do_direct" == "1" ]] && echo "direct bundle missing under $DIRECT_ROOT/magic + $DIRECT_ROOT/netgen (conda disabled)" || echo "conda -n $CONDA_ENV magic netgen")"
      fi ;;
    klayout)
      if [[ -n "$(d KLAYOUT_CMD)" ]] &&
         { [[ "$do_hermetic" != "1" ]] || tool_is_user_owned "$(d KLAYOUT_CMD)"; }; then
        TIER_STATUS="OK"; TIER_ACTION="present"
      else
        TIER_STATUS="$([[ "$do_hermetic" == "1" ]] && echo MISS || echo OPT)"
        TIER_ACTION="$([[ "$do_direct" == "1" ]] && echo "direct bundle missing under $DIRECT_ROOT/klayout (conda disabled)" || echo "conda -n $CONDA_ENV klayout")"
      fi ;;
    pdk)
      if [[ -n "$(d SKY130A_DIR)" ]] &&
         { [[ "$do_hermetic" != "1" ]] || tool_is_user_owned "$(d PDK_ROOT)"; }; then
        TIER_STATUS="OK"; TIER_ACTION="present ($(d SKY130A_DIR))"
      else
        TIER_STATUS="$([[ "$do_hermetic" == "1" ]] && echo MISS || echo OPT)"
        TIER_ACTION="$([[ "$do_direct" == "1" ]] && echo "direct bundle missing under $DIRECT_ROOT/pdks (conda disabled)" || echo "conda -n $CONDA_ENV open_pdks.sky130a -> $CONDA_ROOT/envs/$CONDA_ENV/share/pdk")"
      fi ;;
    graph)
      if [[ -n "$(d GRAPH_PYTHON)" ]] &&
         { [[ "$do_hermetic" != "1" ]] || tool_is_user_owned "$(d GRAPH_PYTHON)"; }; then
        TIER_STATUS="OK"; TIER_ACTION="present ($(d GRAPH_PYTHON))"
      else
        TIER_STATUS="$([[ "$do_hermetic" == "1" ]] && echo MISS || echo OPT)"
        TIER_ACTION="venv+pip torch(cpu)+torch_geometric+pandas -> $_graph_venv"
      fi ;;
    platform_rules)
      # Strict-signoff capability for the default full-flow platform (round-2
      # pilot P0-3): a stock nangate45 ORFS checkout ships NO LVS deck and an
      # unusable zero-diff-area antenna diode, so a green tool table coexists
      # with an impossible strict signoff — discovered only after multi-hour
      # flows. Probe via the sibling skill's platform_capability.py; the
      # installer materializes the repo's bundled DRC/LVS/antenna decks.
      local _cap="$COLLECTION_DIR/signoff-loop/scripts/flow/platform_capability.py"
      # Probe the SELECTED strict platforms (RMD2-P1-01), defaulting to the
      # default full-flow platform when none were named.
      local _plats="${strict_platforms:-nangate45}" _p
      local _pargs=()
      for _p in $_plats; do _pargs+=(--platform "$_p"); done
      local _need_status="OPT"
      [[ -n "$strict_platforms" ]] && _need_status="MISS"
      # Probe with the DETECTED tool environment (PDK/magic/netgen) — the
      # sky130 LVS capability check needs it, and probing bare would misreport
      # a provisioned host as MISS.
      local _pdk="${PDK_ROOT:-}"
      [[ -z "$_pdk" && -n "$(d SKY130A_DIR)" ]] && _pdk="$(dirname "$(d SKY130A_DIR)")"
      if [[ -z "$(d ORFS_ROOT)" || ! -f "$_cap" ]]; then
        TIER_STATUS="$_need_status"; TIER_ACTION="bundled platform DRC/LVS/antenna decks -> ORFS (install ORFS core first)"
      elif PDK_ROOT="$_pdk" MAGIC_EXE="$(d MAGIC_EXE)" NETGEN_EXE="$(d NETGEN_EXE)" \
           python3 "$_cap" --flow-dir "$(d ORFS_ROOT)/flow" "${_pargs[@]}" --strict >/dev/null 2>&1; then
        TIER_STATUS="OK"; TIER_ACTION="strict-signoff capable: $_plats (DRC/LVS decks + usable antenna model)"
      else
        TIER_STATUS="$_need_status"; TIER_ACTION="install bundled platform rule decks (install_platform_rules.sh: $_plats) — strict signoff impossible until then"
      fi ;;
    *) TIER_STATUS="?"; TIER_ACTION="unknown tier" ;;
  esac
}

ALL_TIERS=(core frontend sky130 klayout pdk platform_rules graph)
tier_need() {
  case "$1" in
    core|frontend) echo req ;;
    # Hermetic mode closes the entire user-prefix toolchain in one pass; the
    # platform-rules tier remains governed separately by --strict-platforms.
    sky130|klayout|pdk|graph) [[ "$do_hermetic" == "1" ]] && echo req || echo opt ;;
    # Explicitly selected strict platforms make the rules tier REQUIRED — a
    # requested strict platform cannot complete installation best-effort
    # (RMD2-P1-01).
    platform_rules) [[ -n "$strict_platforms" ]] && echo req || echo opt ;;
    *) echo opt ;;
  esac
}

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
    "$([[ "$do_direct" == 1 ]] && echo 'direct user bundle (no conda)' || ([[ "$SUDO" == 1 ]] && echo 'sudo/build (or conda)' || echo 'conda litex-hub (no-sudo)'))"
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
HERMETIC_FLAG=(); [[ "$do_hermetic" == "1" ]] && HERMETIC_FLAG=(--hermetic)
FORCE_FLAG=(); [[ "$do_hermetic" == "1" ]] && FORCE_FLAG=(--force)

run_tier() {
  local t="$1" script="$SETUP_DIR/install_$1.sh"
  eval_tier "$t"
  [[ "$TIER_STATUS" == "OK" ]] && { echo "[$t] already satisfied — skip"; return 0; }
  if [[ -x "$script" || -f "$script" ]]; then
    echo "[$t] running $(basename "$script") ..."
    R2G_PREFIX="${BIGV}" R2G_CONDA_ENV="$CONDA_ENV" R2G_DIRECT="$do_direct" \
      bash "$script" "${YES_FLAG[@]}" "${FORCE_FLAG[@]}" || {
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
  # Only auto-install required-missing tiers; optional tiers install when named
  # via --tiers. Hermetic mode promotes all tool/PDK/graph tiers to required so
  # a fresh clone closes over user-owned dependencies in one invocation.
  if [[ "$TIER_STATUS" == "OK" ]]; then continue; fi
  if [[ "$need" == "opt" && -z "$tiers_arg" ]] &&
     { [[ "$do_hermetic" != "1" ]] || [[ "$t" == "platform_rules" ]]; }; then
    echo "[$t] optional and not requested (add to --tiers to install) — skip"
    continue
  fi
  run_tier "$t" || install_rc=1
done

# ---- pin env.local.sh --------------------------------------------------------
if [[ -x "$SETUP_DIR/write_env_local.sh" || -f "$SETUP_DIR/write_env_local.sh" ]]; then
  echo
  echo "== pinning references/env.local.sh (both skills) =="
  bash "$SETUP_DIR/write_env_local.sh" "${GP_FLAG[@]}" "${HERMETIC_FLAG[@]}" || \
    echo "warning: env.local.sh pin step failed" >&2
fi

# ---- install manifest (RMD4-P1-01 provenance) --------------------------------
# Persist WHICH toolchain this invocation acted on, and the digests that identify
# it, so a later run (or a signoff manifest) can be compared against it instead of
# re-deriving the answer and possibly getting a different one.
MANIFEST="${SEL[SELECTED_ENV_FILE]:-}"
MANIFEST="${MANIFEST%/*}"                       # …/<skill>/references
MANIFEST="${MANIFEST:-$SKILL_DIR/references}/install_manifest.json"
python3 - "$MANIFEST" "${SEL[SELECTION_SOURCE]:-autodetect}" \
  "${SEL[SELECTED_ORFS_ROOT]:-}" "${SEL[SELECTED_PDK_ROOT]:-}" \
  "${SEL[SELECTED_ENV_FILE]:-}" "${SEL[SELECTED_ENV_SHA256]:-}" \
  "${SEL[OVERRIDES_PINS]:-0}" "$install_rc" "${strict_platforms:-}" <<'PYEOF' 2>/dev/null || \
  echo "warning: could not write install_manifest.json" >&2
import hashlib, json, os, sys, time
(out, source, orfs, pdk, env_file, env_sha, overrides, rc, strict) = sys.argv[1:10]

def _dirsig(path):
    """Cheap identity for a checkout: the flow Makefile's digest."""
    mk = os.path.join(path, "flow", "Makefile") if path else ""
    if not mk or not os.path.isfile(mk):
        return None
    h = hashlib.sha256()
    with open(mk, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

rec = {
    "manifest_version": 1,
    "recorded_at": int(time.time()),
    "selection_source": source,
    "overrides_deployed_pins": overrides == "1",
    "orfs_root": orfs or None,
    "orfs_flow_makefile_sha256": _dirsig(orfs),
    "pdk_root": pdk or None,
    "env_file": env_file or None,
    "env_file_sha256": env_sha or None,
    "strict_platforms": [p for p in (strict or "").split() if p],
    "install_rc": int(rc or 0),
}
os.makedirs(os.path.dirname(out), exist_ok=True)
tmp = out + f".tmp.{os.getpid()}"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(rec, f, indent=1)
    f.write("\n")
os.replace(tmp, out)
print(f"wrote: {out}")
PYEOF

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
