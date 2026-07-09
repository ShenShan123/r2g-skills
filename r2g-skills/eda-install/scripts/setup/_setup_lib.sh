#!/usr/bin/env bash
# Shared helpers for the eda-install tier installers (install_<tier>.sh).
#
# Sourced, NOT executed. Centralizes the conda/litex-hub machinery so a channel
# name, the ToS-gate workaround, or the Miniconda-bootstrap recipe is fixed in
# ONE place (CLAUDE.md: fix a parse/setup bug once, never per worker-copy).
#
# Contract for consumers:
#   - set DRY=1 to PREVIEW (every action prints '+ cmd' to stdout instead of running)
#   - set FORCE=1 to bypass the "already present" idempotency short-circuit
#   - call setup_parse "$@" to consume the common flags (--dry-run/--force/--yes)
# Every network action is idempotent and fail-soft: an unreachable channel /
# missing conda ESCALATES with a HINT and non-zero, never a silent success.

CONDA_ENV="${R2G_CONDA_ENV:-eda}"
CONDA_CH=(--override-channels -c litex-hub -c conda-forge)   # defaults-channel ToS-gate workaround
MINICONDA_URL="https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh"
GRAPH_VENV_SUBPATH="pyenvs/r2g-graph"
TORCH_CPU_INDEX="https://download.pytorch.org/whl/cpu"
ORFS_URL="https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts"

: "${DRY:=0}"
: "${FORCE:=0}"

log()  { printf '[eda-install] %s\n'       "$*" >&2; }
hint() { printf '[eda-install] HINT: %s\n' "$*" >&2; }
die()  { printf '[eda-install] ERROR: %s\n' "$*" >&2; exit 1; }

# run CMD… — execute, or under DRY print '+ CMD' (to stdout) and skip.
run() {
  if [[ "$DRY" == "1" ]]; then printf '+ %s\n' "$*"; return 0; fi
  "$@"
}

have_cmd() { command -v "$1" >/dev/null 2>&1; }

_have_sudo() {
  [[ "${EUID:-$(id -u)}" -eq 0 ]] && return 0
  have_cmd sudo && sudo -n true >/dev/null 2>&1
}

# Big volume for the conda root / PDK / venv: $R2G_PREFIX, else the first writable
# dir with >= R2G_MIN_FREE_GB free, preferring /proj over a (possibly full) $HOME.
pick_big_volume() {
  local min="${R2G_MIN_FREE_GB:-15}" c freekb freegb
  for c in "${R2G_PREFIX:-}" "/proj/$USER" "/proj/workarea/$USER" "$HOME"; do
    [[ -z "$c" ]] && continue
    [[ -d "$c" && -w "$c" ]] || continue
    freekb="$(df -Pk "$c" 2>/dev/null | awk 'NR==2{print $4}')"
    [[ -n "$freekb" ]] || continue
    freegb=$(( freekb / 1024 / 1024 ))
    if [[ "$freegb" -ge "$min" ]]; then echo "$c"; return 0; fi
  done
  return 1
}

# Echo a usable conda executable; bootstrap Miniconda onto the big volume if none.
ensure_conda() {
  local c bigv target
  for c in "${R2G_CONDA:-}" mamba conda; do
    [[ -z "$c" ]] && continue
    if have_cmd "$c"; then command -v "$c"; return 0; fi
  done
  for c in "$HOME/miniconda3/bin/conda" "${R2G_PREFIX:-}/miniconda3/bin/conda"; do
    [[ -n "$c" && -x "$c" ]] && { echo "$c"; return 0; }
  done
  bigv="$(pick_big_volume)" || { hint "no writable volume with >= ${R2G_MIN_FREE_GB:-15}GB free — pass --prefix DIR"; return 1; }
  target="$bigv/miniconda3"
  [[ -x "$target/bin/conda" ]] && { echo "$target/bin/conda"; return 0; }
  if [[ "$DRY" == "1" ]]; then
    printf '+ curl -fsSL -o %s/miniconda.sh %s\n' "$bigv" "$MINICONDA_URL" >&2
    printf '+ bash %s/miniconda.sh -b -p %s\n' "$bigv" "$target" >&2
    echo "$target/bin/conda"; return 0
  fi
  have_cmd curl || { hint "no conda and no curl — install Miniconda to $target and re-run"; return 1; }
  log "installing Miniconda (no-sudo) → $target"
  curl -fsSL -o "$bigv/miniconda.sh" "$MINICONDA_URL" \
    || { hint "Miniconda download failed (offline/proxy?) — fetch $MINICONDA_URL to $bigv/miniconda.sh, then re-run"; return 1; }
  bash "$bigv/miniconda.sh" -b -p "$target" \
    || { hint "Miniconda install failed"; return 1; }
  echo "$target/bin/conda"
}

# conda_env_install PKG… — create-or-install PKGs into $CONDA_ENV on litex-hub.
conda_env_install() {
  local conda action=install
  conda="$(ensure_conda)" || return 1
  if ! "$conda" env list 2>/dev/null | awk '{print $1}' | grep -qx "$CONDA_ENV"; then
    action=create
  fi
  if [[ "$DRY" == "1" ]]; then
    printf '+ %s %s -y -n %s %s %s\n' "$conda" "$action" "$CONDA_ENV" "${CONDA_CH[*]}" "$*"
    return 0
  fi
  log "conda $action -n $CONDA_ENV: $*"
  "$conda" "$action" -y -n "$CONDA_ENV" "${CONDA_CH[@]}" "$@"
}

# Consume the common flags; anything else lands in SETUP_REST[].
setup_parse() {
  SETUP_REST=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run)  DRY=1; shift ;;
      --force|-f) FORCE=1; shift ;;
      --yes|-y)   shift ;;                 # conda always runs -y; accepted for uniformity
      *)          SETUP_REST+=("$1"); shift ;;
    esac
  done
}
