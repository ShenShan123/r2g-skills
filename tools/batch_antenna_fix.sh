#!/usr/bin/env bash
# batch_antenna_fix.sh — clear nangate45 METAL*_ANTENNA DRC fails in bulk by
# running the diode-forced antenna repair (fix_signoff.sh → antenna_diode_repair).
#
# Why: with the antenna model installed (tools/install_nangate45_antenna.sh) OpenROAD
# can finally see + repair nangate45 antennas, but only physical DIODE insertion is
# credited by the FreePDK45 signoff deck (jumpers are not).  fix_signoff.sh applies the
# antenna_diode_repair strategy (SKIP_ANTENNA_REPAIR=1 + MAX_REPAIR_ANTENNAS_ITER_DRT),
# re-routes, and re-checks against the unchanged 300:1 deck.  See
# references/signoff-fixing.md "nangate45 antenna repair".
#
# Pre-flight: idempotently ensures the antenna model is installed.
# Work-list (auto): nangate45 designs whose reports/drc.json is a PURE-antenna fail
# (every category ends in _ANTENNA), ordered ascending by instance count, capped by
# --max-inst.  Pass explicit design basenames to override discovery.
#
# Usage:
#   tools/batch_antenna_fix.sh [--max-inst N] [--jobs J] [--timeout SECS]
#                              [--max-iters K] [--resume] [--platform P]
#                              [--dry-run] [design ...]
#
#   --max-inst N   skip designs with instance_count > N         (default 60000)
#   --jobs J       max concurrent fix_signoff.sh jobs           (default 4)
#   --timeout S    per-design ORFS_TIMEOUT in seconds           (default 1200)
#   --max-iters K  fix_signoff.sh --max-iters                   (default 2)
#   --resume       resume re-route from the route stage (faster); else full re-run
#   --platform P   ORFS platform                                (default nangate45)
#   --dry-run      print the work-list and exit
#   [design ...]   explicit design-case basenames; else auto-discover pure-antenna fails
#
# Emits one JSON line per design to design_cases/_batch/antenna_fix_<UTC-stamp>.jsonl
# and a human summary to stdout at the end.
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
SKILL_DIR="$REPO_ROOT/r2g-skills/signoff-loop"
DC="$REPO_ROOT/design_cases"

MAX_INST=60000
JOBS=4
TIMEOUT=1200
MAX_ITERS=2
RESUME=""
PLATFORM=nangate45
DRY_RUN=0
DESIGNS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --max-inst) MAX_INST="$2"; shift 2;;
    --jobs)     JOBS="$2"; shift 2;;
    --timeout)  TIMEOUT="$2"; shift 2;;
    --max-iters) MAX_ITERS="$2"; shift 2;;
    --resume)   RESUME="--resume"; shift;;
    --platform) PLATFORM="$2"; shift 2;;
    --dry-run)  DRY_RUN=1; shift;;
    -h|--help)  sed -n '2,38p' "$0"; exit 0;;
    --*)        echo "unknown flag: $1" >&2; exit 2;;
    *)          DESIGNS+=("$1"); shift;;
  esac
done

STAMP="$(date +%Y%m%dT%H%M%S%z)"
OUT_DIR="$DC/_batch"
mkdir -p "$OUT_DIR"
JSONL="$OUT_DIR/antenna_fix_${STAMP}.jsonl"

# Pre-flight: ensure the antenna model is installed (idempotent) for nangate45.
if [[ "$PLATFORM" == "nangate45" ]]; then
  if ! bash "$SCRIPT_DIR/install_nangate45_antenna.sh" --status 2>/dev/null | grep -q "ANTENNAAREARATIO: 10/10"; then
    echo "Installing nangate45 antenna model (one-time)…"
    bash "$SCRIPT_DIR/install_nangate45_antenna.sh"
  fi
fi

