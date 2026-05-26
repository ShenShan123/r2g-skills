#!/usr/bin/env bash
# Run signoff + extraction + knowledge-store ingest on every design_case
# whose backend/RUN_*/stage_log.jsonl shows a successful "finish" stage.
#
# Idempotent: skips designs that already have reports/ppa.json.
#
# Usage:
#   tools/finalize_runs.sh                # process all design_cases
#   tools/finalize_runs.sh <name> [...]   # process a subset

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SKILL="$ROOT/r2g-rtl2gds"
KNOWLEDGE="$SKILL/knowledge"

if [[ $# -eq 0 ]]; then
  TARGETS=()
  while IFS= read -r d; do
    TARGETS+=("$(basename "$d")")
  done < <(find "$ROOT/design_cases" -mindepth 1 -maxdepth 1 -type d \
      ! -name '_*' -printf '%p\n')
else
  TARGETS=("$@")
fi

declare -i ok=0 skipped=0 missing=0 failed=0

for name in "${TARGETS[@]}"; do
  proj="$ROOT/design_cases/$name"
  [[ -d "$proj" ]] || { echo "[skip] $name: no project dir"; continue; }

  latest="$(ls -td "$proj/backend/RUN_"*/ 2>/dev/null | head -1)"
  if [[ -z "$latest" ]]; then
    echo "[--] $name: no RUN_ dir yet"; ((missing++)); continue
  fi

  stage_log="$latest/stage_log.jsonl"
  if [[ ! -f "$stage_log" ]] || ! grep -q '"stage": "finish".*"status": 0' "$stage_log"; then
    echo "[--] $name: backend not yet finished"
    ((missing++)); continue
  fi

  if [[ -f "$proj/reports/ppa.json" ]]; then
    echo "[ok] $name: already finalized (skipping)"
    ((skipped++)); continue
  fi

  echo "[run] $name"

  # PPA + timing gate
  python3 "$SKILL/scripts/extract/extract_ppa.py" "$proj" "$proj/reports/ppa.json" \
      || { echo "  ppa extract failed"; ((failed++)); continue; }
  python3 "$SKILL/scripts/reports/check_timing.py" "$proj" \
      || echo "  timing_check non-zero exit (may be moderate/severe — see timing_check.json)"

  # Signoff (best-effort; each script collects its own results)
  bash "$SKILL/scripts/flow/run_drc.sh" "$proj" nangate45 \
      >> "$latest/signoff.log" 2>&1 || echo "  drc returned non-zero"
  bash "$SKILL/scripts/flow/run_lvs.sh" "$proj" nangate45 \
      >> "$latest/signoff.log" 2>&1 || echo "  lvs returned non-zero"
  bash "$SKILL/scripts/flow/run_rcx.sh" "$proj" nangate45 \
      >> "$latest/signoff.log" 2>&1 || echo "  rcx returned non-zero"

  # Extract signoff json
  python3 "$SKILL/scripts/extract/extract_drc.py" "$proj" "$proj/reports/drc.json" 2>/dev/null || true
  python3 "$SKILL/scripts/extract/extract_lvs.py" "$proj" "$proj/reports/lvs.json" 2>/dev/null || true
  python3 "$SKILL/scripts/extract/extract_rcx.py" "$proj" "$proj/reports/rcx.json" 2>/dev/null || true

  # Diagnosis
  python3 "$SKILL/scripts/reports/build_diagnosis.py" "$proj" "$proj/reports/diagnosis.json" 2>/dev/null || true

  # Knowledge ingest
  python3 "$KNOWLEDGE/ingest_run.py" "$proj" 2>&1 | sed 's/^/  ingest: /'

  echo "[ok] $name done"
  ((ok++))
done

echo "---"
echo "Finalized: $ok | Already done: $skipped | Not yet: $missing | Failed: $failed"

# Rebuild derived artifacts once at the end
if [[ $ok -gt 0 ]]; then
  python3 "$KNOWLEDGE/learn_heuristics.py" >/dev/null && echo "Rebuilt heuristics.json"
  python3 "$KNOWLEDGE/mine_rules.py"       >/dev/null && echo "Rebuilt failure_candidates.json"
fi
