#!/usr/bin/env bash
set -euo pipefail

# usage: resolve_platform_paths.sh <config.mk> <platform>
# Emits KEY=VALUE lines on stdout:
#   LIB_FILES TECH_LEF SC_LEF ADDITIONAL_LIBS ADDITIONAL_LEFS SUPPLY_VOLTAGE
# Primary source: ORFS Makefile variable expansion (handles corner-built vars on
# asap7/gf180). Fallback: glob the platform dir + a per-platform voltage map.
# See references/label-extraction.md.
#
# THIN SHIM (Task 6): the path/voltage resolution now lives in
#   scripts/extract/techlib/resolve.py
# (Python API + a byte-identical KEY=VALUE CLI). This script's only job is to set up
# ORFS env discovery and hand off. Its external contract is unchanged: a clean 6-line
# KEY=VALUE stdout (resolve.py prints only those lines; diagnostics go to stderr).

CONFIG_MK="${1:-}"
PLATFORM="${2:-nangate45}"

# Absolutize CONFIG_MK now: the Make invocation inside resolve.py runs with cwd=$FLOW_DIR,
# so a relative DESIGN_CONFIG would point at the wrong file (and silently break
# corner-built vars like asap7's LIB_FILES).
if [[ -n "$CONFIG_MK" && -f "$CONFIG_MK" ]]; then
  CONFIG_MK="$(cd "$(dirname "$CONFIG_MK")" && pwd)/$(basename "$CONFIG_MK")"
fi

# shellcheck source=/dev/null
# Redirect _env.sh's diagnostic chatter to stderr so this script's stdout stays a
# clean KEY=VALUE contract for consumers that capture it wholesale. resolve.py reads
# $FLOW_DIR (falling back to $ORFS_ROOT/flow) from the environment — it does NOT
# re-implement autodetect — so we must source _env.sh and export the result here.
source "$(dirname "${BASH_SOURCE[0]}")/_env.sh" 1>&2
export FLOW_DIR ORFS_ROOT

exec python3 "$(dirname "${BASH_SOURCE[0]}")/../extract/techlib/resolve.py" \
  "$CONFIG_MK" "$PLATFORM"
