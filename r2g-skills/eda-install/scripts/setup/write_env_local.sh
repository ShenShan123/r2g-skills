#!/usr/bin/env bash
# Generate references/env.local.sh for BOTH skills from the currently-resolved
# toolchain, so a bootstrapped environment (the preferred direct bundle under
# R2G_PREFIX, legacy conda binaries, a torch venv, or a non-standard ORFS) is
# auto-discovered on the next session with no manual edit.
#
# Layer 4 of the bootstrap (detect → plan → install → PIN → verify).
# See docs/superpowers/plans/r2g-skills-bootstrap-2026-07-08.md.
#
# It sources scripts/flow/_env.sh to resolve every path, then writes only the
# lines that autodetect would otherwise miss:
#   - ORFS_ROOT (pinned always — makes discovery deterministic)
#   - openroad/yosys/sta ONLY when they are NOT under $ORFS_ROOT/tools/install
#     (i.e. they came from conda/PATH, which a fresh shell might not find)
#   - signoff tools (iverilog/vvp/verilator/klayout/magic/netgen), regardless of
#     whether they came from the direct bundle or a legacy package environment
#   - PDK_ROOT (+ derived SKY130A_DIR)
#   - R2G_GRAPH_PYTHON (the torch venv for the graph stage)
# Every existing env.local.sh is backed up once before it is replaced.
set -uo pipefail

SETUP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
FLOW_DIR_SH="$(cd -- "$SETUP_DIR/../flow" && pwd)"        # sibling _env.sh (byte-identical copy)
SKILL_ROOT="$(cd -- "$SETUP_DIR/../.." && pwd)"           # …/r2g-skills/eda-install
SKILLS_ROOT="$(cd -- "$SKILL_ROOT/.." && pwd)"            # …/r2g-skills (targets are sibling skills)

graph_python="${R2G_GRAPH_PYTHON:-}"
do_dry=0
hermetic=0
declare -a TARGETS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --graph-python) graph_python="${2:-}"; shift 2 ;;
    --dry-run)      do_dry=1; shift ;;
    --hermetic)     hermetic=1; shift ;;
    --target)       TARGETS+=("${2:-}"); shift 2 ;;   # override output dir(s), repeatable
    -h|--help)
      echo "usage: write_env_local.sh [--graph-python PATH] [--hermetic] [--dry-run] [--target references-dir]..."; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

