#!/usr/bin/env bash
# Install the r2g-rtl2gds Claude Code skill.
#
# Standalone — this script depends only on bash, ln, cp, mkdir, rm.
# Run it from inside the r2g-rtl2gds/ directory (or pass --src DIR).
set -euo pipefail

SKILL_NAME="r2g-rtl2gds"
SRC_DEFAULT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

print_help() {
  cat <<EOF
Install the ${SKILL_NAME} Claude Code skill.

Usage:
  $(basename "$0") [--user | --project DIR] [--link] [--force] [--src DIR]
  $(basename "$0") --uninstall [--user | --project DIR]

Options:
  --user           Install to \$HOME/.claude/skills/${SKILL_NAME} (default if interactive choice picks 'user').
  --project DIR    Install to DIR/.claude/skills/${SKILL_NAME}.
  --link           Symlink instead of copy (recommended while developing the skill).
  --force          Overwrite an existing install at the destination.
  --src DIR        Source directory to install from (default: this script's directory).
  --uninstall      Remove a previous install.
  -h, --help       Show this help.

With no scope flag, the script prompts:  user (~/.claude/skills) vs project (./.claude/skills).
EOF
}

mode="install"
scope=""
project_dir=""
do_link=0
do_force=0
src_dir="$SRC_DEFAULT"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)        scope="user"; shift ;;
    --project)     scope="project"; project_dir="${2:-}"; shift 2 ;;
    --link)        do_link=1; shift ;;
    --force)       do_force=1; shift ;;
    --src)         src_dir="${2:-}"; shift 2 ;;
    --uninstall)   mode="uninstall"; shift ;;
    -h|--help)     print_help; exit 0 ;;
    *) echo "unknown arg: $1" >&2; print_help; exit 2 ;;
  esac
done

# Sanity: source must look like the skill.
if [[ ! -f "$src_dir/SKILL.md" ]]; then
  echo "error: --src '$src_dir' does not contain SKILL.md" >&2
  exit 2
fi

# Materialize the knowledge store on a fresh clone. The binary knowledge.sqlite is a
# REBUILDABLE artifact (no longer git-tracked, 2026-06-23 bundle-as-source-of-truth
# migration); the committed source of truth is the git-friendly TEXT bundle
# knowledge/store/. Rebuild the binary from it ONLY when absent (never clobber a live
# local store, e.g. mid-campaign). Best-effort: a failure here NEVER aborts the install
# — the skill simply falls back to its hardcoded config tables until you run the import.
_k_db="$src_dir/knowledge/knowledge.sqlite"
_k_bundle="$src_dir/knowledge/store"
if [[ ! -f "$_k_db" && -f "$_k_bundle/manifest.json" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    echo "knowledge.sqlite absent — rebuilding from committed bundle ($_k_bundle) ..."
    if python3 "$src_dir/knowledge/knowledge_sync.py" import \
         --bundle "$_k_bundle" --db "$_k_db"; then
      echo "rebuilt: $_k_db"
    else
      echo "WARNING: knowledge.sqlite rebuild failed; the skill will use hardcoded" >&2
      echo "         config tables until you run:" >&2
      echo "         python3 knowledge/knowledge_sync.py import --bundle knowledge/store --db knowledge/knowledge.sqlite" >&2
    fi
  else
    echo "WARNING: python3 not found; cannot rebuild knowledge.sqlite from the bundle." >&2
  fi
fi

# Resolve scope interactively if not set.
if [[ -z "$scope" ]]; then
  if [[ ! -t 0 ]]; then
    scope="user"
    echo "Non-interactive: defaulting to user install (~/.claude/skills/${SKILL_NAME})."
    echo "Pass --project DIR to scope to a single project instead."
  fi
  echo "Install ${SKILL_NAME} into which Claude Code scope?"
  echo "  [1] user      (\$HOME/.claude/skills/${SKILL_NAME})"
  echo "  [2] project   (./.claude/skills/${SKILL_NAME})"
  printf "Choice [1]: "
  read -r choice
  case "${choice:-1}" in
    1|user|U|u)    scope="user" ;;
    2|project|P|p) scope="project"; project_dir="$PWD" ;;
    *) echo "unknown choice: $choice" >&2; exit 2 ;;
  esac
fi

if [[ "$scope" == "project" ]]; then
  : "${project_dir:=$PWD}"
  dest_root="$(cd -- "$project_dir" && pwd)/.claude/skills"
else
  dest_root="$HOME/.claude/skills"
fi
dest="$dest_root/$SKILL_NAME"

if [[ "$mode" == "uninstall" ]]; then
  if [[ -e "$dest" || -L "$dest" ]]; then
    rm -rf -- "$dest"
    echo "removed: $dest"
  else
    echo "nothing to remove at $dest"
  fi
  exit 0
fi

mkdir -p -- "$dest_root"

if [[ -e "$dest" || -L "$dest" ]]; then
  if [[ "$do_force" -eq 0 ]]; then
    echo "destination already exists: $dest" >&2
    echo "re-run with --force to overwrite, or --uninstall first." >&2
    exit 1
  fi
  rm -rf -- "$dest"
fi

if [[ "$do_link" -eq 1 ]]; then
  ln -s -- "$src_dir" "$dest"
  echo "linked:  $dest -> $src_dir"
else
  cp -R -- "$src_dir" "$dest"
  echo "copied:  $src_dir -> $dest"
fi

cat <<EOF

Install complete.

Next steps:
  1. Restart Claude Code (or run /reload) so the skill is picked up.
  2. Verify EDA tool discovery:
       bash "$dest/scripts/flow/check_env.sh"
  3. In a Claude Code session, ask:
       "take this RTL through to GDS on nangate45"
     and the r2g-rtl2gds skill will be invoked automatically.
EOF
