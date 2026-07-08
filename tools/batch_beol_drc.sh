#!/usr/bin/env bash
# batch_beol_drc.sh — convert FEOL-hang `stuck` DRC designs to an honest
# routing-DRC verdict via BEOL-only mode (DRC_BEOL_ONLY=1).
#
# Why: ~271 designs in the corpus are `stuck` because KLayout's FreePDK45.lydrc
# FEOL boolean ops (poly/active/well) never finish.  BEOL-only mode disables the
# FEOL + ANTENNA rule groups (library-internal, pre-verified geometry) and checks
# only the per-design metal/via/cut routing, yielding status `clean_beol` (0 viol)
# or `fail` (real BEOL violations).  See references/failure-patterns.md
# "BEOL-only fallback" and references/signoff-fixing.md.
#
# Large designs (>~400K inst) re-hang on the BEOL CONTACT op, so this tool caps
# by instance count (--max-inst) and bounds memory via --jobs; any design that
# still hangs is killed by the per-design DRC_TIMEOUT and left honestly `stuck`.
#
# Usage:
#   tools/batch_beol_drc.sh [--max-inst N] [--jobs J] [--timeout SECS]
#                           [--platform P] [--dry-run] [design ...]
#
#   --max-inst N   skip stuck designs with instance_count > N   (default 200000)
#   --jobs J       max concurrent run_drc.sh jobs                (default 4)
#   --timeout S    per-design DRC_TIMEOUT in seconds             (default 1800)
#   --platform P   ORFS platform                                 (default nangate45)
#   --dry-run      print the work-list and exit
#   [design ...]   explicit design-case basenames; else auto-discover status==stuck
#
# Emits one JSON line per processed design to:
#   design_cases/_batch/beol_drc_<UTC-stamp>.jsonl
# and a human summary to stdout at the end.
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
SKILL_DIR="$REPO_ROOT/r2g-skills/signoff-loop"
DC="$REPO_ROOT/design_cases"

MAX_INST=200000
JOBS=4
TIMEOUT=1800
PLATFORM=nangate45
DRY_RUN=0
DESIGNS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --max-inst) MAX_INST="$2"; shift 2;;
    --jobs)     JOBS="$2"; shift 2;;
    --timeout)  TIMEOUT="$2"; shift 2;;
    --platform) PLATFORM="$2"; shift 2;;
    --dry-run)  DRY_RUN=1; shift;;
    -h|--help)  sed -n '2,30p' "$0"; exit 0;;
    --*)        echo "unknown flag: $1" >&2; exit 2;;
    *)          DESIGNS+=("$1"); shift;;
  esac
done

STAMP="$(date +%Y%m%dT%H%M%S%z)"
OUT_DIR="$DC/_batch"
mkdir -p "$OUT_DIR"
JSONL="$OUT_DIR/beol_drc_${STAMP}.jsonl"

# Build the work-list: (instance_count, design) for stuck designs <= MAX_INST,
# ordered ascending by size.  If explicit designs were given, use those instead.
mapfile -t WORK < <(python3 - "$DC" "$MAX_INST" "${DESIGNS[@]+"${DESIGNS[@]}"}" <<'PY'
import json, sys, os, glob
dc = sys.argv[1]; max_inst = int(sys.argv[2]); explicit = sys.argv[3:]
def inst(proj):
    p = os.path.join(dc, proj, "reports", "ppa.json")
    try: return json.load(open(p)).get("geometry", {}).get("instance_count") or 0
    except Exception: return 0
def drc_status(proj):
    p = os.path.join(dc, proj, "reports", "drc.json")
    try: return json.load(open(p)).get("status")
    except Exception: return None
if explicit:
    cands = explicit
else:
    cands = [d.split("/")[-3] for d in glob.glob(os.path.join(dc, "*", "reports", "drc.json"))]
rows = []
for proj in sorted(set(cands)):
    st = drc_status(proj)
    if not explicit and st != "stuck":      # auto mode: only stuck designs
        continue
    if st == "clean_beol":                   # already done — idempotent skip
        continue
    n = inst(proj)
    if n > max_inst:
        continue
    rows.append((n, proj))
rows.sort()
for n, proj in rows:
    print(f"{n}\t{proj}")
PY
)

echo "BEOL-only DRC batch — $STAMP"
echo "  platform=$PLATFORM  max_inst=$MAX_INST  jobs=$JOBS  per-design timeout=${TIMEOUT}s"
echo "  work-list: ${#WORK[@]} design(s)"
if [[ ${#WORK[@]} -eq 0 ]]; then echo "  (nothing to do)"; exit 0; fi
printf '    %s\n' "${WORK[@]}" | sed 's/\t/  /'
if [[ "$DRY_RUN" == "1" ]]; then echo "(dry-run; exiting)"; exit 0; fi

run_one() {
  local inst="$1" proj="$2"
  local pdir="$DC/$proj"
  local log="$OUT_DIR/beol_${proj}.log"
  local start end wall status viol mode
  start=$(date +%s)
  DRC_BEOL_ONLY=1 DRC_TIMEOUT="$TIMEOUT" \
    timeout "$((TIMEOUT + 180))" \
    bash "$SKILL_DIR/scripts/flow/run_drc.sh" "$pdir" "$PLATFORM" >"$log" 2>&1
  python3 "$SKILL_DIR/scripts/extract/extract_drc.py" "$pdir" "$pdir/reports/drc.json" >/dev/null 2>&1
  end=$(date +%s); wall=$((end - start))
  read -r status viol mode < <(python3 - "$pdir/reports/drc.json" <<'PY'
import json, sys
try:
    j = json.load(open(sys.argv[1]))
    print(j.get("status"), j.get("total_violations"), j.get("drc_mode"))
except Exception:
    print("error", "None", "None")
PY
)
  printf '{"design":"%s","inst":%s,"status":"%s","violations":%s,"drc_mode":"%s","wall_s":%s}\n' \
    "$proj" "$inst" "$status" "$viol" "$mode" "$wall" >> "$JSONL"
  echo "  [$status] $proj (inst=$inst viol=$viol ${wall}s)"
}

# Bounded-parallel dispatch.
running=0
for line in "${WORK[@]}"; do
  inst="${line%%$'\t'*}"; proj="${line#*$'\t'}"
  run_one "$inst" "$proj" &
  running=$((running + 1))
  if [[ $running -ge $JOBS ]]; then wait -n 2>/dev/null || wait; running=$((running - 1)); fi
done
wait

echo ""
echo "=== BEOL-only DRC batch summary ($JSONL) ==="
python3 - "$JSONL" <<'PY'
import json, sys, collections
c = collections.Counter(); walls = []
for ln in open(sys.argv[1]):
    try: j = json.loads(ln)
    except Exception: continue
    c[j["status"]] += 1; walls.append(j.get("wall_s") or 0)
total = sum(c.values())
for st, n in c.most_common():
    print(f"  {st:14s} {n}")
print(f"  {'TOTAL':14s} {total}   (max wall {max(walls) if walls else 0}s)")
PY
