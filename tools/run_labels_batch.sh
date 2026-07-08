#!/usr/bin/env bash
set -uo pipefail

# Backfill dataset labels across completed designs.
# Usage: ./run_labels_batch.sh [max_parallel_jobs] [design ...]
#   max_parallel_jobs  default 4 (OpenROAD STA/PDNSim are memory-light vs KLayout LVS)
#   design...          explicit project names under design_cases/; if omitted,
#                      auto-discovers designs with a collected 6_final.odb.
# Per-design logs + labels_backfill.jsonl under design_cases/_batch/logs_labels_<tag>/.
# See r2g-skills/def-graph/references/label-extraction.md.

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CASES_DIR="$BASE_DIR/design_cases"
SKILL_DIR="$BASE_DIR/r2g-skills/def-graph"
RUN_LABELS="$SKILL_DIR/scripts/flow/run_labels.sh"

MAX_JOBS=4
if [[ "${1:-}" =~ ^[0-9]+$ ]]; then MAX_JOBS="$1"; shift; fi

LOGTAG="${LOGTAG:-$(ls -d "$CASES_DIR"/_batch/logs_labels_* 2>/dev/null | wc -l)}"
LOG_DIR="$CASES_DIR/_batch/logs_labels_${LOGTAG}"
mkdir -p "$LOG_DIR"
JSONL="$LOG_DIR/labels_backfill.jsonl"
: > "$JSONL"

# --- Build design list ------------------------------------------------------
designs=()
if [[ $# -gt 0 ]]; then
  for d in "$@"; do designs+=("$(basename "$d")"); done
else
  while IFS= read -r odb; do
    designs+=("$(echo "$odb" | sed -E "s#^$CASES_DIR/([^/]+)/.*#\1#")")
  done < <(find "$CASES_DIR" -maxdepth 5 -path '*/backend/RUN_*/final/6_final.odb' 2>/dev/null | sort -u)
  mapfile -t designs < <(printf '%s\n' "${designs[@]}" | awk 'NF && !seen[$0]++')
fi

echo "Backfilling labels for ${#designs[@]} designs (max $MAX_JOBS concurrent) -> $LOG_DIR"

run_one() {
  local d="$1"
  local proj="$CASES_DIR/$d"
  local log="$LOG_DIR/$d.log"
  if [[ ! -d "$proj" ]]; then
    echo "{\"design\":\"$d\",\"status\":\"missing\"}" >> "$JSONL"
    return
  fi
  bash "$RUN_LABELS" "$proj" > "$log" 2>&1
  local stats="$proj/reports/labels_stats.json"
  if [[ -f "$stats" ]]; then
    python3 - "$d" "$stats" >> "$JSONL" <<'PY'
import json, sys
d, stats = sys.argv[1], sys.argv[2]
try:
    j = json.load(open(stats))
    L = j.get("labels", {})
    row = {"design": d, "status": j.get("status", "done"),
           "rows": {k: (v.get("rows") if isinstance(v, dict) else None) for k, v in L.items()},
           "label_status": {k: (v.get("status") if isinstance(v, dict) else None) for k, v in L.items()}}
except Exception as e:
    row = {"design": d, "status": "error", "error": str(e)}
print(json.dumps(row))
PY
  else
    echo "{\"design\":\"$d\",\"status\":\"no_stats\"}" >> "$JSONL"
  fi
  echo "  done: $d"
}

i=0
for d in "${designs[@]}"; do
  run_one "$d" &
  i=$((i + 1))
  if (( i % MAX_JOBS == 0 )); then wait; fi
done
wait

echo "Roll-up: $JSONL"
echo "rows in roll-up: $(wc -l < "$JSONL")"
python3 - "$JSONL" <<'PY'
import json, sys, collections
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
c = collections.Counter()
for r in rows:
    for k, v in (r.get("label_status") or {}).items():
        c[(k, v)] += 1
print(f"designs: {len(rows)}")
for k in sorted(c):
    print(f"  {k[0]:11s} {str(k[1]):8s} {c[k]}")
PY
