#!/usr/bin/env python3
"""Extract the detailed-route stage outcome -> reports/route.json.

A *route-stage* failure (orfs_status='fail' at fail_stage='route') is the
backend-abort analogue of a signoff DRC/LVS violation: the design reached
detailed routing (DRT) but did not finish a clean route — because of congestion,
an irreducible DRT residual, or a wall-clock timeout that killed DRT mid-grind.

Until now the closed learning loop could only see *post-route* signoff symptoms
(drc/lvs/timing). A route abort never produces a drc.json/lvs.json, so it was
invisible to fix_signoff.sh and to ab_runner — the A/B loop was structurally
blind to it (see references/failure-patterns.md "Routing Congestion" and the
2026-06-17 route-relief note). This extractor emits the SAME {status,
total_violations} shape the fix loop already consumes, so `fix_signoff.sh --check
route` and the learner can treat a route abort uniformly with a DRC violation.

status:
  'clean'   — route stage exit 0 AND 0 residual DRT DRC markers
  'fail'    — route stage exit 0 but residual DRT DRC markers remain
  'timeout' — route stage was killed (exit 124/137 = wall-clock timeout / OOM)
  'unknown' — no route stage in the stage log yet (route never reached)

total_violations is the residual DRT DRC marker count (0 on a clean route), so
fix_signoff.sh _count works unchanged; a timeout with no parseable residual
reports total_violations=None.
"""
from __future__ import annotations

import glob
import json
import re
import sys
from pathlib import Path

# A route-stage exit code that means "killed", not "found N violations": GNU
# timeout uses 124 (TERM) / 137 (128+SIGKILL after --kill-after) — see
# run_orfs.sh setsid timeout. These are NOT a clean/quantified failure.
_TIMEOUT_CODES = {124, 137}


def _latest_backend(project_root: Path) -> Path | None:
    runs = sorted(glob.glob(str(project_root / "backend" / "RUN_*")))
    return Path(runs[-1]) if runs else None


def _route_stage_status(run_dir: Path) -> int | None:
    """Exit code of the 'route' stage from stage_log.jsonl (None if absent)."""
    sl = run_dir / "stage_log.jsonl"
    if not sl.exists():
        return None
    status = None
    for line in sl.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("stage") == "route":
            status = row.get("status")     # last route entry wins
    return status


def _residual_from_report(run_dir: Path) -> int | None:
    """Residual DRT DRC marker count from reports_orfs/5_route_drc.rpt.

    A clean route writes an EMPTY 5_route_drc.rpt (0 lines). Each violation is a
    'bbox' / 'violation type' stanza; count the 'violation type' markers, falling
    back to non-empty line count. Returns None if no final report exists (killed
    before the report was written)."""
    rpt = run_dir / "reports_orfs" / "5_route_drc.rpt"
    if not rpt.exists():
        return None
    text = rpt.read_text(encoding="utf-8", errors="ignore")
    if not text.strip():
        return 0
    markers = re.findall(r"violation type", text, flags=re.IGNORECASE)
    if markers:
        return len(markers)
    # Unknown structured format with content but no recognized marker: treat the
    # non-empty line count as a coarse, non-zero residual (never silently 0).
    return sum(1 for ln in text.splitlines() if ln.strip())


def _residual_from_log(run_dir: Path) -> int | None:
    """Last DRT 'with N violations' from the detailed-route log (the grind
    snapshot), used when a killed run left no final 5_route_drc.rpt."""
    logs = glob.glob(str(run_dir / "logs" / "**" / "5_2_route.log"), recursive=True)
    if not logs:
        return None
    text = Path(sorted(logs)[-1]).read_text(encoding="utf-8", errors="ignore")
    matches = re.findall(r"with (\d+) violations", text)
    return int(matches[-1]) if matches else None


def extract(project_root: Path) -> dict:
    run_dir = _latest_backend(project_root)
    if run_dir is None:
        return {"status": "unknown", "total_violations": None, "stage": "route",
                "reason": "no backend run dir"}
    route_status = _route_stage_status(run_dir)
    residual = _residual_from_report(run_dir)
    if residual is None:
        residual = _residual_from_log(run_dir)

    completed = route_status == 0
    if route_status in _TIMEOUT_CODES:
        status = "timeout"
    elif route_status is None:
        # No 'route' stage logged: the abort was earlier (synth/floorplan/place/
        # cts) — not a route symptom. Stay 'unknown' so the route fixer no-ops.
        status = "unknown"
    elif completed and (residual == 0):
        status = "clean"
    elif completed:
        status = "fail"          # routed, but DRT residual DRC remains
    else:
        status = "fail"          # non-zero, non-timeout route exit

    return {
        "status": status,
        "total_violations": residual,
        "completed": completed,
        "route_stage_status": route_status,
        "stage": "route",
        "backend_run": run_dir.name,
    }


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: extract_route.py <project-root> <output.json>", file=sys.stderr)
        return 1
    project_root = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    result = extract(project_root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
