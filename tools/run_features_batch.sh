#!/usr/bin/env bash
set -uo pipefail

# Backfill dataset features across completed designs.
# Usage: ./run_features_batch.sh [max_parallel_jobs] [design ...]
#   max_parallel_jobs  default 4 (the workers are pure-Python parsers — memory-light)
#   design...          explicit project names under design_cases/; if omitted,
#                      auto-discovers designs with a collected 6_final.def.
# Per-design logs + features_backfill.jsonl under design_cases/_batch/logs_features_<tag>/.
# See r2g-skills/def-graph/references/feature-extraction.md.

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CASES_DIR="$BASE_DIR/design_cases"
SKILL_DIR="$BASE_DIR/r2g-skills/def-graph"
RUN_FEATURES="$SKILL_DIR/scripts/flow/run_features.sh"

# Scrub stray per-design overrides from the parent shell so they cannot leak to every
# design in the batch (e.g. an exported R2G_DEF/DEF_FILE would pin all designs to one DEF).
unset R2G_DEF R2G_SPEF DEF_FILE 2>/dev/null || true

MAX_JOBS=4
if [[ "${1:-}" =~ ^[0-9]+$ ]]; then MAX_JOBS="$1"; shift; fi

# Next log index = max existing numeric suffix + 1 (a directory COUNT would reuse an
# index and truncate the roll-up if an intermediate logs_features_N dir was deleted).
if [[ -z "${LOGTAG:-}" ]]; then
  _max=$(ls -d "$CASES_DIR"/_batch/logs_features_* 2>/dev/null | sed -E 's#.*logs_features_##' | grep -E '^[0-9]+$' | sort -n | tail -1)
  LOGTAG=$(( ${_max:--1} + 1 ))
fi
LOG_DIR="$CASES_DIR/_batch/logs_features_${LOGTAG}"
mkdir -p "$LOG_DIR"
JSONL="$LOG_DIR/features_backfill.jsonl"
: > "$JSONL"

# --- Build design list ------------------------------------------------------
designs=()
if [[ $# -gt 0 ]]; then
  for d in "$@"; do designs+=("$(basename "$d")"); done
else
  while IFS= read -r defp; do
    designs+=("$(echo "$defp" | sed -E "s#^$CASES_DIR/([^/]+)/.*#\1#")")
  done < <(find "$CASES_DIR" -maxdepth 5 -path '*/backend/RUN_*/*/6_final.def' 2>/dev/null | sort -u)
  mapfile -t designs < <(printf '%s\n' "${designs[@]}" | awk 'NF && !seen[$0]++')
fi

echo "Backfilling features for ${#designs[@]} designs (max $MAX_JOBS concurrent) -> $LOG_DIR"

run_one() {
  local d="$1"
  local proj="$CASES_DIR/$d"
  local log="$LOG_DIR/$d.log"
  if [[ ! -d "$proj" ]]; then
    echo "{\"design\":\"$d\",\"status\":\"missing\"}" >> "$JSONL"
    return
  fi
  bash "$RUN_FEATURES" "$proj" > "$log" 2>&1
  local stats="$proj/reports/features_stats.json"
  if [[ -f "$stats" ]]; then
    python3 - "$d" "$stats" >> "$JSONL" <<'PY'
import json, sys
d, stats = sys.argv[1], sys.argv[2]
try:
    j = json.load(open(stats))
    F = j.get("features", {})
    row = {"design": d, "status": j.get("status", "done"),
           "platform": j.get("platform"), "spef_present": j.get("spef_present"),
           "rows": {k: (v.get("rows") if isinstance(v, dict) else None) for k, v in F.items()},
           "feature_status": {k: (v.get("status") if isinstance(v, dict) else None) for k, v in F.items()}}
except Exception as e:
    row = {"design": d, "status": "error", "error": str(e)}
print(json.dumps(row))
PY
  else
    echo "{\"design\":\"$d\",\"status\":\"no_stats\"}" >> "$JSONL"
  fi
  echo "  done: $d"
}

# Sliding window: keep up to MAX_JOBS in flight, launching a new design as soon as any
# one finishes (wait -n, bash 4.3+) rather than draining a whole group at a barrier.
running=0
for d in "${designs[@]}"; do
  run_one "$d" &
  running=$((running + 1))
  if (( running >= MAX_JOBS )); then wait -n; running=$((running - 1)); fi
done
wait

echo "Roll-up: $JSONL"
echo "rows in roll-up: $(wc -l < "$JSONL")"
python3 - "$JSONL" <<'PY'
import json, sys, collections
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
c = collections.Counter()
for r in rows:
    for k, v in (r.get("feature_status") or {}).items():
        c[(k, v)] += 1
print(f"designs: {len(rows)}")
for k in sorted(c):
    print(f"  {k[0]:16s} {str(k[1]):8s} {c[k]}")
PY