# Build work-list: (instance_count, design) for pure-antenna nangate45 fails <= MAX_INST.
mapfile -t WORK < <(python3 - "$DC" "$MAX_INST" "$PLATFORM" "${DESIGNS[@]+"${DESIGNS[@]}"}" <<'PY'
import json, sys, os, glob
dc, max_inst, platform = sys.argv[1], int(sys.argv[2]), sys.argv[3]
explicit = sys.argv[4:]
def load(proj, name):
    try: return json.load(open(os.path.join(dc, proj, "reports", name + ".json")))
    except Exception: return {}
def inst(proj):
    return load(proj, "ppa").get("geometry", {}).get("instance_count") or 0
def design_platform(proj):
    cfg = os.path.join(dc, proj, "constraints", "config.mk")
    try:
        for ln in open(cfg):
            if "PLATFORM" in ln and "=" in ln:
                return ln.split("=", 1)[1].strip()
    except Exception: pass
    return None
def pure_antenna_fail(proj):
    d = load(proj, "drc")
    if d.get("status") not in ("fail", "failed"): return False
    cats = d.get("categories") or {}
    return bool(cats) and all(k.upper().endswith("_ANTENNA") for k in cats)
if explicit:
    cands = explicit
else:
    cands = sorted({d.split("/")[-3] for d in glob.glob(os.path.join(dc, "*", "reports", "drc.json"))})
rows = []
for proj in cands:
    if not explicit:
        if design_platform(proj) != platform: continue
        if not pure_antenna_fail(proj): continue
    n = inst(proj)
    if n and n > max_inst: continue
    rows.append((n, proj))
rows.sort()
for n, proj in rows:
    print(f"{n}\t{proj}")
PY
)

echo "Antenna-fix batch — $STAMP"
echo "  platform=$PLATFORM  max_inst=$MAX_INST  jobs=$JOBS  per-design ORFS_TIMEOUT=${TIMEOUT}s  max-iters=$MAX_ITERS  resume=${RESUME:-no}"
echo "  work-list: ${#WORK[@]} design(s)"
if [[ ${#WORK[@]} -eq 0 ]]; then echo "  (nothing to do)"; exit 0; fi
printf '    %s\n' "${WORK[@]}" | sed 's/\t/  /'
if [[ "$DRY_RUN" == "1" ]]; then echo "(dry-run; exiting)"; exit 0; fi

run_one() {
  local inst="$1" proj="$2"
  local pdir="$DC/$proj"
  local log="$OUT_DIR/antenna_${proj}.log"
  local start end wall before after status
  start=$(date +%s)
  ORFS_TIMEOUT="$TIMEOUT" \
    bash "$SKILL_DIR/scripts/flow/fix_signoff.sh" "$pdir" "$PLATFORM" \
      --check drc --max-iters "$MAX_ITERS" $RESUME >"$log" 2>&1
  end=$(date +%s); wall=$((end - start))
  read -r status after before < <(python3 - "$pdir/reports/drc.json" "$pdir/reports/fix_log.jsonl" <<'PY'
import json, sys
d = {}
try: d = json.load(open(sys.argv[1]))
except Exception: pass
status = d.get("status", "error"); after = d.get("total_violations")
before = None
try:
    rows = [json.loads(l) for l in open(sys.argv[2]) if l.strip()]
    if rows: before = rows[0].get("before")
except Exception: pass
print(status, after if after is not None else "null", before if before is not None else "null")
PY
)
  printf '{"design":"%s","inst":%s,"status":"%s","before":%s,"after":%s,"wall_s":%s}\n' \
    "$proj" "${inst:-0}" "$status" "$before" "$after" "$wall" >> "$JSONL"
  echo "  [$status] $proj (inst=$inst $before->$after ${wall}s)"
}

running=0
for line in "${WORK[@]}"; do
  inst="${line%%$'\t'*}"; proj="${line#*$'\t'}"
  run_one "$inst" "$proj" &
  running=$((running + 1))
  if [[ $running -ge $JOBS ]]; then wait -n 2>/dev/null || wait; running=$((running - 1)); fi
done
wait

echo ""
echo "=== Antenna-fix batch summary ($JSONL) ==="
python3 - "$JSONL" <<'PY'
import json, sys, collections
c = collections.Counter(); cleaned = 0; total = 0
for ln in open(sys.argv[1]):
    try: j = json.loads(ln)
    except Exception: continue
    total += 1; c[j["status"]] += 1
    if j["status"] == "clean": cleaned += 1
for st, n in c.most_common():
    print(f"  {st:14s} {n}")
print(f"  {'TOTAL':14s} {total}   (newly clean: {cleaned})")
PY
