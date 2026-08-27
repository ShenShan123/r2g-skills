#!/usr/bin/env bash
# Disk cleanup for the ORFS campaigns (approved Tier 1 + Tier 2).
#
# Tier 1: delete REGENERABLE ORFS intermediates (logs / results / objects) under
#         every backend/RUN_*/, PRESERVING final/ (DEF/GDS), run-meta.json,
#         stage_log.jsonl, stage_artifact_manifest.jsonl, flow.log, and the
#         per-project lvs/ drc/ rcx/ features/ reports/ evidence.
# Tier 2: delete the superseded orfs-v1 smoke campaign entirely.
#
# Idempotent: run with DRY=1 to print what would be removed, then without to
# execute. Safe to re-run.
#
#   bash scripts/cleanup_campaign_disk.sh DRY=1     # list only
#   bash scripts/cleanup_campaign_disk.sh            # execute

set -euo pipefail
shopt -s nullglob

ROOT=/data1/zhangdy/tehm-campaigns
DRY=0
[[ "${1:-}" == "DRY=1" ]] && DRY=1

# campaign -> base dir containing the project/case dirs
BASES=(
  "$ROOT/orfs-v1/cases"
  "$ROOT/orfs-v2-diversity/cases"
  "$ROOT/orfs-v3-calibration/cases"
  "$ROOT/orfs-v3-contexts/projects"
  "$ROOT/orfs-v3-heldout-calibration/projects"
)

total=0
declare -A seen

# ---- Tier 1: RUN_* intermediates --------------------------------------------
for base in "${BASES[@]}"; do
  for sub in logs results objects; do
    for d in "$base"/*/backend/RUN_*/"$sub"; do
      [ -d "$d" ] || continue
      sz=$(du -sk "$d" 2>/dev/null | awk '{print $1}')
      total=$((total + sz))
      if [[ "$DRY" == "1" ]]; then
        printf '[dry] %s  (%sM)\n' "$d" "$((sz / 1024))"
      else
        printf 'rm    %s  (%sM)\n' "$d" "$((sz / 1024))"
        rm -rf "$d"
      fi
    done
  done
done

# ---- Tier 2: superseded orfs-v1 ---------------------------------------------
if [[ -d "$ROOT/orfs-v1" ]]; then
  sz=$(du -sk "$ROOT/orfs-v1" 2>/dev/null | awk '{print $1}')
  total=$((total + sz))
  if [[ "$DRY" == "1" ]]; then
    printf '[dry] %s  (%sM)  [Tier 2: whole campaign]\n' "$ROOT/orfs-v1" "$((sz / 1024))"
  else
    printf 'rm    %s  (%sM)  [Tier 2: whole campaign]\n' "$ROOT/orfs-v1" "$((sz / 1024))"
    rm -rf "$ROOT/orfs-v1"
  fi
fi

echo "--------------------------------------------------"
if [[ "$DRY" == "1" ]]; then
  echo "DRY RUN — would reclaim ~$((total / 1024 / 1024)) GB"
else
  echo "reclaimed ~$((total / 1024 / 1024)) GB"
fi