hostwide_path() {
  case "${1:-}" in
    /usr/*|/opt/*|/bin/*|/sbin/*) return 0 ;;
  esac
  return 1
}

# Default targets: every consumer skill's references/ dir (rtl-acquire delegates
# to the same shared _env.sh + pins, incl. R2G_GRAPH_PYTHON for netlist graphs).
if [[ "${#TARGETS[@]}" -eq 0 ]]; then
  TARGETS=(
    "$SKILLS_ROOT/signoff-loop/references"
    "$SKILLS_ROOT/def-graph/references"
    "$SKILLS_ROOT/rtl-acquire/references"
  )
fi

# Self-heal (2026-07-09, failure-patterns.md #29, generalizing #26): we resolve
# through the eda-install copy of _env.sh, and eda-install has NO
# references/env.local.sh of its own (it is not a pin target) — so a value that
# exists ONLY as a pin in the TARGETS' env.local.sh (conda signoff tools, a
# staged PDK_ROOT) used to be silently DROPPED on every regeneration. Pre-source
# the first existing target pin file so previously pinned values enter _env.sh
# at resolution order #1; its -x / contains-sky130A validation still discards
# stale pins (fail-closed), and autodetect refills them where possible.
for _t in "${TARGETS[@]}"; do
  if [[ -f "$_t/env.local.sh" ]]; then
    set +u   # pin files are user snippets; _env.sh sources them under +u too
    # shellcheck disable=SC1090,SC1091
    source "$_t/env.local.sh" 1>&2
    set -u
    echo "[write_env_local] recalled existing pins from $_t/env.local.sh" >&2
    break
  fi
done

# A previous diagnostic run may have left /usr or /opt pins in a consumer
# env.local.sh.  Hermetic bootstrap must not recall those pins and then let them
# override the newly installed user-prefix binaries.  Keep user-owned pins and
# ORFS/PDK roots; only discard host-wide executable/directory values.
if [[ "$hermetic" == "1" ]]; then
  for _v in OPENROAD_EXE YOSYS_EXE STA_EXE IVERILOG_EXE VVP_EXE VERILATOR_EXE \
            KLAYOUT_CMD MAGIC_EXE NETGEN_EXE PDK_ROOT SKY130A_DIR ORFS_ROOT; do
    _val="${!_v:-}"
    [[ -n "$_val" ]] && hostwide_path "$_val" && unset "$_v"
  done
fi

# A prior hermetic run may have pinned the no-sudo conda Yosys.  If the selected
# ORFS checkout already contains its own packaged Yosys, let the resolver choose
# that flow-matched binary instead; its capability probe is part of the TEHM
# preflight contract and can reject older conda builds (e.g. missing
# ``read_liberty -unit_delay``).  Only discard the pin when the resolved target
# remains inside ORFS_ROOT, so a symlink escape cannot be reintroduced here.
if [[ "$hermetic" == "1" && -n "${ORFS_ROOT:-}" ]]; then
  for _candidate in "$ORFS_ROOT/tools/install/yosys/bin/yosys" \
                    "$ORFS_ROOT/tools/install/Yosys/bin/yosys"; do
    if [[ -x "$_candidate" ]]; then
      _resolved_candidate="$(readlink -f "$_candidate" 2>/dev/null || true)"
      case "$_resolved_candidate" in
        "$ORFS_ROOT"/*) unset YOSYS_EXE; break ;;
      esac
    fi
  done
fi

# Resolve the toolchain (regenerating from resolved values is idempotent).
# Chatter to stderr; keep our stdout clean.
export R2G_GRAPH_PYTHON="$graph_python"
if [[ "$hermetic" == "1" ]]; then
  export R2G_HERMETIC=1
fi
# shellcheck source=/dev/null
source "$FLOW_DIR_SH/_env.sh" 1>&2

# Self-heal (2026-07-09, failure-patterns.md "Dataset-Extraction" #26): a pin
# regeneration from a shell WITHOUT R2G_GRAPH_PYTHON used to silently DROP the
# graph-venv pin from every target — run_graphs.sh then SKIPs the PyG stage
# (graph_skipped) on a fully provisioned machine. _env.sh has no venv autodetect,
# so recall the pin from the targets' existing env.local.sh (validated with -x)
# before writing; an explicit --graph-python always wins and is never filtered.
if [[ -z "$graph_python" ]]; then
  for _t in "${TARGETS[@]}"; do
    _prev="$(sed -n 's/^export R2G_GRAPH_PYTHON="\(.*\)"$/\1/p' "$_t/env.local.sh" 2>/dev/null | head -1)"
    if [[ -n "$_prev" && -x "$_prev" ]]; then
      graph_python="$_prev"
      echo "[write_env_local] recalled R2G_GRAPH_PYTHON=$_prev from $_t/env.local.sh" >&2
      break
    fi
  done
fi

_orfs_install_prefix="${ORFS_ROOT:-}/tools/install/"

# Prefer the hermetic OpenROAD copy, while retaining an ORFS-packaged Yosys when
# it is available.  The ORFS tree's Yosys is tied to its flow and may carry
# capabilities (for example ``read_liberty -unit_delay``) that the old
# no-sudo conda build does not.  A compatible tree-packaged tool must not be
# replaced merely because a second user-owned binary exists.
if [[ "$hermetic" == "1" && -n "${R2G_PREFIX:-}" ]]; then
  _prefix_bin="$R2G_PREFIX/miniconda3/envs/${R2G_CONDA_ENV:-eda}/bin"
  [[ -x "$_prefix_bin/openroad" ]] && OPENROAD_EXE="$_prefix_bin/openroad"
  [[ ! -x "${YOSYS_EXE:-}" && -x "$_prefix_bin/yosys" ]] && YOSYS_EXE="$_prefix_bin/yosys"
  export OPENROAD_EXE YOSYS_EXE
fi

# A direct bundle is the canonical hermetic installation.  Override recalled
# user-local/legacy pins when the corresponding bundled wrapper exists; this
# prevents a stale ~/.local or conda path from surviving a migration merely
# because it is technically user-owned.
if [[ "$hermetic" == "1" ]]; then
  _direct_root="${R2G_TOOLCHAIN_ROOT:-${R2G_PREFIX:-}}"
  if [[ -n "$_direct_root" ]]; then
    if [[ -x "$_direct_root/openroad/bin/openroad.bin" ]]; then
      OPENROAD_EXE="$_direct_root/openroad/bin/openroad.bin"
    elif [[ -x "$_direct_root/openroad/bin/openroad" ]]; then
      OPENROAD_EXE="$_direct_root/openroad/bin/openroad"
    fi
    [[ -x "$_direct_root/yosys/bin/yosys" ]] && YOSYS_EXE="$_direct_root/yosys/bin/yosys"
    [[ -x "$_direct_root/sta/bin/sta" ]] && STA_EXE="$_direct_root/sta/bin/sta"
    [[ -x "$_direct_root/oss-cad-suite/bin/iverilog" ]] && IVERILOG_EXE="$_direct_root/oss-cad-suite/bin/iverilog"
    [[ -x "$_direct_root/oss-cad-suite/bin/vvp" ]] && VVP_EXE="$_direct_root/oss-cad-suite/bin/vvp"
    [[ -x "$_direct_root/oss-cad-suite/bin/verilator" ]] && VERILATOR_EXE="$_direct_root/oss-cad-suite/bin/verilator"
    if [[ -x "$_direct_root/klayout/bin/klayout.bin" ]]; then
      KLAYOUT_CMD="$_direct_root/klayout/bin/klayout.bin"
    elif [[ -x "$_direct_root/klayout/bin/klayout" ]]; then
      KLAYOUT_CMD="$_direct_root/klayout/bin/klayout"
    fi
    [[ -x "$_direct_root/magic/bin/magic" ]] && MAGIC_EXE="$_direct_root/magic/bin/magic"
    [[ -x "$_direct_root/netgen/bin/netgen" ]] && NETGEN_EXE="$_direct_root/netgen/bin/netgen"
    [[ -d "$_direct_root/pdks/sky130A" ]] && PDK_ROOT="$_direct_root/pdks"
    export OPENROAD_EXE YOSYS_EXE STA_EXE IVERILOG_EXE VVP_EXE VERILATOR_EXE \
           KLAYOUT_CMD MAGIC_EXE NETGEN_EXE PDK_ROOT
  fi
fi

# emit_export VAR VALUE [skip_if_under_prefix]
emit_export() {
  local var="$1" val="$2" skip_prefix="${3:-}"
  [[ -z "$val" ]] && return 0
  if [[ -n "$skip_prefix" && "$val" == "$skip_prefix"* ]]; then
    return 0   # under ORFS tools/install → _env.sh already finds it, don't pin
  fi
  printf 'export %s="%s"\n' "$var" "$val"
}

build_content() {
  cat <<'HDR'
# Local environment overrides for the r2g-skills toolchain.
#
# GENERATED by scripts/setup/write_env_local.sh (bootstrap pin step). Pins tool
# discovery in scripts/flow/_env.sh to the paths resolved on this machine so a
# direct user bundle or legacy package environment is found with no manual edit.
# Regenerate:  bash <skill>/scripts/setup/write_env_local.sh
# Every value is optional; delete a line to fall back to autodetection.
HDR
  echo
  echo "# --- OpenROAD-flow-scripts checkout --------------------------------------"
  # Keep the toolchain root explicit so a fresh shell can classify and
  # rediscover a user-prefix direct bundle (or legacy conda tools) without
  # relying on PATH or /opt.
  emit_export R2G_PREFIX "${R2G_PREFIX:-}"
  emit_export R2G_TOOLCHAIN_ROOT "${R2G_TOOLCHAIN_ROOT:-${R2G_PREFIX:-}}"
  [[ "$hermetic" == "1" ]] && echo 'export R2G_HERMETIC="1"'
  emit_export ORFS_ROOT "${ORFS_ROOT:-}"
  echo
  echo "# --- Tool binaries (pinned only when outside \$ORFS_ROOT/tools/install) ----"
  emit_export OPENROAD_EXE  "${OPENROAD_EXE:-}"  "$_orfs_install_prefix"
  emit_export YOSYS_EXE     "${YOSYS_EXE:-}"     "$_orfs_install_prefix"
  emit_export STA_EXE       "${STA_EXE:-}"       "$_orfs_install_prefix"
  emit_export IVERILOG_EXE  "${IVERILOG_EXE:-}"
  emit_export VVP_EXE       "${VVP_EXE:-}"
  emit_export VERILATOR_EXE "${VERILATOR_EXE:-}"
  emit_export KLAYOUT_CMD   "${KLAYOUT_CMD:-}"
  emit_export MAGIC_EXE     "${MAGIC_EXE:-}"
  emit_export NETGEN_EXE    "${NETGEN_EXE:-}"
  echo
  echo "# --- PDK root (sky130 DRC/LVS with Magic / Netgen) -----------------------"
  # -d guard: never pin a deleted tree (a stale pin would be recalled forever)
  if [[ -n "${PDK_ROOT:-}" && -d "${PDK_ROOT}" ]]; then
    printf 'export PDK_ROOT="%s"\n' "$PDK_ROOT"
    printf '[ -d "$PDK_ROOT/sky130A" ] && export SKY130A_DIR="$PDK_ROOT/sky130A"\n'
  fi
  echo
  echo "# --- Graph-dataset stage (def-graph run_graphs.sh) -----------------------"
  if [[ -n "${graph_python:-}" ]]; then
    emit_export R2G_GRAPH_PYTHON "$graph_python"
  else
    echo '# HINT: R2G_GRAPH_PYTHON is NOT pinned — the def-graph / rtl-acquire graph'
    echo '#       stage will SKIP (designs record graph_skipped, which is NOT success).'
    echo '#       Provision: bootstrap.sh --tiers graph, or re-run'
    echo '#       write_env_local.sh --graph-python /path/to/venv/bin/python'
  fi
}

CONTENT="$(build_content)"

if [[ "$do_dry" == "1" ]]; then
  echo "# (dry-run) would write the following to: ${TARGETS[*]}" >&2
  echo "$CONTENT"
  exit 0
fi

_ts="$(date +%s)"
for dir in "${TARGETS[@]}"; do
  [[ -d "$dir" ]] || { echo "skip (no dir): $dir" >&2; continue; }
  dst="$dir/env.local.sh"
  if [[ -f "$dst" ]]; then
    cp -- "$dst" "$dst.bak-$_ts"
    echo "backed up: $dst -> $dst.bak-$_ts" >&2
  fi
  printf '%s\n' "$CONTENT" > "$dst"
  echo "wrote: $dst" >&2
done
