#!/usr/bin/env bash
# Install the r2g-skills Claude Code skills (eda-install + signoff-loop + def-graph + rtl-acquire).
#
# Standalone — depends only on bash, ln, cp, mkdir, rm.
# Run it from inside the r2g-skills/ directory (or pass --src DIR).
#
# The sub-skills install as SEPARATE Claude Code skills:
#   <scope>/.claude/skills/signoff-loop -> r2g-skills/signoff-loop
#   <scope>/.claude/skills/def-graph    -> r2g-skills/def-graph
#   <scope>/.claude/skills/eda-install  -> r2g-skills/eda-install
#   <scope>/.claude/skills/rtl-acquire  -> r2g-skills/rtl-acquire
# so the harness triggers each on its own description. Use --link while developing
# (a plain copy silently goes stale as the canonical tree evolves).
set -euo pipefail

PLUGIN_NAME="r2g-skills"
SKILLS=(signoff-loop def-graph eda-install rtl-acquire)
SRC_DEFAULT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_CSV="$(IFS=,; echo "${SKILLS[*]}")"

print_help() {
  cat <<EOF
Install the ${PLUGIN_NAME} Claude Code skills (${SKILLS[*]}).

Usage:
  $(basename "$0") [--user | --project DIR] [--link] [--force] [--src DIR]
  $(basename "$0") --uninstall [--user | --project DIR]

Options:
  --user           Install to \$HOME/.claude/skills/{${SKILLS_CSV}}.
  --project DIR    Install to DIR/.claude/skills/{${SKILLS_CSV}}.
  --link           Symlink instead of copy (recommended while developing the skills).
  --force          Overwrite an existing install at the destination.
  --src DIR        Source r2g-skills directory (default: this script's directory).
  --uninstall      Remove a previous install of all sub-skills.
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

# Sanity: source must contain every sub-skill.
for s in "${SKILLS[@]}"; do
  if [[ ! -f "$src_dir/$s/SKILL.md" ]]; then
    echo "error: --src '$src_dir' is missing $s/SKILL.md" >&2
    exit 2
  fi
done

# Resolve scope interactively if not set.
if [[ -z "$scope" ]]; then
  if [[ ! -t 0 ]]; then
    scope="user"
    echo "Non-interactive: defaulting to user install (~/.claude/skills)."
    echo "Pass --project DIR to scope to a single project instead."
  else
    echo "Install ${PLUGIN_NAME} into which Claude Code scope?"
    echo "  [1] user      (\$HOME/.claude/skills)"
    echo "  [2] project   (./.claude/skills)"
    printf "Choice [1]: "
    read -r choice
    case "${choice:-1}" in
      1|user|U|u)    scope="user" ;;
      2|project|P|p) scope="project"; project_dir="$PWD" ;;
      *) echo "unknown choice: $choice" >&2; exit 2 ;;
    esac
  fi
fi

if [[ "$scope" == "project" ]]; then
  : "${project_dir:=$PWD}"
  dest_root="$(cd -- "$project_dir" && pwd)/.claude/skills"
else
  dest_root="$HOME/.claude/skills"
fi

if [[ "$mode" == "uninstall" ]]; then
  for s in "${SKILLS[@]}"; do
    dest="$dest_root/$s"
    if [[ -e "$dest" || -L "$dest" ]]; then
      rm -rf -- "$dest"; echo "removed: $dest"
    else
      echo "nothing to remove at $dest"
    fi
  done
  exit 0
fi

mkdir -p -- "$dest_root"

for s in "${SKILLS[@]}"; do
  dest="$dest_root/$s"
  if [[ -e "$dest" || -L "$dest" ]]; then
    if [[ "$do_force" -eq 0 ]]; then
      echo "destination already exists: $dest" >&2
      echo "re-run with --force to overwrite, or --uninstall first." >&2
      exit 1
    fi
    rm -rf -- "$dest"
  fi
  if [[ "$do_link" -eq 1 ]]; then
    ln -s -- "$src_dir/$s" "$dest"
    echo "linked:  $dest -> $src_dir/$s"
  else
    cp -R -- "$src_dir/$s" "$dest"
    echo "copied:  $src_dir/$s -> $dest"
  fi
done

cat <<EOF

Install complete (${SKILLS[*]}).

Next steps:
  1. Restart Claude Code (or run /reload) so the skills are picked up.
  2. Detect + provision the EDA toolchain (eda-install):
       bash "$src_dir/eda-install/bootstrap.sh" --dry-run   # plan only; drop --dry-run to install + pin
     or just verify what is already discoverable:
       bash "$dest_root/eda-install/scripts/flow/check_env.sh"
  3. In a Claude Code session, ask e.g. "set up the EDA tools" (eda-install),
     "take this RTL through to GDS on nangate45" (signoff-loop),
     "build the graph dataset for this design" (def-graph), or
     "expand the netlist-graph corpus from these RTL repos" (rtl-acquire).
EOF
