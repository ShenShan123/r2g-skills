"""fix_signoff.sh --check route: the backend-abort (route congestion) fix loop.

A route abort never reaches signoff DRC, so it flows through --check route:
diagnose route_relief -> apply (lower CORE_UTILIZATION) -> rerun from floorplan ->
extract_route -> log. The CRUX is that the logged symptom keys under
check='orfs_stage', class='route' (so the run's and the fix's symptom_ids agree
and the A/B loop can match them).
"""
from __future__ import annotations
import json
import os
import stat
import subprocess
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
FIX_SIGNOFF = SKILL / "scripts" / "flow" / "fix_signoff.sh"


def _stub(path: Path, body: str):
    path.write_text("#!/usr/bin/env bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_route_fix_logs_orfs_stage_symptom_and_clears(tmp_path):
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = sky130hd\n"
        "export CORE_UTILIZATION = 25\n")
    # route.json: the pre-fix route abort (timeout with residual DRT violations).
    (proj / "reports" / "route.json").write_text(json.dumps(
        {"status": "timeout", "total_violations": 5247}))

    bindir = tmp_path / "bin"
    bindir.mkdir()
    # diagnose --next yields route_relief once, then STOP; --apply echoes config_edits.
    _stub(bindir / "diagnose.py",
          'if [[ "$*" == *"--next"* ]]; then\n'
          '  if [[ -f /tmp/_rr_$$ ]]; then echo -e "STOP\\tclean\\tdone"; \n'
          '  else echo -e "route_relief\\tfloorplan\\troute"; fi\n'
          'elif [[ "$*" == *"--apply"* ]]; then touch /tmp/_rr_$$;\n'
          '  echo "{\\"applied\\":\\"route_relief\\",\\"config_edits\\":{\\"CORE_UTILIZATION\\":\\"17\\"}}"; fi')
    # run_orfs: no-op (the real rerun would re-route at lower util).
    _stub(bindir / "noop.sh", 'exit 0')
    # extract_route: post-rerun the route is CLEAN (0 residual).
    _stub(bindir / "extract_route.py",
          'python3 - "$@" <<\'PY\'\nimport json,sys\n'
          'open(sys.argv[2],"w").write(json.dumps({"status":"clean","total_violations":0}))\nPY')

    env = dict(os.environ,
               R2G_DIAGNOSE=str(bindir / "diagnose.py"),
               R2G_RUN_ORFS=str(bindir / "noop.sh"),
               R2G_EXTRACT_ROUTE=str(bindir / "extract_route.py"))
    r = subprocess.run(["bash", str(FIX_SIGNOFF), str(proj), "sky130hd",
                        "--check", "route", "--max-iters", "2"], env=env, check=False)
    # final state clean -> exit 0
    assert r.returncode == 0

    lines = [json.loads(l) for l in
             (proj / "reports" / "fix_log.jsonl").read_text().splitlines() if l.strip()]
    applied = [r for r in lines if r["strategy"] == "route_relief"]
    assert applied, "expected an applied route_relief iteration row"
    row = applied[0]
    # CRUX: the route fix logs under the canonical backend-abort symptom key.
    assert row["check"] == "orfs_stage"
    assert row["violation_class"] == "route"
    assert row["from_stage"] == "floorplan"
    assert row["before"] == "5247"
    assert row["after"] == "0"
    assert row["verdict"] == "cleared"
    assert json.loads(row["config_delta"]) == {"CORE_UTILIZATION": "17"}
