#!/usr/bin/env bash
set -uo pipefail

# usage: run_strict_signoff.sh <project-dir> [platform] [flow-variant]
#
# Execute and normalize the three production signoff legs over one frozen ORFS
# backend generation, then run def-graph's exact strict gate.  Every checker is
# allowed to finish writing its evidence even when another leg fails; the final
# exit is non-zero unless DRC, LVS, RCX, and the strict provenance gate all pass.

PROJECT_DIR="${1:-}"
PLATFORM="${2:-sky130hd}"
FLOW_VARIANT="${3:-}"
if [[ -z "$PROJECT_DIR" || ! -d "$PROJECT_DIR" ]]; then
  echo "usage: run_strict_signoff.sh <project-dir> [platform] [flow-variant]" >&2
  exit 2
fi
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
[[ -n "$FLOW_VARIANT" ]] || FLOW_VARIANT="$(basename "$PROJECT_DIR")"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
EXTRACT_DIR="$SKILL_DIR/scripts/extract"
GRAPH_FLOW_DIR="$(cd "$SKILL_DIR/../def-graph/scripts/flow" && pwd)"
REPORTS="$PROJECT_DIR/reports"
mkdir -p "$REPORTS"
export R2G_STRICT_SIGNOFF=1

# shellcheck source=/dev/null
source "$SCRIPT_DIR/_env.sh"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/_backend_run.sh"
RUN_DIR="$(r2g_pick_backend_run "$PROJECT_DIR" || true)"
if [[ -z "$RUN_DIR" || ! -d "$RUN_DIR" ]]; then
  echo "ERROR: no completed backend run is available for strict signoff" >&2
  exit 2
fi
RUN_TAG="$(basename "$RUN_DIR")"
FINAL_DEF="$RUN_DIR/final/6_final.def"
[[ -f "$FINAL_DEF" ]] || FINAL_DEF="$RUN_DIR/results/6_final.def"
if [[ ! -f "$FINAL_DEF" ]]; then
  echo "ERROR: selected backend $RUN_TAG has no frozen final DEF" >&2
  exit 2
fi

status=0
run_leg() {
  local name="$1"; shift
  echo "[strict-signoff] $name"
  "$@"
  local rc=$?
  if [[ "$rc" -ne 0 ]]; then
    echo "[strict-signoff] $name failed (rc=$rc)" >&2
    status=1
  fi
  return 0
}

run_leg drc bash "$SCRIPT_DIR/run_drc.sh" "$PROJECT_DIR" "$PLATFORM" "$FLOW_VARIANT"
run_leg drc-normalize python3 "$EXTRACT_DIR/extract_drc.py" \
  "$PROJECT_DIR" "$REPORTS/drc.json" --run-dir "$RUN_DIR"

case "$PLATFORM" in
  sky130hd|sky130hs)
    run_leg lvs bash "$SCRIPT_DIR/run_netgen_lvs.sh" \
      "$PROJECT_DIR" "$PLATFORM" "$FLOW_VARIANT"
    ;;
  *)
    run_leg lvs bash "$SCRIPT_DIR/run_lvs.sh" \
      "$PROJECT_DIR" "$PLATFORM" "$FLOW_VARIANT"
    ;;
esac
run_leg lvs-normalize python3 "$EXTRACT_DIR/extract_lvs.py" \
  "$PROJECT_DIR" "$REPORTS/lvs.json" --run-dir "$RUN_DIR"

run_leg rcx bash "$SCRIPT_DIR/run_rcx.sh" "$PROJECT_DIR" "$PLATFORM" "$FLOW_VARIANT"
run_leg rcx-normalize python3 "$EXTRACT_DIR/extract_rcx.py" \
  "$PROJECT_DIR" "$REPORTS/rcx.json"
run_leg route-normalize python3 "$EXTRACT_DIR/extract_route.py" \
  "$PROJECT_DIR" "$REPORTS/route.json"
run_leg ppa-normalize python3 "$EXTRACT_DIR/extract_ppa.py" \
  "$PROJECT_DIR" "$REPORTS/ppa.json"

run_leg strict-gate python3 "$GRAPH_FLOW_DIR/signoff_gate.py" "$PROJECT_DIR" \
  --run-dir "$RUN_DIR" --def "$FINAL_DEF" --mode strict

python3 - "$PROJECT_DIR" "$RUN_TAG" "$PLATFORM" "$status" <<'PYEOF'
import datetime, hashlib, json, pathlib, sys
project, run_tag, platform, status = sys.argv[1:]
root = pathlib.Path(project)
reports = root / "reports"
def load(name):
    try:
        return json.loads((reports / name).read_text())
    except Exception:
        return {"status": "missing"}
doc = {
    "receipt_version": "strict-signoff-v0.1",
    "created_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    "platform": platform,
    "run_tag": run_tag,
    "status": "pass" if int(status) == 0 else "fail",
    "checks": {name[:-5]: load(name) for name in
               ("drc.json", "lvs.json", "rcx.json", "route.json", "signoff_gate.json")},
}
path = reports / "strict_signoff.json"
tmp = path.with_suffix(".json.tmp")
tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
tmp.replace(path)
PYEOF

exit "$status"
