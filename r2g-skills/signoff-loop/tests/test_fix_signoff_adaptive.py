"""fix_signoff.sh has an adaptive budget (D12): past a base of 3 iters it stops
after 2 consecutive non-improving iterations; the hard cap is 8 (not 3)."""
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


def _common(tmp_path, drc_initial):
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n")
    (proj / "reports" / "drc.json").write_text(json.dumps(
        {"status": "fail", "total_violations": drc_initial,
         "categories": {"M2_ANTENNA": {"count": drc_initial}}}))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _stub(bindir / "noop.sh", 'exit 0')
    return proj, bindir


def _applied_rows(proj):
    lines = [json.loads(l) for l in
             (proj / "reports" / "fix_log.jsonl").read_text().splitlines() if l.strip()]
    return [r for r in lines if r["strategy"] not in ("none",) and r.get("iter")]


def test_improving_run_exceeds_base_and_clears(tmp_path):
    proj, bindir = _common(tmp_path, 100)
    # diagnose always offers a strategy (never STOP); --apply succeeds.
    _stub(bindir / "diagnose.py",
          'if [[ "$*" == *"--next"* ]]; then echo -e "diode\\troute\\tdrc";\n'
          'elif [[ "$*" == *"--apply"* ]]; then echo "{}"; fi')
    # extract: steadily decreasing count 80,60,40,20,0 on successive calls.
    ctr = tmp_path / "ctr"
    _stub(bindir / "extract.py",
          'python3 - "$@" <<PY\n'
          'import json,sys,os\n'
          'ctr="%s"\n'
          'n=int(open(ctr).read()) if os.path.exists(ctr) else 100\n'
          'n=max(0,n-20)\n'
          'open(ctr,"w").write(str(n))\n'
          'open(sys.argv[2],"w").write(json.dumps(\n'
          '  {"status":"clean" if n==0 else "fail","total_violations":n,\n'
          '   "categories":{} if n==0 else {"M2_ANTENNA":{"count":n}}}))\n'
          'PY' % str(ctr))

    env = dict(os.environ,
               R2G_DIAGNOSE=str(bindir / "diagnose.py"),
               R2G_RUN_ORFS=str(bindir / "noop.sh"),
               R2G_RUN_DRC=str(bindir / "noop.sh"),
               R2G_EXTRACT_DRC=str(bindir / "extract.py"))
    # NOTE: no --max-iters; relies on new default cap 8.
    subprocess.run(["bash", str(FIX_SIGNOFF), str(proj), "nangate45",
                    "--check", "drc"], env=env, check=False)

    rows = _applied_rows(proj)
    assert len(rows) > 3, f"expected >3 iterations, got {len(rows)}"
    assert rows[-1]["verdict"] == "cleared"
    assert rows[-1]["after"] == "0"


def test_stuck_run_stops_at_exactly_three(tmp_path):
    proj, bindir = _common(tmp_path, 50)
    # diagnose always offers a strategy (never STOP); --apply succeeds.
    _stub(bindir / "diagnose.py",
          'if [[ "$*" == *"--next"* ]]; then echo -e "diode\\troute\\tdrc";\n'
          'elif [[ "$*" == *"--apply"* ]]; then echo "{}"; fi')
    # extract: count never changes (stays 50).
    _stub(bindir / "extract.py",
          'python3 - "$@" <<\'PY\'\nimport json,sys\n'
          'open(sys.argv[2],"w").write(json.dumps(\n'
          '  {"status":"fail","total_violations":50,\n'
          '   "categories":{"M2_ANTENNA":{"count":50}}}))\nPY')

    env = dict(os.environ,
               R2G_DIAGNOSE=str(bindir / "diagnose.py"),
               R2G_RUN_ORFS=str(bindir / "noop.sh"),
               R2G_RUN_DRC=str(bindir / "noop.sh"),
               R2G_EXTRACT_DRC=str(bindir / "extract.py"))
    # NOTE: no --max-iters; relies on new default cap 8.
    subprocess.run(["bash", str(FIX_SIGNOFF), str(proj), "nangate45",
                    "--check", "drc"], env=env, check=False)

    rows = _applied_rows(proj)
    assert len(rows) == 3, f"expected exactly 3 iterations, got {len(rows)}"
    assert all(r["verdict"] == "no_improvement" for r in rows)
